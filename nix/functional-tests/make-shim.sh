#!/usr/bin/env bash
# Build the package that goes in the `NIX_DAEMON_PACKAGE` place, so that
# pynixd answers the functional tests of Nix.
#
# `run.sh` puts `$NIX_DAEMON_PACKAGE/bin` first on the PATH that `startDaemon`
# uses, and `startDaemon` then runs `nix daemon`. So `bin/nix` of this package
# sends `daemon` to pynixd. It sends every other command to the real Nix,
# because `db-migration.sh` and `user-envs-migration.sh` call
# `$NIX_DAEMON_PACKAGE/bin/nix` for ordinary commands, and `isDaemonNewer`
# calls it for `daemon --version`.
#
# Inputs:
#   WORK         the working directory of setup.sh. Default /scratch/nixft.
#   PYNIXD_BIN   the pynixd program. Default: pynixd on PATH.
#   REAL_NIX     the nix program. Default: nix on PATH.
#
# It prints the path of the package, for `NIX_DAEMON_PACKAGE`.
set -euo pipefail

WORK=${WORK:-/scratch/nixft}
SHIM=$WORK/shim
PYNIXD_BIN=${PYNIXD_BIN:-$(command -v pynixd || true)}
REAL_NIX=${REAL_NIX:-$(command -v nix || true)}

if [[ -z "$PYNIXD_BIN" || ! -x "$PYNIXD_BIN" ]]; then
    echo "make-shim.sh: set PYNIXD_BIN to the pynixd program" >&2
    exit 2
fi
if [[ -z "$REAL_NIX" || ! -x "$REAL_NIX" ]]; then
    echo "make-shim.sh: set REAL_NIX to the nix program" >&2
    exit 2
fi

rm -rf "${SHIM:?}"
mkdir -p "$SHIM/bin"

# The two paths come from this shell. Everything after them belongs to the
# shim, and this shell must not read any of it.
cat > "$SHIM/bin/nix" <<EOF
#!/usr/bin/env bash
REAL_NIX=$REAL_NIX
PYNIXD_BIN=$PYNIXD_BIN
EOF

cat >> "$SHIM/bin/nix" <<'EOF'
set -euo pipefail

# Find the subcommand. `startDaemon` runs
#   nix --extra-experimental-features 'nix-command' daemon
# so `daemon` is the last word and not the first. A test of `$1` alone sends
# every daemon to the real Nix, and the suite then measures nothing.
#
# The word after a flag is not the subcommand either:
# `--extra-experimental-features` takes a value, so "the first word that is not
# a flag" finds `nix-command`. This looks for the word `daemon` in any place.
# The suite runs no other command that holds that word.
is_daemon=false
wants_version=false
for arg in "$@"; do
    case "$arg" in
        daemon) is_daemon=true ;;
        --version) wants_version=true ;;
        *) ;;
    esac
done

# Only `nix daemon` belongs to pynixd. `nix daemon --version` does not: the
# suite asks it for a version number, and pynixd states the version of the
# protocol and not of Nix.
if [[ "$is_daemon" != true ]] || [[ "$wants_version" == true ]]; then
    exec "$REAL_NIX" "$@"
fi

if [[ -z "${NIX_STORE_DIR:-}" || -z "${NIX_DAEMON_SOCKET_PATH:-}" ]]; then
    echo "shim: NIX_STORE_DIR and NIX_DAEMON_SOCKET_PATH must be set" >&2
    exit 2
fi

# `setup.sh` gives each test a chroot store, so NIX_STORE_DIR is
# `$TEST_ROOT/nix/store` and `$TEST_ROOT` is the root that pynixd needs.
#
# `readlink -f` is necessary. Nix refuses a store when a parent directory of it
# is a symbolic link, and it says so: "the path ... is a symlink; this is not
# allowed for the Nix store and its parent directories". `vars.sh` resolves
# NIX_STORE_DIR for the same reason.
root=$(readlink -f "$(dirname "$(dirname "$NIX_STORE_DIR")")")

config=$root/pynixd-test-config.json
cat > "$config" <<JSON
{
  "stores": {
    "local": {
      "type": "local-socket",
      "store_path": "$root",
      "socket_path": "$root/pynixd-upstream.socket",
      "nix_bin": "$REAL_NIX",
      "use_db": true,
      "monitor": false,
      "probe": false
    }
  },
  "unix_path": "$NIX_DAEMON_SOCKET_PATH",
  "ssh_port": null,
  "http_port": null
}
JSON

export PYNIXD_CONFIG=$config
# The suite starts this in the background and waits for the socket to appear.
exec "$PYNIXD_BIN" daemon
EOF

chmod +x "$SHIM/bin/nix"
echo "$SHIM"
