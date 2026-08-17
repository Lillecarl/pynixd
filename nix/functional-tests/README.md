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

### From a darwin host: `nixft.sh`

The suite needs Linux, and a darwin host reaches Linux through the `vzrun`
builder. Use `nixft.sh` there, and give it the same commands:

```sh
./pynixd/nix/functional-tests/nixft.sh pynixd --suite ca
./pynixd/nix/functional-tests/nixft.sh --work /scratch/nixft-full all
```

**`--dump PATH` writes a file of the builder to the terminal.**
`meson test --print-errorlogs` prints the tail of the log of a failed test and
not the whole of it, so a `log.debug` line that a request writes at its start
does not reach the terminal. The whole log is at
`build/meson-logs/testlog.txt`, and no directory of the builder survives the
run, so the file has to leave in the same invocation:

```sh
./pynixd/nix/functional-tests/nixft.sh --log-level DEBUG \
    --dump build/meson-logs/testlog.txt pynixd build
```

A path with no leading `/` is relative to the work directory. Give the option
again for a second file.

**`--nix-version` picks which Nix the suite measures.** `nix_2_34` is the
default, because `supportedNixFloor` in `default.nix` names 2.34 as the floor
of this repository. `nix_2_35` and `git` are the other two.

```sh
./pynixd/nix/functional-tests/nixft.sh --nix-version nix_2_35 \
    --work /scratch/nixft-235 all --suite ca
```

**2.35 is the only arm that can see the gap of issue #162.** The protocol
number stopped at 1.38 and each new capability is a feature name; 2.34 offers
none of them, so a run against 2.34 measures nothing about the features that
`DrvOutput` and `UnkeyedRealisation` now hang on. 2.35 offers
`realisation-with-path-not-hash`, so it is the version that reads what pynixd
does with a peer that names it.

Give a separate `--work` for each version. The work directory holds the
checked-out test scripts of one Nix, and `setup` runs only when the build
directory is absent.

**Keep the work directory outside `$HOME`.** One run of `all --suite ca` under
each of two paths, same checkout and same suite:

| work directory      | regressions | fails in the control as well |
| ------------------- | ----------- | ---------------------------- |
| `$HOME/nixft-ca`    | 2           | 8                            |
| `/scratch/nixft-ca` | 4           | 1                            |

Seven tests that a plain `nix-daemon` passes fail under `$HOME`: `build`,
`build-cache`, `nix-copy`, `nix-shell`, `repl`, `selfref-gc` and `signatures`.
Each one builds something.

**A build user cannot reach `$HOME`.** The two directories carry these modes:

```
drwx------ builder builder  /nix/.rw-store/home
drwxrwxrwt root    root     /nix/.rw-store/scratch
```

A sandboxed build runs as `nixbld1`, which is `uid=30001 gid=30000(nixbld)`.
That user is not `builder`, it is not in the group of `builder`, and mode 700
gives "other" no execute bit, so it cannot traverse into `$HOME`. `/scratch`
is 1777, which every user may traverse.

The consequence is the reason this table is here at all: the control run is
the measuring instrument of this suite, and a broken control hides a
regression rather than reports one. Two of the four real regressions read as
"fails in both" in that run.

**The builder is disposable, and the script takes that as the rule.** It shuts
down after 60 seconds with no open connection, and the next boot makes its
disk again from nothing. No directory of it survives that, `$HOME` included:
`$HOME` and `/scratch` are two paths on one ext4 image that the host
truncates on every cold boot. So the choice of name above buys no durability,
and the table is its whole reason.

Two rules follow, and the script is both of them:

- **One invocation does the whole job.** `nixft-remote.sh` runs inside the
  builder and seeds the fetch cache, builds the runner, prepares the work
  directory and runs the command. Each of the four is a no-op when its result
  is already there. A host script that built, then prepared, then ran, left a
  half-made work directory whenever a restart landed between two calls, and
  the next call then reported something unrelated.
- **One invocation holds one connection.** The shutdown counts open
  connections, so a suite that runs inside one invocation keeps the builder
  alive for its whole length.

`nixft-remote.sh` writes `NIXFT-DONE <code>` as its last line and always exits
0. That is how the host tells a failed test from a dead worker: a run with no
such line did not finish, whatever the transport reported, and `nixft.sh`
tries it again.

The builder reads the checkout as a store path, and not as a shared
directory, so the code under test is the code the host holds. `nixft.sh`
passes that path, and it reads `nixft-remote.sh` out of it as well.

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

**Give the machine the fetch cache of the host, once for each start of the
machine.** The evaluation reads the lockfile and fetches the
`flake-compatish` input from the GitHub tarball endpoint. GitHub answers `429
Too Many Requests` to that endpoint after a few runs, and a token does not
raise the limit: the limiter is the anti-scraping one, and it counts the
address. `nix` then waits 69 s, 121 s, 259 s and 573 s between the tries,
which is longer than the test run.

The host already holds the answer in `~/.cache/nix`. The machine wipes its
home on a restart, so it asks GitHub again every time. The store is the one
thing the two share, so send the cache through it:

```sh
GIT_CACHE=$(nix store add-path ~/.cache/nix/tarball-cache-v2 --name nix-tarball-cache)
FETCHER=$(nix store add-path ~/.cache/nix/fetcher-cache-v4.sqlite --name nix-fetcher-cache)

vzrun sh -c "
    mkdir -p \$HOME/.cache/nix
    rm -f \$HOME/.cache/nix/fetcher-cache-v4.sqlite*
    cp -f $FETCHER \$HOME/.cache/nix/fetcher-cache-v4.sqlite
    cp -r $GIT_CACHE \$HOME/.cache/nix/tarball-cache-v2
    chmod -R u+w \$HOME/.cache/nix
"
```

Remove the `-shm` and the `-wal` file of the SQLite database as well. A
database that arrives beside the journal of a different run reads as corrupt,
and `nix` then fetches again.

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

### The same mode, without the suite

`pynixd/tests/parity/test_wire_parity.py` runs one small workload the same
way, with the same recorder and the same comparison. It needs no Linux and no
builder of Nix, so it runs in the dev shell of any host and it takes eight
seconds.

It found four more defects, and each one is a difference that no script of
the suite reports:

1. `nix store gc` deleted nothing through pynixd. An idle pooled connection
   kept a worker of the daemon alive, and that worker held the temporary root
   of the path. Issue #174.
2. `QueryPathInfo` answered `sha256:<digest>` where `nix-daemon` answers the
   digest alone. The fast path of pynixd read the `narHash` column of the
   database, and that column carries the name of the algorithm. No client
   complained, because `Hash::parseAny` reads both forms. The signature of a
   path did complain: `ValidPathInfo::fingerprint` at `path-info.cc:48` puts
   the base-32 digest in the string it signs, and pynixd signed the base-16
   one, so every signature that pynixd made was false.
3. The second `nix build` of a content-addressed derivation answered
   `willBuild: [cad.drv]` to `QueryMissing`, and `nix-daemon` answers an empty
   set. `Store::queryMissing` reads `queryPartialDerivationOutputMap` at
   `misc.cc:217`, which answers from the realisation when the derivation names
   no output path. The client took a different code path after that answer, so
   every operation after it differed too.
4. `nix build --rebuild` and `--repair` both failed. The goal system of pynixd
   raised `RuntimeError` for `BuildMode.CHECK` and for `BuildMode.REPAIR`, so
   those two commands, and `nix-store --realise --check`, `--repair-path` and
   `--verify --repair`, all failed through pynixd and passed through
   `nix-daemon`. Neither mode is a build that pynixd can schedule, so the
   request goes straight to the local store now.

**The lesson is the size of the workload.** Sixty commands find what 207
scripts do not, because a script reads its own exit status and this reads the
bytes.

## The measurement

Client, scripts and daemon all Nix 2.34.8. One serial run each, on Linux,
with the relocated store layout that the suite itself sets.

| run     | OK  | FAIL | SKIP |
| ------- | --- | ---- | ---- |
| control | 151 | 20   | 36   |
| pynixd  | 148 | 24   | 35   |

**3 regressions**: a test that the control passes and pynixd fails.

| test                 | why                                             |
| -------------------- | ----------------------------------------------- |
| `ca:new-build-cmd`   | #196, and it fails at the same `build.sh:247`    |
| `main:build`         | #196, at `build.sh:247`                          |
| `main:store-info`    | permanent, and the next section gives the reason |

So two issues hold every regression, one of the two is on purpose, and the
two that are not are **one assertion**. `new-build-cmd.sh` sources `build.sh`,
so both arrive at the same line.

`main:multiple-outputs-substitute-failure` moved SKIP to FAIL. Issue #199
holds it: the managed upstream daemon did not start, which is a fault of the
harness rather than of the subject of the test.

`main:multiple-outputs`, `main:gc-concurrent` and `main:build-delete` come and
go between runs, and all three are the #174 family. Read a single failure of
one of them as a report of that issue, and not as a new one.

**20 tests fail in both arms.** Those are a defect of Nix or of this harness,
and not of pynixd. `main:build-remote-*` is eight of them, because the suite
has no second machine to build on here.

**The control moved from 149 to 151, and the shell is the reason.** The
`bash` that `runtimeInputs` now names put a shell in the store, so
`chroot-store` and `nested-sandboxing` build inside their sandbox and pass in
both arms. The section below on the harness gives the mechanism. A control
that fails hides a regression, so read these two numbers as one measurement
that got better and not as a change of subject.

### The `ca` suite alone

The whole suite takes about 25 minutes, and one suite takes about two. This
is the shorter loop, and the numbers are for `--suite ca`.

| run     | OK  | FAIL | SKIP |
| ------- | --- | ---- | ---- |
| control | 19  | 1    | 4    |
| pynixd  | 18  | 2    | 4    |

`ca:recursive` fails in both, so the one regression left is `new-build-cmd`.

### The same suite against Nix 2.35: 17 regressions

`--nix-version nix_2_35`, same checkout, same suite, one run of each arm:

| run     | OK  | FAIL | SKIP |
| ------- | --- | ---- | ---- |
| control | 19  | 1    | 4    |
| pynixd  | 2   | 19   | 3    |

The control is the same as at 2.34: Nix has no trouble with itself. pynixd
answers **2 of 24**, and the two that pass are
`derivation-advanced-attributes` and `derivation-json`, neither of which
builds anything. Every test that touches a realisation fails, with one
message:

```
error: the daemon is missing the 'realisation-with-path-not-hash' protocol
feature, needed to support content-addressing derivations
```

**That was issue #162, measured.** The protocol number stopped at 1.38 and
each new capability is a feature name. 2.34 offers none of them, so every
measurement of this suite before that one was taken against the one version
that cannot see the gap.

### After the claim: 4 regressions

pynixd names `realisation-with-path-not-hash` now, and every request that the
feature touches carries both shapes with a gate that picks one. Same command,
same checkout:

| run     | OK | FAIL | SKIP | regressions |
| ------- | -- | ---- | ---- | ----------- |
| before  | 2  | 19   | 3    | 17          |
| after   | 15 | 5    | 4    | 4           |

The control answers 19 OK / 1 FAIL / 4 SKIP at both.

The four left are `ca:build`, `ca:build-cache`, `ca:issue-13247` and
`ca:new-build-cmd`. The last one is a regression against 2.34 as well, and
issue #196 holds that one.

**The floor did not move.** The same suite against 2.34 answers control
19/1/4 and pynixd 18/2/4 after the claim, which is the number above it. Run
both arms after a change to a codec: 2.34 says the floor holds, and 2.35 says
the feature works.

### What moved, and what moved it

| correction | measured |
| ---------- | -------- |
| #195, the empty output path off the wire | `ca:build-cache` and `ca:issue-13247` stopped dropping the connection, and each moved to the behaviour the crash was hiding |
| #197, the options of the client on a path that is added | `ca:signatures` FAIL to OK |
| #196, a cap for `max-jobs`, and one replay for each client | every derivation is built once, and the log of a build reaches a client once |
| do not build a derivation whose input failed | `resolved_derivation_not_stored` gone from `main:build` |
| #198, the daemon substitutes a content-addressed output | `ca:issue-13247` FAIL to OK |
| #187, the plan sees a realisation that a substituter holds | `ca:build-cache` FAIL to OK |

Removing the layout patch of #176 moved `nix-channel` from FAIL to OK in the
control run, so that patch was breaking a test of Nix on its own.
`nested-sandboxing` still fails, and its cause is not the layout.

The count was 40 before issues #178, #179, #180, #182, #183, #184 and #185.
Issues #174 and #175 then took it from 29 to 9, in five steps: 29, 19, 15,
11, 9. Each one is below.

`compare` reports no "other change" now. Three tests moved from SKIP to FAIL
in an earlier run -- `local-overlay-store:delete-duplicate`,
`local-overlay-store:stale-file-handle` and
`main:multiple-outputs-substitute-failure` -- and all three skip again, in
both runs. Issue #186 holds the first two, and the cause is not proven: the
run that reported them differs from this one in the corrections of #174 and
#175, and in nothing that touches the shape of a store.

### A regression that stays

`main:store-info` reads the version out of `nix store info`, and it greps for
`Version: 2.34.8`. pynixd answers `Version: pynixd-0.1.0`, because it is not
Nix and it says so in the handshake. `wirelog compare` holds the same
difference as the `handshake.nix_version` exemption, and that exemption is on
purpose.

So this test counts as a regression in every run, and no correction is
planned. The GC tests above are the other permanent group.

### A log line that stays different

`nix-build` on a dynamic derivation writes the warning "Ignoring dynamic
derivation ... while querying missing paths" **twice** with `nix-daemon`, and
**once** with pynixd. Nothing else of that command differs.

Nix walks the request twice. The client calls `printMissing` at
`shared.cc:57`, which is one `QueryMissing` on the wire, and `Worker::run` at
`worker.cc:340` calls `store.queryMissing` again inside the daemon to warm
the cache of the substituters. The second walk answers the same question, and
each walk writes the warning. pynixd answers the request and runs no worker,
so it walks once.

**pynixd keeps one walk.** A second walk of the closure buys one log line, and
pynixd already holds the answer in its substitution queue. Writing the line a
second time without the walk would be a line that no work produced. The
marker at `pynixd/goals/query_missing.py:209` names the defect of Nix, and
issue #191 tracks the list of such markers.

No script of the suite reads this line, so no test moves either way. Issue
#189 holds the measurement.

### The 20 control failures

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
- **7 others** — `structured-attrs` (it wants a flake registry), `shell`,
  `formatter`, `nix-profile`, `json`, `tarball` and `fetchurl`.

`chroot-store` and `nested-sandboxing` were on that list and are not any
more. Both build inside a sandbox, and both failed because the suite had no
shell in the store to build with.

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

### The same test, and four more defects

`main:build` reached line 167 after those three, and it reaches line 247 now.
Issue #196 holds the four, and each one is a decision of the root-goal loop
that Nix takes differently:

1. **A substituter counted as a builder.** `_build_slots` returned the number
   of goals when any store was not the local one, so `max-jobs` reached the
   loop in almost no configuration. Almost every configuration holds a binary
   cache. The instrumented line reads `slots=4, goals=4, max_build_jobs=1,
   stores=["http-cache.nixos.org", "local"]`.
2. **The goals ran in the order of the request.**
   `DerivationBuildingGoal::key()` at `derivation-building-goal.cc:54` builds
   `"dd$" + name + "$" + path`, and `goal.hh:604` states the rule: `aardvark`
   runs before `baboon`. The client sends the four derivations of
   `fod-failing.nix` in store-path order, so pynixd built x3 where the test
   asserts x1.
3. **"2 dependencies failed" where Nix says 1.** `Goal::amDone` at
   `goal.cc:242` drops every waitee that is left as soon as one fails and
   `keep-going` is off, so the number is 1 whatever number would have failed.
4. **A goal that the request left behind was still awaited, and reported.**
   `Worker::run` leaves its loop when `topGoals` is empty, so that goal never
   reaches `amDone` and `entry-points.cc:93` skips it.

### A harness defect that hid a whole fixture

`tests/functional/meson.build:17` runs `find_program('bash')` and writes the
answer into `config.nix` as `shell`. Every `mkDerivation` of the suite uses
that value as its builder. The runner named no bash in `runtimeInputs`, so
the first one on PATH was `/run/current-system/sw/bin/bash` of the builder
machine, and no sandbox can reach that path.

`cancelled-builds` of `main:build` was therefore degenerate **in both arms**.
`slow` must sleep 10 s so that `fast-fail` can fail while it runs; with no
shell it died at once with "executing '/run/current-system/sw/bin/bash': No
such file or directory", and the test asked which of two instant failures
landed first. The control arm won that race by milliseconds, so the fixture
reported nothing about either daemon.

This is the second time the harness, and not pynixd, decided what a test
said. The `$HOME` table above is the first. Read a control failure as a
question about the harness before you read it as a defect of Nix.

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
