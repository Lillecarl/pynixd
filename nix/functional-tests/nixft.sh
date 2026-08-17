#!/usr/bin/env bash
# Run the Nix functional tests in the Linux builder, from the darwin host.
#
#   ./nixft.sh pynixd --suite ca
#   ./nixft.sh --work "\$HOME/nixft-ca" all
#
# **The builder is disposable, and a run must not care.** It shuts down after
# 60 seconds with no open connection, and the next boot makes its disk again
# from nothing. No directory of it survives that, `$HOME` included: `$HOME`
# and `/scratch` are two paths on one ext4 image that the host truncates on
# every cold boot.
#
# Two rules follow, and this script is both of them:
#
# - **One invocation does the whole job.** A host script that built, then
#   prepared, then ran, left a half-made work directory whenever a restart
#   landed between two of those calls, and the next call reported something
#   else. `nixft-remote.sh` re-establishes everything it needs instead.
# - **One invocation holds one connection.** The shutdown counts open
#   connections, so a suite that runs inside a single invocation keeps the
#   builder alive for its whole length and cannot lose its work directory
#   halfway.
#
# **A dead worker and a failed test are not the same answer.**
# `nixft-remote.sh` writes `NIXFT-DONE <code>` as its last line and always
# exits 0, so the exit code of the transport says one thing only: whether the
# command reached the end. A run with no such line is retried; a run with one
# is the answer, and this exits with the code that line names.
#
# Options:
#   --work DIR    the work directory in the builder. Default: the builder
#                 chooses `$HOME/nixft`, which is short. No directory of the
#                 builder outlives a restart of it, so the name buys nothing
#                 else.
#   --tries N     attempts before giving up. Default 3.
#   --repo DIR    the checkout to test. Default: the one that holds this file.
set -euo pipefail

# `-` and not a path with `$HOME` in it. The home directory of the builder is
# not the home directory of this host, and a quoted `$HOME` would reach the
# builder as the six characters of its own name.
WORK=-
TRIES=3
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)

while (( $# > 0 )); do
    case "$1" in
        --work) WORK=$2; shift 2 ;;
        --tries) TRIES=$2; shift 2 ;;
        --repo) REPO=$2; shift 2 ;;
        --) shift; break ;;
        *) break ;;
    esac
done

if (( $# == 0 )); then
    echo "nixft.sh: give a command for the runner, for example 'pynixd --suite ca'" >&2
    exit 2
fi

say() {
    echo "nixft: $*" >&2
}

# **The source path names the content of the checkout.** The builder reads the
# checkout through this store path and not through a shared directory, so the
# code under test is the code this host holds, and a build of the same
# checkout is a cache hit.
say "reading the source of $REPO"
src=$(cd "$REPO" && nix eval --raw --impure --expr \
    '(import ./nix/source.nix { lib = (import <nixpkgs> {}).lib; })')

# The two parts of the fetch cache of this host, through the store. See
# `seed_the_fetch_cache` in `nixft-remote.sh` for the reason. A host with no
# cache passes `-`, and the builder then fetches for itself.
add_cache_part() {
    local path=$1 name=$2
    if [[ ! -e "$path" ]]; then
        echo "-"
        return
    fi
    nix store add-path "$path" --name "$name" 2>/dev/null || echo "-"
}
git_cache=$(add_cache_part "$HOME/.cache/nix/tarball-cache-v2" nix-tarball-cache)
fetch_cache=$(add_cache_part "$HOME/.cache/nix/fetcher-cache-v4.sqlite" nix-fetcher-cache)

remote=$src/pynixd/nix/functional-tests/nixft-remote.sh
if [[ ! -f "$remote" ]]; then
    say "the source holds no nixft-remote.sh at $remote"
    exit 2
fi

# An explicit template. `mktemp -t nixft` is enough for the mktemp of darwin
# and not for the one of coreutils, which the dev shell puts first and which
# answers "too few X's in template".
log=$(mktemp "${TMPDIR:-/tmp}/nixft.XXXXXX")
trap 'rm -f "$log"' EXIT

for (( attempt = 1; attempt <= TRIES; attempt++ )); do
    say "attempt $attempt of $TRIES: $*"
    # `|| true`, because the exit code of the transport says only whether the
    # command reached the end. The sentinel below is the answer.
    vzrun bash "$remote" "$src" "$WORK" "$git_cache" "$fetch_cache" "$@" \
        2>&1 | tee "$log" || true

    sentinel=$(grep -E '^NIXFT-DONE [0-9]+$' "$log" | tail -1 || true)
    if [[ -n "$sentinel" ]]; then
        code=${sentinel#NIXFT-DONE }
        exit "$code"
    fi
    say "the worker did not finish the command. Trying again."
done

say "gave up after $TRIES attempts"
exit 1
