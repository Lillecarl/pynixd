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
      ./compare.py
    ];
  };
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

    do_control() {
      echo "=== control: a plain nix daemon, $NIXFT_VERSION ==="
      WORK=$WORK NIX_DAEMON_PACKAGE=$NIX_PKG SAVE_LOG=$CONTROL_LOG \
        bash "$SCRIPTS/run.sh" "$@"
    }

    do_pynixd() {
      echo "=== candidate: pynixd, $NIXFT_VERSION ==="
      local shim
      shim=$(WORK=$WORK PYNIXD_BIN=$PYNIXD_BIN REAL_NIX=$NIX_PKG/bin/nix \
        bash "$SCRIPTS/make-shim.sh")
      WORK=$WORK NIX_DAEMON_PACKAGE=$shim SAVE_LOG=$PYNIXD_LOG \
        bash "$SCRIPTS/run.sh" "$@"

      # **A passing test proves nothing until pynixd was in the path.** The
      # shim sends `nix daemon` to pynixd and everything else to the real Nix,
      # and three defects in that one decision made the whole suite go green
      # while pynixd served no request at all. pynixd writes its config beside
      # each test store, so the file is the proof.
      local served
      served=$(find "$WORK/tmp" -name pynixd-test-config.json 2>/dev/null | wc -l)
      echo "pynixd served $served test store(s)"
      if [ "$served" -eq 0 ]; then
        echo "ERROR: pynixd served no test at all. The result above is meaningless." >&2
        return 1
      fi
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
      ""|-h|--help|help) usage; exit 1 ;;
      *) echo "nanopynix-nixft: no command named '$command'" >&2; usage; exit 2 ;;
    esac
  '';

  meta = {
    description = "Nix's functional test suite, run against a daemon (${version})";
    platforms = lib.platforms.linux;
  };
}
