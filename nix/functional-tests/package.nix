# One program that runs Nix's functional test suite against a daemon.
#
# **Every tool it needs comes from this closure, and every path it needs is
# written in at build time.** A person runs three commands and gets a result:
#
#     nix build --file . nixFunctionalTests.nix_2_34 --out-link result
#     ./result/bin/nanopynix-nixft-nix_2_34 all
#
# The name of the program holds the version, so two versions can be on one
# PATH and a person can see which one a command ran.
#
# On a Darwin host the tests need a Linux machine, because they build
# derivations. The store is shared with that machine, so the same store path
# runs there:
#
#     vzrun /nix/store/...-nanopynix-nixft-nix_2_34/bin/nanopynix-nixft-nix_2_34 all
#
# `ci/steps.nix` explains why a body belongs in a package rather than in a
# shell command that a person types. The same three reasons apply here:
# shellcheck reads what `writeShellApplication` builds, `runtimeInputs` names
# each tool so the machine's own installation does not decide, and one name
# replaces a line of store paths.
#
# Read README.md beside this file for the test mode, and issue #172 for the
# work.
{
  lib,
  writeShellApplication,
  # The Nix whose daemon serves the control run. Its `src` holds the test
  # scripts, so the client, the scripts and the control daemon are one version.
  nix,
  # The daemon proxy under test.
  pynixd,
  # The attribute name of the Nix version, such as "nix_2_34". It names the
  # work directory, so two versions do not share one.
  version,
  meson,
  ninja,
  jq,
  git,
  busybox,
  coreutils,
  findutils,
  gnugrep,
  gnused,
  gawk,
  diffutils,
  procps,
  python3,
}:
let
  scripts = lib.fileset.toSource {
    root = ./.;
    fileset = lib.fileset.unions [
      ./setup.sh
      ./run.sh
      ./make-shim.sh
      ./make-record-shim.sh
      ./compare.py
    ];
  };
  # The interpreter of the pynixd application, which has
  # `nix_daemon_protocol` in it. The recorder of the `streams` mode is a
  # module of that package, and it has no entry point of its own.
  wirelogPython = "${pynixd.venv}/bin/python";
in
writeShellApplication {
  name = "nanopynix-nixft-${version}";

  # **`busybox` is last on purpose.** meson looks for `ls` to state where
  # coreutils is, and busybox carries an `ls`. Before coreutils it wins that
  # search, and meson then names the wrong directory. Nix's own
  # `tests/functional/meson.build` wants busybox for the sandbox tests, so it
  # cannot be dropped.
  runtimeInputs = [
    coreutils
    findutils
    gnugrep
    gnused
    gawk
    diffutils
    procps
    meson
    ninja
    jq
    git
    python3
    busybox
  ];

  text = ''
    NIXFT_VERSION=${lib.escapeShellArg version}
    NIX_PKG=${lib.escapeShellArg nix}
    NIX_SRC=${lib.escapeShellArg nix.src}
    PYNIXD_BIN=${lib.escapeShellArg (lib.getExe' pynixd "pynixd")}
    WIRELOG_PYTHON=${lib.escapeShellArg wirelogPython}
    SCRIPTS=${lib.escapeShellArg scripts}

    # The client must be the Nix that owns the test scripts, so it goes in
    # front of everything. `runtimeInputs` above put the rest there already.
    PATH=$NIX_PKG/bin:$PATH
    export PATH

    # One work directory for each version, so two versions never share a build
    # directory or a store. `/tmp` and not `/scratch`: Nix refuses a store
    # under a symbolic link, and `setup.sh` resolves this path for that reason.
    WORK=''${NIXFT_WORK:-/tmp/nanopynix-nixft/$NIXFT_VERSION}
    export WORK

    CONTROL_LOG=$WORK/control.testlog.json
    PYNIXD_LOG=$WORK/pynixd.testlog.json

    # The recordings of the `streams` mode. They sit outside `$WORK/tmp`,
    # because `run.sh` wipes that directory when the next run starts, and the
    # comparison needs the control recordings after the candidate run.
    STREAMS=$WORK/streams
    STREAMS_CONTROL=$STREAMS/control
    STREAMS_PYNIXD=$STREAMS/pynixd

    usage() {
      cat >&2 <<USAGE
    nanopynix-nixft-$NIXFT_VERSION -- Nix's functional tests against a daemon

      setup              prepare the suite. Run this first. It wipes \$WORK.
      control [ARGS]     run the suite against a plain nix daemon
      pynixd  [ARGS]     run the suite against pynixd
      compare            state which tests pynixd alone fails
      status             the totals of the run in \$WORK, while it goes on
      detail NAME [WHICH]  the whole output of one test. WHICH is
                         \`control\` (default) or \`pynixd\`.
      all     [ARGS]     setup, control, pynixd, compare

    The stream mode. It reads the wire and not the verdict of each script:

      record-control [ARGS]  run against a plain nix daemon, and record
      record-pynixd  [ARGS]  run against pynixd, and record
      diff-streams           state which tests differ on the wire
      streams [ARGS]         setup, record-control, record-pynixd, diff-streams

    Each ARGS goes to \`meson test\`, so \`control --suite ca\` runs one suite
    and \`control gc fetchurl\` runs two tests.

    Environment:
      NIXFT_WORK   the work directory. Default /tmp/nanopynix-nixft/$NIXFT_VERSION
      JOBS         tests at a time. Default 1.

    Paths of this build:
      nix       $NIX_PKG
      tests     $NIX_SRC/tests/functional
      pynixd    $PYNIXD_BIN
    USAGE
    }

    do_setup() {
      NIX_SRC=$NIX_SRC WORK=$WORK bash "$SCRIPTS/setup.sh"
    }

    # The pynixd package for the `NIX_DAEMON_PACKAGE` place. Both the plain
    # mode and the stream mode need it, so it is one function.
    make_pynixd_shim() {
      WORK=$WORK PYNIXD_BIN=$PYNIXD_BIN REAL_NIX=$NIX_PKG/bin/nix \
        bash "$SCRIPTS/make-shim.sh"
    }

    # **A passing test proves nothing until pynixd was in the path.** The shim
    # sends `nix daemon` to pynixd and everything else to the real Nix, and
    # three defects in that one decision made the whole suite go green while
    # pynixd served no request at all. pynixd writes its config beside each
    # test store, so the file is the proof.
    check_pynixd_served() {
      local served
      served=$(find "$WORK/tmp" -name pynixd-test-config.json 2>/dev/null | wc -l)
      echo "pynixd served $served test store(s)"
      if [ "$served" -eq 0 ]; then
        echo "ERROR: pynixd served no test at all. The result above is meaningless." >&2
        return 1
      fi
    }

    do_control() {
      echo "=== control: a plain nix daemon, $NIXFT_VERSION ==="
      WORK=$WORK NIX_DAEMON_PACKAGE=$NIX_PKG SAVE_LOG=$CONTROL_LOG \
        bash "$SCRIPTS/run.sh" "$@"
    }

    do_pynixd() {
      echo "=== candidate: pynixd, $NIXFT_VERSION ==="
      local shim
      shim=$(make_pynixd_shim)
      WORK=$WORK NIX_DAEMON_PACKAGE=$shim SAVE_LOG=$PYNIXD_LOG \
        bash "$SCRIPTS/run.sh" "$@"
      check_pynixd_served
    }

    # meson writes `testlog.json` while the run goes on, so this reads a run
    # that has not finished. The last line can be half written, and `jq` stops
    # at the first line it cannot read, so `sed $d` drops that line.
    do_status() {
      local live=$WORK/build/meson-logs/testlog.json
      if [ ! -e "$live" ]; then
        echo "status: no run has started in $WORK"
        return 1
      fi
      echo "=== $live ==="
      sed '$d' "$live" | jq -r '.result' | sort | uniq -c
      echo "tests recorded: $(wc -l < "$live")"
      if pgrep -f 'meson test' >/dev/null; then
        echo "a run is going on"
      else
        echo "no run is going on"
      fi
    }

    # The output of one test, from a saved log. meson keeps `stderr` in
    # `testlog.json`, and the test scripts run under `bash -x`, so this is the
    # whole trace of the script. It is the only record after the run, because
    # `run.sh` wipes the store of each test when the next run starts.
    #
    # `$1` is a test name, or a part of one: `gc`, `ca:recursive`,
    # `nix-channel`. `$2` names which log to read: `control` (the default) or
    # `pynixd`.
    do_detail() {
      local pattern=''${1:-}
      local which=''${2:-control}
      if [ -z "$pattern" ]; then
        echo "detail: name a test, for example \`detail nix-channel\`" >&2
        return 2
      fi
      # An `if`, and not `[ ... ] && log=...`. Under `set -e` the second form
      # ends the program whenever the test is false, which is the usual case.
      local log=$CONTROL_LOG
      if [ "$which" = pynixd ]; then
        log=$PYNIXD_LOG
      fi
      if [ ! -e "$log" ]; then
        echo "detail: no $which log at $log" >&2
        return 2
      fi
      jq -r --arg pattern "$pattern" \
        'select(.name | test($pattern)) | "=== \(.name) [\(.result)] ===\n\(.stderr)"' \
        "$log"
    }

    # ── The stream mode ─────────────────────────────────────────────────
    #
    # `compare` above reads the verdict of each script, and a script says
    # "pass" or "fail" for reasons that are not the wire: it reads a message,
    # it counts the store paths on the disk, it wants a path to be dead. The
    # contract of pynixd is narrower than that. A client must not be able to
    # tell pynixd from `nix-daemon`, and that is a statement about the bytes.
    #
    # So this mode runs the same workload twice with a recorder in the middle,
    # and compares the two streams of operations. A test whose script fails in
    # both runs still gives a usable answer here. Issue #175.

    wirelog() {
      "$WIRELOG_PYTHON" -m nix_daemon_protocol.wirelog "$@"
    }

    # Build the recording shim over one inner package, and run the suite.
    record_run() {
      local inner=$1 out_root=$2 shim_dir=$3 save_log=$4
      shift 4
      rm -rf "''${out_root:?}"
      mkdir -p "$out_root"
      local shim
      shim=$(WORK=$WORK INNER=$inner OUT_ROOT=$out_root SHIM_DIR=$shim_dir \
        PYTHON=$WIRELOG_PYTHON REAL_NIX=$NIX_PKG/bin/nix \
        bash "$SCRIPTS/make-record-shim.sh")
      WORK=$WORK NIX_DAEMON_PACKAGE=$shim SAVE_LOG=$save_log \
        bash "$SCRIPTS/run.sh" "$@"
    }

    do_record_control() {
      echo "=== control, recorded: a plain nix daemon, $NIXFT_VERSION ==="
      record_run "$NIX_PKG" "$STREAMS_CONTROL" "$WORK/record-shim-control" \
        "$CONTROL_LOG" "$@"
    }

    do_record_pynixd() {
      echo "=== candidate, recorded: pynixd, $NIXFT_VERSION ==="
      local inner
      inner=$(make_pynixd_shim)
      record_run "$inner" "$STREAMS_PYNIXD" "$WORK/record-shim-pynixd" \
        "$PYNIXD_LOG" "$@"
      check_pynixd_served
    }

    # One verdict for each test, from the recordings alone.
    do_diff_streams() {
      if [ ! -d "$STREAMS_CONTROL" ] || [ ! -d "$STREAMS_PYNIXD" ]; then
        echo "diff-streams: run \`record-control\` and \`record-pynixd\` first" >&2
        return 2
      fi

      local report=$STREAMS/report.txt
      local one=$STREAMS/one.txt
      : > "$report"

      local same=0 different=0 missing=0
      local dir key
      # `%h` of each `daemon-N` directory is the directory of the test, and
      # `sort -u` then names each test once however many daemons it started.
      while IFS= read -r dir; do
        key=''${dir#"$STREAMS_CONTROL/"}
        if [ ! -d "$STREAMS_PYNIXD/$key" ]; then
          echo "MISSING   $key" | tee -a "$report"
          missing=$((missing + 1))
          continue
        fi
        if wirelog compare "$STREAMS_CONTROL/$key" "$STREAMS_PYNIXD/$key" > "$one" 2>&1; then
          same=$((same + 1))
        else
          different=$((different + 1))
          echo "DIFFERENT $key"
          { echo "=== $key ==="; cat "$one"; echo; } >> "$report"
        fi
      done < <(find "$STREAMS_CONTROL" -mindepth 2 -type d -name 'daemon-*' -printf '%h\n' | sort -u)

      rm -f "$one"
      echo "=== STREAM SUMMARY ==="
      echo "same:      $same"
      echo "different: $different"
      echo "missing:   $missing"
      echo "the differences are at $report"
      if [ "$different" -ne 0 ] || [ "$missing" -ne 0 ]; then
        return 1
      fi
    }

    do_compare() {
      if [ ! -e "$CONTROL_LOG" ] || [ ! -e "$PYNIXD_LOG" ]; then
        echo "compare: run \`control\` and \`pynixd\` first" >&2
        return 2
      fi
      python3 "$SCRIPTS/compare.py" "$CONTROL_LOG" "$PYNIXD_LOG"
    }

    command=''${1:-}
    # `[ $# -gt 0 ] && shift` ends the program under `set -e` when there is no
    # argument, before `usage` can say what the arguments are.
    if [ $# -gt 0 ]; then
      shift
    fi

    case "$command" in
      setup)   do_setup ;;
      control) do_control "$@" ;;
      pynixd)  do_pynixd "$@" ;;
      compare) do_compare ;;
      status)  do_status ;;
      detail)  do_detail "$@" ;;
      all)
        do_setup
        do_control "$@"
        do_pynixd "$@"
        do_compare
        ;;
      record-control) do_record_control "$@" ;;
      record-pynixd)  do_record_pynixd "$@" ;;
      diff-streams)   do_diff_streams ;;
      streams)
        do_setup
        do_record_control "$@"
        do_record_pynixd "$@"
        do_diff_streams
        ;;
      ""|-h|--help|help) usage; exit 1 ;;
      *) echo "nanopynix-nixft: no command named '$command'" >&2; usage; exit 2 ;;
    esac
  '';

  meta = {
    description = "Nix's functional test suite, run against a daemon (${version})";
    platforms = lib.platforms.linux;
  };
}
