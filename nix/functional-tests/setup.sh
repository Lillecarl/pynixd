#!/usr/bin/env bash
# Prepare Nix's own functional test suite to run against a daemon.
#
# Read README.md in this directory first. It gives the mode, and the reason
# each patch below is necessary.
#
# Inputs:
#   NIX_SRC   the source of Nix, which holds tests/functional. Required.
#   WORK      the working directory. Default /scratch/nixft.
#
# Needs meson, ninja, jq, git and nix on PATH.
set -euo pipefail

NIX_SRC=${NIX_SRC:-}
WORK=${WORK:-/scratch/nixft}

if [[ -z "$NIX_SRC" ]]; then
    echo "setup.sh: set NIX_SRC to the source of Nix. For example:" >&2
    echo "  NIX_SRC=\$(nix build --file . pkgs.nixVersions.nix_2_34.src --no-link --print-out-paths)" >&2
    exit 2
fi
if [[ ! -d "$NIX_SRC/tests/functional/common" ]]; then
    echo "setup.sh: $NIX_SRC holds no tests/functional/common" >&2
    exit 2
fi

# `head -n N` then the new text then `tail -n +N+1` puts text after line N.
# The suite is bash, and each patch must fail loudly when upstream moves the
# line it needs. A silent patch that applies to nothing is the worst result.
insert_after() {
    local file=$1 pattern=$2 what=$3 line
    line=$(grep -n -- "$pattern" "$file" | head -1 | cut -d: -f1)
    if [[ -z "$line" ]]; then
        echo "setup.sh: $file holds no line for '$what'; the patch is stale" >&2
        exit 1
    fi
    {
        head -n "$line" "$file"
        cat
        tail -n "+$((line + 1))" "$file"
    } > "$file.new"
    mv "$file.new" "$file"
}

rm -rf "${WORK:?}"
mkdir -p "$WORK"
# `tests/functional/.version` and `tests/functional/nix-meson-build-support`
# are symbolic links to `../../`, so the whole source must come across.
cp -r "$NIX_SRC/." "$WORK/src"
chmod -R u+w "$WORK/src"

INIT="$WORK/src/tests/functional/common/init.sh"
FUNCS="$WORK/src/tests/functional/common/functions.sh"

# --- Patch 1: the daemon must not inherit NIX_REMOTE=daemon ---------------
#
# `startDaemon` sets NIX_REMOTE=daemon after it starts the daemon, so upstream
# always starts a daemon with an empty value. This mode exports NIX_REMOTE
# before the first test, so the daemon inherited `daemon`, and it opened
# itself as its own store. Each worker then became a client of the daemon, and
# that client forked another worker. The argv of a worker holds the process id
# of its client, and those ids were other workers:
#
#     81490  81448  Ssl  nix 81488      <- 81488 is a worker
#     81492  81448  Ssl  nix 81490      <- 81490 is a worker
#
# One test made 16,451 processes in under two minutes, and the machine went
# into swap.
launch="    PATH=\$DAEMON_PATH nix --extra-experimental-features 'nix-command' daemon &"
if [[ "$(grep -c -F -x -- "$launch" "$FUNCS")" != "1" ]]; then
    echo "setup.sh: functions.sh holds no startDaemon launch line; the patch is stale" >&2
    exit 1
fi
replacement="    PATH=\$DAEMON_PATH NIX_REMOTE= nix --extra-experimental-features 'nix-command' daemon &"
awk -v old="$launch" -v new="$replacement" '$0 == old { print new; next } { print }' \
    "$FUNCS" > "$FUNCS.new"
mv "$FUNCS.new" "$FUNCS"

# --- Patch 2: give the daemon the store again after a wipe ----------------
#
# `doClearStore` runs `rm -rf "$NIX_STATE_DIR"`, and the database of the store
# is in that directory. The daemon holds that database open. 60 of the 203
# scripts call `clearStore`. `restartDaemon` returns at once when no daemon
# runs, so the plain `NIX_REMOTE=''` mode does not change.
insert_after "$FUNCS" '^    clearProfiles$' "doClearStore's clearProfiles call" <<'EOF'
    # Added for the daemon run. The lines above deleted the database that the
    # daemon holds open. Give it the new store.
    restartDaemon
EOF

# --- Patch 3: initialise the database against the local store -------------
#
# `nix-store --init` runs before any daemon, so no socket exists yet, and
# NIX_REMOTE already says `daemon`.
sed -i 's/^nix-store --init$/NIX_REMOTE= nix-store --init/' "$INIT"
if ! grep -q '^NIX_REMOTE= nix-store --init$' "$INIT"; then
    echo "setup.sh: init.sh holds no 'nix-store --init' line; the patch is stale" >&2
    exit 1
fi

# --- Patch 4: start a daemon for every test -------------------------------
#
# Only 19 of the 203 scripts call `startDaemon` themselves. Those 19 keep
# working: `startDaemon` returns at once when a daemon already runs, and
# `restartDaemon` kills the daemon and starts it again.
# `$NIX_STATE_DIR` is a part of the pattern that grep reads, and not a name to
# expand here.
# shellcheck disable=SC2016
insert_after "$INIT" '^test -e "\$NIX_STATE_DIR"/db/db.sqlite$' "init.sh sanity check" <<'EOF'

# Added for the daemon run. `vars.sh` sets NIX_REMOTE from NIX_REMOTE_.
if [[ "${NIX_REMOTE:-}" == daemon ]]; then
    startDaemon
fi
EOF

# --- Patch 5: give each test a chroot store -------------------------------
#
# The suite puts the store at `$TEST_ROOT/store` and the state at
# `$TEST_ROOT/var/nix`. That is a relocated store. pynixd serves a chroot store
# only: it gives its managed daemon `--store <store_path>`, and
# `local-fs-store.hh:54-70` of Nix states that this makes the store
# `$root/nix/store` and the state `$root/nix/var/nix`.
#
# So each test gets a chroot layout here, and `$TEST_ROOT` is then the
# `store_path` that pynixd needs. The control run uses this layout as well,
# because a comparison of two runs must change the daemon and nothing else.
VARS="$WORK/src/tests/functional/common/vars.sh"
sed -i \
    -e 's|"\$TEST_ROOT/store"|"$TEST_ROOT/nix/store"|' \
    -e 's|^      NIX_STORE_DIR=\$TEST_ROOT/store$|      NIX_STORE_DIR=$TEST_ROOT/nix/store|' \
    -e 's|^  export NIX_LOCALSTATE_DIR=\$TEST_ROOT/var$|  export NIX_LOCALSTATE_DIR=$TEST_ROOT/nix/var|' \
    -e 's|^  export NIX_LOG_DIR=\$TEST_ROOT/var/log/nix$|  export NIX_LOG_DIR=$TEST_ROOT/nix/var/log/nix|' \
    -e 's|^  export NIX_STATE_DIR=\$TEST_ROOT/var/nix$|  export NIX_STATE_DIR=$TEST_ROOT/nix/var/nix|' \
    "$VARS"
for name in "nix/store" "nix/var" "nix/var/log/nix" "nix/var/nix"; do
    if ! grep -q "TEST_ROOT/$name" "$VARS"; then
        echo "setup.sh: vars.sh holds no '$name' after the layout patch; the patch is stale" >&2
        exit 1
    fi
done

# `init.sh` makes each of those directories, and a nested path needs `-p`.
sed -i \
    -e 's|^mkdir "\$NIX_STORE_DIR"$|mkdir -p "$NIX_STORE_DIR"|' \
    -e 's|^mkdir "\$NIX_LOCALSTATE_DIR"$|mkdir -p "$NIX_LOCALSTATE_DIR"|' \
    -e 's|^mkdir "\$NIX_STATE_DIR"$|mkdir -p "$NIX_STATE_DIR"|' \
    "$INIT"

# Two scripts build the path of the main store themselves, rather than read
# the name that holds it. Both mean the same directory under either layout.
# The names in the replacement text belong to the patched script, and this
# shell must not expand them.
# shellcheck disable=SC2016
sed -i 's|chmod -R -w "\$TEST_ROOT"/store|chmod -R -w "$NIX_STORE_DIR"|' \
    "$WORK/src/tests/functional/read-only-store.sh"
# shellcheck disable=SC2016
sed -i 's|rm -rf "\$TEST_ROOT/var/log/nix"|rm -rf "$NIX_LOG_DIR"|' \
    "$WORK/src/tests/functional/binary-cache.sh"

echo "patches applied:"
grep -n "NIX_REMOTE= nix --extra" "$FUNCS"
grep -n "    restartDaemon" "$FUNCS"
grep -n "NIX_REMOTE= nix-store --init" "$INIT"
grep -n "    startDaemon" "$INIT"

meson setup "$WORK/src/tests/functional" "$WORK/build"
echo "READY $WORK"
