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
each patch names its reason in the file. A fifth patch rewrote the layout of
the store of each test, and issue #176 removed it.

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
`gc-concurrent`, `optimise-store`, `multiple-outputs` and `selfref-gc` differ
for this reason and will keep differing.

The first run of this mode found issue #177: `BuildPaths` answered a status
where Nix answers a constant `1`, so a failed build read as a successful one.
No script could find it, because the client of Nix reads that number and drops
it.

## The measurement

Client, scripts and daemon all Nix 2.34.8. One serial run each, on Linux,
with the relocated store layout that the suite itself sets.

| run     | OK  | FAIL | SKIP |
| ------- | --- | ---- | ---- |
| control | 149 | 22   | 36   |
| pynixd  | 120 | 54   | 33   |

**29 regressions**: a test that the control passes and pynixd fails. 7 in the
`ca` suite, 2 in `flakes`, 2 in `dyn-drv`, and 18 in `main`.

Removing the layout patch of #176 moved `nix-channel` from FAIL to OK in the
control run, so that patch was breaking a test of Nix on its own.
`nested-sandboxing` still fails, and its cause is not the layout.

The count was 40 before issues #178, #179, #180, #182, #183, #184 and #185.
Each one is below.

Three tests moved from SKIP to FAIL, and `compare` puts them under "other
changes" rather than under the regressions. `local-overlay-store:delete-duplicate`
and `local-overlay-store:stale-file-handle` are the two new ones: the managed
daemon of pynixd does not start in the store shape of that suite, and the
script then fails where the control run skips it. The third,
`main:multiple-outputs-substitute-failure`, is older.

### The 22 control failures

**A failure here is a failure of Nix or of this harness, and not of pynixd.**
`compare` puts these under "FAILS IN BOTH" and keeps them out of the answer.
Report a genuine defect of Nix to `github/lillecarl/nix`.

- **8 `build-remote-*`** need a remote builder.
- **4 recursive-nix** — `recursive`, `ca/recursive`,
  `dyn-drv/recursive-mod-json` and `dyn-drv/dep-built-drv-2`. The `nix` inside
  the build does not learn where the store is, and it answers
  `path "..." is not in the Nix store`. Not yet understood.
- **1 `db-migration`** — the script states its own condition: "This assumes
  that the `daemon` package is older than the `client` one". Both are 2.34.8
  here.
- **9 others** — `chroot-store`, `structured-attrs` (it wants a flake
  registry), `shell`, `formatter`, `nix-profile`, `nested-sandboxing`, `json`,
  `tarball` and `fetchurl`.

### A number that hid a defect

`main:placeholders` passed through pynixd in every run before this one, and it
was never passing. pynixd answered `BuildPaths` with a number that said
"failed", `RemoteStore::buildPaths` of Nix reads that number and drops it, and
`placeholders.sh` reads the exit status alone. Issue #177 corrected the answer
and the test moved to FAIL, with a real builder error under it. Issue #178
holds that defect.

Measured both ways: with the correction the test fails, and with the
correction reverted and nothing else changed it reports `Ok: 1`.

### One test, and three defects under it

`main:placeholders` passes now. Reaching that took three corrections, and
each one made the next one visible. This is the shape of the work here, so it
is worth stating once:

1. **#178.** pynixd sent the wanted outputs of a derivation and dropped the
   others. The daemon rewrites `builtins.placeholder <name>` for each output
   the derivation names, so `${placeholder "bin"}` reached the builder as a
   path that is not there. `main:placeholders` passes with this corrected.
2. **#179.** An already-valid derived path answered with no realisation, so
   `nix build --json` wrote no `outputs` key. `main:build` read `null` at
   line 23.
3. **#180.** The request held its derived paths in a set, so the answers came
   back sorted. `main:build` reads them by position at line 8. That test
   passed in one work directory and failed in another, with no change but the
   hash part of two store paths.

`main:build` reaches line 91 now, and it started at line 8. A defect in this
project hides the next one, so a test that moves forward is progress even
when it still fails.

`main:multiple-outputs` is a `#174` difference now, and not one of the three
above. `nix store delete --ignore-liveness` cannot delete a path that a temp
root holds: `collectGarbage` of Nix reads the temp roots whatever that option
says. Add it to the list of GC tests above.

### The `ca` suite, and one id

pynixd resolves a derivation before it sends it, and the daemon then hashes a
different ATerm. Each realisation came back under that other hash, so the
client asked for the original id and found nothing. Nine of sixteen failures
were two assertions of Nix, and both stopped the program:

```
Assertion 'thisRealisation' failed at built-path.cc:122      (5 tests)
Assertion 'maybeOutputPath' failed at nix-build.cc:730       (4 tests)
```

Issue #182 gives each id its original hash again, as Nix does at
`derivation-goal.cc:193-236`. Both assertions are gone, and each test that
they killed reaches a later line now.

Issue #183 is the other half. `Derivation::shouldResolve` at
`derivations.cc:1129` states which derivations Nix resolves before it builds
them, and pynixd asked a narrower question: a deferred output alone. A
floating content-addressed output therefore went unresolved, so the builder
read a `DownstreamPlaceholder` as a path and the input was not in `inputSrcs`.

The two together took the `ca` regressions from 14 to 10.

Issue #184 is the third defect. pynixd sends a resolved derivation, and it
sent the path of the original derivation with it. `DerivationBuildingGoal` of
the daemon prefers the derivation on the disk whenever that path is valid, at
`derivation-building-goal.cc:1239`, so the daemon read the original derivation
and answered under the id of that one. pynixd writes the resolved derivation
to the store now, as `derivation-resolution-goal.cc` of Nix does, and names
that path in the request. `Store.add_text_to_store` and
`Connection.call_with_payload` are the two parts that this needed.

Issue #185 is the fourth. A floating content-addressed output takes its path
from the build, so the derivation names no path for that output, and pynixd
built the derivation again. `DerivationGoal::checkPathValidity` at
`derivation-goal.cc:405` reads the store instead: `sha256:<hash>!<name>` maps
to a path, and a valid path there ends the goal with no build.
`EnsureDerivedPathGoal` asks that question first now. `ca:build` needs it,
because `testGC` builds with `-j0` after a garbage collection, so a second
build is not allowed.

| `ca` suite    | OK  | FAIL | SKIP |
| ------------- | --- | ---- | ---- |
| before #184   | 5   | 16   | 3    |
| with #184     | 7   | 14   | 3    |
| with #185     | 11  | 9    | 4    |

`ca:build` passes. So do `build-with-garbage-path`,
`duplicate-realisation-in-closure`, `nix-copy`, `nix-shell`, `selfref-gc` and
`why-depends`.

## The store of each test

**The suite uses a relocated store, and pynixd serves one.** `common/vars.sh`
exports `NIX_STORE_DIR=$TEST_ROOT/store` and `NIX_STATE_DIR=$TEST_ROOT/var/nix`,
so the store path and the directory on disk are the same and the state sits
beside it. `make-shim.sh` reads both names out of the environment of the test
and writes them into the configuration of pynixd.

Nix moves a store the other way as well. `--store <root>` puts the files at
`<root>/nix/store` and moves no store path, so `builtins.storeDir` still
answers `/nix/store`. pynixd served that shape alone until issue #176, and a
fifth patch here rewrote the layout of the suite to match. **That patch
changed the tests to fit pynixd**, and it was the one patch of the five that
worked around pynixd rather than around the harness. It is gone, with the two
script rewrites under it.

`pynixd/store_layout.py` holds both shapes now, and `tests/unit/test_store_layout.py`
states the difference.

## The safety limit

`run.sh` starts a watchdog, and the watchdog ends the run above 1500
processes. Patch 1 of `setup.sh` gives the reason: a daemon that inherits
`NIX_REMOTE=daemon` opens itself as its store, and each worker forks another
worker. One test reached 16,451 processes in under two minutes.
