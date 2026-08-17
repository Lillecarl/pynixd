#!/usr/bin/env bash
# Everything the functional suite needs, in one run inside the Linux builder.
#
# **The builder is disposable, and this script takes that as the rule.** The
# worker lives about 60 seconds and a restart wipes the writable layer of its
# store, so nothing here may depend on a step of an earlier invocation. Each
# run seeds the fetch cache, builds the runner, prepares the work directory
# and runs the command, and each of those four steps is a no-op when its
# result is already there.
#
# `nixft.sh` on the host starts this script. Do not call it directly: it reads
# its arguments in a fixed order and states no defaults.
#
# Arguments, in this order:
#   SRC          the source store path of the checkout to test
#   WORK         the work directory of the suite, or `-` for the default
#   GIT_CACHE    a store path that holds `tarball-cache-v2`, or `-`
#   FETCH_CACHE  a store path that holds `fetcher-cache-v4.sqlite`, or `-`
#   ...          the arguments of the runner, for example `pynixd --suite ca`
#
# The last line it writes is `NIXFT-DONE <code>`. The host reads that line to
# tell a test failure from a dead worker: a run with no such line did not
# finish, whatever exit code the transport gave.
set -uo pipefail

SRC=$1
WORK=$2
GIT_CACHE=$3
FETCH_CACHE=$4
shift 4

say() {
    echo "nixft-remote: $*" >&2
}

# ── The work directory ──────────────────────────────────────────────
#
# Two properties, and Nix states the reason for each one itself.
#
# **No parent of it is a symbolic link.** Nix refuses such a store: "the path
# ... is a symlink; this is not allowed for the Nix store and its parent
# directories". `/tmp` on darwin is that case, and it is why the suite cannot
# run on the host.
#
# **The path is short.** Each test puts a daemon socket at
# `<work>/tmp/nix-test/<suite>/<test>/dSocket`, and `sun_path` holds 108
# bytes. A long work directory therefore fails inside a test, and the failure
# it gives says only that a socket did not appear.
#
# **No directory of the builder survives a restart of it, and `$HOME` is no
# exception.** `$HOME` and `/scratch` are two paths on one ext4 image, and
# `runVm` of the host makes that image again on every cold boot. The host
# shares `/host-nix` and `/var/keys`, and both are read-only, so there is no
# writable path of the host to fall back to either. `$HOME/nixft` is the
# default because it is short, and for no other reason.
#
# The property that does hold is narrower, and the design above rests on it:
# the builder shuts down after 60 seconds with **no open connection**. One
# invocation holds one connection for its whole life, so a suite that runs
# inside a single invocation cannot lose its work directory halfway.
if [[ "$WORK" == "-" ]]; then
    WORK=$HOME/nixft
fi
mkdir -p "$WORK"
WORK=$(readlink -f "$WORK")

# The longest suffix a test appends. `local-overlay-store` is the longest
# suite name, and `build-with-garbage-path` the longest test name of the ones
# that this suite runs.
readonly SOCKET_SUFFIX="/tmp/nix-test/local-overlay-store/build-with-garbage-path/dSocket"
readonly SUN_PATH_LIMIT=108
if (( ${#WORK} + ${#SOCKET_SUFFIX} > SUN_PATH_LIMIT )); then
    say "the work directory is too long: ${#WORK} bytes, and a test adds ${#SOCKET_SUFFIX}."
    say "a Unix socket path holds $SUN_PATH_LIMIT bytes. Give a shorter --work."
    echo "NIXFT-DONE 2"
    exit 0
fi

# ── The fetch cache ─────────────────────────────────────────────────
#
# The evaluation reads the lockfile and fetches the `flake-compatish` input
# from the tarball endpoint of GitHub. GitHub answers `429 Too Many Requests`
# after a few runs, and a token does not raise that limit: the limiter is the
# anti-scraping one and it counts the address. The retry then waits 69 s,
# 121 s, 259 s and 573 s, which is longer than the run.
#
# The host holds the answer already. This copies the two parts of its cache
# through the store, which is the one thing the host and the builder share.
seed_the_fetch_cache() {
    if [[ "$GIT_CACHE" == "-" || "$FETCH_CACHE" == "-" ]]; then
        return
    fi
    if [[ -d "$HOME/.cache/nix/tarball-cache-v2" ]]; then
        return
    fi
    say "seeding the fetch cache"
    mkdir -p "$HOME/.cache/nix"
    # The old journal files of SQLite name the database that was there
    # before, so a copy of the database alone leaves the reader with two
    # halves of two different caches.
    rm -f "$HOME/.cache/nix/fetcher-cache-v4.sqlite"*
    cp -f "$FETCH_CACHE" "$HOME/.cache/nix/fetcher-cache-v4.sqlite"
    cp -r "$GIT_CACHE" "$HOME/.cache/nix/tarball-cache-v2"
    chmod -R u+w "$HOME/.cache/nix"
}
seed_the_fetch_cache

# ── The runner ──────────────────────────────────────────────────────
#
# `--no-link` and `--print-out-paths`, and no output link. An output link is a
# file in a directory that a restart wipes, and the store path is what the
# next step needs. `FLAKE_COMPATISH_DISABLE_OVERRIDES=1` makes this evaluation
# agree with a flake evaluation: it reads the lockfile rather than the local
# checkout.
say "building the runner from $SRC"
if ! runner=$(FLAKE_COMPATISH_DISABLE_OVERRIDES=1 \
    nix build --file "$SRC" nixFunctionalTests.nix_2_34 \
    --no-link --print-out-paths 2>&1 | tail -1); then
    say "the runner did not build"
    echo "NIXFT-DONE 3"
    exit 0
fi
if [[ ! -d "$runner/bin" ]]; then
    say "the runner build printed no store path: $runner"
    echo "NIXFT-DONE 3"
    exit 0
fi
runner_bin=$(echo "$runner"/bin/nanopynix-nixft-*)
if [[ ! -x "$runner_bin" ]]; then
    say "the runner holds no program: $runner/bin"
    echo "NIXFT-DONE 3"
    exit 0
fi

# ── The work directory of the suite ─────────────────────────────────
#
# `setup` wipes the directory it prepares, so this runs it only when the
# meson build directory is absent. That is the test of "a restart took it".
if [[ ! -d "$WORK/build" ]]; then
    say "preparing $WORK"
    if ! NIXFT_WORK="$WORK" "$runner_bin" setup >/dev/null; then
        say "setup failed"
        echo "NIXFT-DONE 4"
        exit 0
    fi
fi

# ── The command ─────────────────────────────────────────────────────
NIXFT_WORK="$WORK" "$runner_bin" "$@"
code=$?
echo "NIXFT-DONE $code"
exit 0
