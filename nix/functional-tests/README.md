# Nix's functional tests, against a daemon

Nix carries 203 functional test scripts. They encode what the daemon protocol
must do. pynixd sits in the middle of that protocol, so the scripts test
pynixd as well, when a pynixd daemon answers them.

Issue #172 holds the plan and the measurements. This file gives the mode and
the commands.

## The mode, and why Nix does not run it

Nix runs the suite two ways, and neither one tests the daemon fully:

1. The plain derivation runs with `NIX_REMOTE=''`. The client speaks to the
   local store, and no daemon takes part.
2. `tests/nixos/functional/common.nix` sets `NIX_REMOTE=daemon`, and it also
   sets `isTestOnNixOS=1`. **79 of the 209 scripts call `TODO_NixOS`**, which
   skips the test under that flag. `ca/common.sh` and `dyn-drv/common.sh` call
   it, so both suites skip there.

So the ca suite and the dyn-drv suite have **no daemon coverage upstream**.

This directory makes the third mode: one store for each test, as in mode 1,
and a daemon, as in mode 2. `setup.sh` applies four patches to reach it, and
each patch names its reason in the file.

## Run it

`setup.sh` and `run.sh` need `meson`, `ninja`, `jq`, `git` and `nix` on PATH.
The tests build derivations, so run this on Linux.

```sh
# The source of the tests. The binary cache holds the built suite as well, at
# pkgs.nixVersions.nixComponents_2_34.nix-functional-tests.
NIX_SRC=$(nix build --file . pkgs.nixVersions.nix_2_34.src --no-link --print-out-paths)

NIX_SRC=$NIX_SRC ./setup.sh
./run.sh                    # every suite
./run.sh --suite ca         # one suite
./run.sh gc fetchurl        # named tests
```

**Match the version of the daemon to the version of the tests.** The scripts
travel with the *client*, so the suite states the version of the client, and
`NIX_DAEMON_PACKAGE` is the free one. Use the same version for both until a
reason to differ appears. `supportedNixFloor` is 2.34, and 2.34 is what the
measurement below used.

## Test pynixd

`run.sh` reads `NIX_DAEMON_PACKAGE`, which is the hook Nix already has for
this: `tests/functional/package.nix` takes a `test-daemon` argument, and Nix
builds `nix-daemon-compat-tests` from it. Give a package whose `bin/nix`
sends `daemon` to pynixd, and every other command to the real Nix.
`db-migration.sh` and `user-envs-migration.sh` call
`$NIX_DAEMON_PACKAGE/bin/nix` for ordinary commands, and `isDaemonNewer` calls
it for `daemon --version`, so a shim that answers `daemon` alone is not
enough.

**Compare against the control.** Run the suite with a plain `nix daemon`
first. A test that fails in both runs is not a defect of pynixd. Only a test
that passes the control and fails through pynixd is one.

### In the Linux virtual machine of a Darwin host

The tests build derivations, so they need Linux. The store is shared with the
host, and `/scratch` belongs to the machine.

```sh
# On the host. `source.nix` filters the checkout into the shared store.
SRC=$(nix eval --raw --impure --expr \
    '(import ./nix/source.nix { lib = (import <nixpkgs> {}).lib; })')

# In the machine. The tools live in the writable store of the machine, so a
# rebuild of the machine removes them, and this command makes them again.
nix build --no-link --print-out-paths \
    nixpkgs#meson nixpkgs#ninja nixpkgs#jq nixpkgs#git nixpkgs#busybox

# In the machine. FLAKE_COMPATISH_DISABLE_OVERRIDES makes this agree with a
# flake evaluation, as every CI workflow does.
FLAKE_COMPATISH_DISABLE_OVERRIDES=1 \
    nix build --file "$SRC" pynixd --no-link --print-out-paths
```

Put `busybox` last on PATH. Before it, `meson` finds `ls` there rather than in
coreutils, and it then states the wrong directory for `coreutils`.

## The control measurement

Client, scripts and daemon all Nix 2.34.8. One serial run, on Linux:

```
149 OK    36 SKIP    18 FAIL     (203 total)

suite                 OK   SKIP   FAIL
main                  92     19     15
flakes                30      1      0
ca                    18      4      1
dyn-drv                5      1      2
local-overlay-store    0     11      0
git-hashing            3      0      0
git                    1      0      0
```

The 18 failures:

- **8 `build-remote-*`** need a remote builder.
- **4 recursive-nix** — `recursive`, `ca/recursive`,
  `dyn-drv/recursive-mod-json` and `dyn-drv/dep-built-drv-2`. The `nix` inside
  the build does not learn that the store is at `$TEST_ROOT/store`, and it
  answers `path "..." is not in the Nix store`. Not yet understood.
- **1 `db-migration`** — the script states its own condition: "This assumes
  that the `daemon` package is older than the `client` one". Both are 2.34.8
  here.
- **5 others** — `chroot-store`, `structured-attrs` (it wants a flake
  registry), `shell`, `formatter`, `nix-profile`.

## What pynixd needs first

**pynixd serves a chroot store only.** `LocalSocketStoreSpec` holds one
`store_path`, and the managed daemon gets `--store <store_path>`.
`local-fs-store.hh:54-70` of Nix states that this gives `$root/nix/store` and
`$root/nix/var/nix`. The suite uses `$TEST_ROOT/store` and `$TEST_ROOT/var/nix`,
which is a relocated store and not a chroot store.

Change `common/vars.sh` to a chroot layout first, because that changes no
pynixd code, and a failure then names pynixd and nothing else. Only 2 of the
203 scripts name the main store directly: `read-only-store.sh:40` and
`binary-cache.sh:33`. Every other `$TEST_ROOT/store*` is a second store that
the test makes for itself.

## The safety limit

`run.sh` starts a watchdog, and the watchdog ends the run above 1500
processes. Patch 1 of `setup.sh` gives the reason: a daemon that inherits
`NIX_REMOTE=daemon` opens itself as its store, and each worker forks another
worker. One test reached 16,451 processes in under two minutes.
