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

**`nixFunctionalTests` in `default.nix` builds one program for each supported
Nix version, and that program is the way to run this.** It carries its Nix,
the test scripts of that Nix, pynixd, and every tool the scripts need. Nothing
comes off the PATH of the machine.

```sh
nix build --file . nixFunctionalTests.nix_2_34 --out-link result
./result/bin/nanopynix-nixft-nix_2_34 all
```

`nix_2_34`, `nix_2_35` and `git` are the versions, and they are the versions
that `supportedNixFloor` in `default.nix` selects. The commands are:

| command   | what it does                                           |
| --------- | ------------------------------------------------------ |
| `setup`   | prepare the suite. Run it first. It wipes the work dir. |
| `control` | run the suite against a plain `nix daemon`              |
| `pynixd`  | run the suite against pynixd                            |
| `compare` | state which tests pynixd alone fails                    |
| `all`     | all four, in that order                                 |

The stream mode reads the wire instead of the verdict of each script:

| command          | what it does                                      |
| ---------------- | ------------------------------------------------- |
| `record-control` | run against a plain `nix daemon`, and record       |
| `record-pynixd`  | run against pynixd, and record                     |
| `diff-streams`   | state which tests differ on the wire               |
| `streams`        | setup, both records, and the comparison            |

Each further argument goes to `meson test`, so `control --suite ca` runs one
suite and `control gc fetchurl` runs two tests.

`NIXFT_WORK` names the work directory, and `JOBS` gives the number of tests at
a time. The default is one.

**Match the version of the daemon to the version of the tests.** The scripts
travel with the *client*, so the suite states the version of the client. Each
program above holds one Nix and uses it for both, which is why the version is
in its name.

**The tests build derivations, so they need Linux.** On a Darwin host, build
the program in the Linux machine and then run the same store path there. The
store is shared, so one build serves both.

```sh
# On the host. `source.nix` filters the checkout into the shared store.
SRC=$(nix eval --raw --impure --expr \
    '(import ./nix/source.nix { lib = (import <nixpkgs> {}).lib; })')

# In the machine. FLAKE_COMPATISH_DISABLE_OVERRIDES makes this agree with a
# flake evaluation, as every CI workflow does.
vzrun env FLAKE_COMPATISH_DISABLE_OVERRIDES=1 \
    nix build --file "$SRC" nixFunctionalTests.nix_2_34 --no-link --print-out-paths

# `/scratch` of the machine is on a disk. `/` is a tmpfs, and the stores of the
# tests do not fit in it.
vzrun env NIXFT_WORK=/scratch/nixft-2.34 \
    /nix/store/...-nanopynix-nixft-nix_2_34/bin/nanopynix-nixft-nix_2_34 all
```

## How pynixd takes the place of the daemon

`run.sh` reads `NIX_DAEMON_PACKAGE`, which is the hook Nix already has for
this: `tests/functional/package.nix` takes a `test-daemon` argument, and Nix
builds `nix-daemon-compat-tests` from it. `make-shim.sh` builds a package
whose `bin/nix` sends `daemon` to pynixd, and every other command to the real
Nix. `db-migration.sh` and `user-envs-migration.sh` call
`$NIX_DAEMON_PACKAGE/bin/nix` for ordinary commands, and `isDaemonNewer` calls
it for `daemon --version`, so a shim that answers `daemon` alone is not
enough.

**A passing test proves nothing until pynixd was in the path.** Three defects
in that one decision made the whole suite report success while the shim sent
every daemon to the real Nix. pynixd writes `pynixd-test-config.json` beside
each test store, so the `pynixd` command counts those files and fails when it
finds none.

**Compare against the control.** A test that fails in both runs is not a
defect of pynixd. Only a test that passes the control and fails through pynixd
is one. `compare.py` states that difference, and `compare` calls it.

## The stream mode

**A script says "pass" or "fail" for reasons that are not the wire.** It reads
a message, it counts the store paths on the disk, it wants a path to be dead.
The contract of pynixd is narrower: a client must not be able to tell pynixd
from `nix-daemon`, and that is a statement about the bytes.

So `streams` runs the same workload twice with a recorder between the client
and the daemon, and compares the two streams of operations. A test whose
script fails in both runs still gives an answer here.

```
client -> $NIX_DAEMON_SOCKET_PATH -> recorder -> iSocket -> the daemon
```

`make-record-shim.sh` builds the recorder into the `NIX_DAEMON_PACKAGE` place,
over an inner package. The inner package is the plain Nix in one run and the
pynixd shim in the other, so the two runs differ in the daemon and in nothing
else. The recorder starts the inner daemon as its own child, so `killDaemon`
kills one pid and both go away.

The recordings go to `$NIXFT_WORK/streams/{control,pynixd}/<suite>/<test>/
daemon-N/conn-NNNN.wire`. `<suite>` and `<test>` come from `TEST_SUITE_NAME`
and `TEST_NAME`, which meson sets, so the two runs write the same names.
`daemon-N` counts the daemons of one test, because `restartDaemon` starts a
second one.

**Some differences are on purpose, and `wirelog compare` lists each one with
its reason.** The name and the version of pynixd, the protocol version it
presents, the features it adds, the trust it reports, and the registration
time of a store path. The last one is a difference of the two runs and not of
the two daemons: they add the same path at two times.

**A build holds its temporary roots for the lifetime of the connection, and
the GC tests read that as a defect.** Issue #174 records the decision: a
long-lived daemon that holds a path is not a fault, and `max_lifetime` bounds
how long it lasts. `simple`, `gc`, `ca/gc`, `dependencies`, `build-delete`,
`gc-concurrent`, `optimise-store` and `selfref-gc` differ for this reason and
will keep differing.

The first run of this mode found issue #177: `BuildPaths` answered a status
where Nix answers a constant `1`, so a failed build read as a successful one.
No script could find it, because the client of Nix reads that number and drops
it.

## The control measurement

Client, scripts and daemon all Nix 2.34.8. One serial run, on Linux, with the
chroot store layout of patch 5:

```
151 OK    36 SKIP    20 FAIL     (207 total)
```

The 20 failures:

- **8 `build-remote-*`** need a remote builder.
- **4 recursive-nix** — `recursive`, `ca/recursive`,
  `dyn-drv/recursive-mod-json` and `dyn-drv/dep-built-drv-2`. The `nix` inside
  the build does not learn where the store is, and it answers
  `path "..." is not in the Nix store`. Not yet understood.
- **1 `db-migration`** — the script states its own condition: "This assumes
  that the `daemon` package is older than the `client` one". Both are 2.34.8
  here.
- **7 others** — `chroot-store`, `structured-attrs` (it wants a flake
  registry), `shell`, `formatter`, `nix-profile`, `nix-channel`,
  `nested-sandboxing`. The last two arrived with patch 5, and neither is
  explained yet.

**A failure here is a failure of Nix or of this harness, and not of pynixd.**
`compare` puts these under "FAILS IN BOTH" and keeps them out of the answer.
Report a genuine defect of Nix to `github/lillecarl/nix`.

## Why each test gets a chroot store

**pynixd serves a chroot store only.** `LocalSocketStoreSpec` holds one
`store_path`, and the managed daemon gets `--store <store_path>`.
`local-fs-store.hh:54-70` of Nix states that this gives `$root/nix/store` and
`$root/nix/var/nix`. The suite puts the store at `$TEST_ROOT/store` and the
state at `$TEST_ROOT/var/nix`, which is a relocated store and not a chroot
store.

Patch 5 changes the layout of the suite, and not pynixd, so a failure then
names pynixd and nothing else. The control run takes the same patch, because a
comparison of two runs must change the daemon and nothing else. Only 2 of the
207 scripts name the main store directly: `read-only-store.sh:40` and
`binary-cache.sh:33`. Every other `$TEST_ROOT/store*` is a second store that
the test makes for itself.

## The safety limit

`run.sh` starts a watchdog, and the watchdog ends the run above 1500
processes. Patch 1 of `setup.sh` gives the reason: a daemon that inherits
`NIX_REMOTE=daemon` opens itself as its store, and each worker forks another
worker. One test reached 16,451 processes in under two minutes.
