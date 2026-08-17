# Build re-entrancy: delegating the goal system to a real Nix

Sources read, all citations below name the function beside the line number:

| Tree | Version | Role |
|---|---|---|
| `~/Code/nix_r` | 2.36.0 | primary read |
| `~/Code/nix-master` | 2.35.0 | cross-check |
| `/nix/store/2ijv0g60…-source` | 2.34.8 | the floor (`nixVersions.nix_2_34.src`) |
| installed `nix` | 2.34.8 | what the measurement below ran against |
| `~/Code/nix` | `351bc2449`, 2026-08-03 | upstream HEAD, read for Facts 9 to 11 |

Claims are tagged **[measured]**, **[read]**, or **[inferred]**. Nothing here is
from memory.

## The goal

Stop reimplementing Nix's goal graph in `pynixd/goals/`. Instead hand a real Nix
a `BuildPathsWithResults` request with `--max-jobs 0` and a `builders` entry
pointing back at pynixd, so pynixd receives *atomic* `BuildDerivation` units it
can distribute across a fleet without reasoning about dependencies,
substitution, CA resolution or dynamic derivations.

## The invariant

Everything below reduces to one rule:

> **Only the process holding the output-path lock, or a child of it, may write
> that output path.**

pynixd holds no locks of its own, so the hazard exists at exactly one moment:
when pynixd forwards a *write* to a store while an inner Nix holds the lock.

## Fact 1 — output locks are held for the whole build hook

`DerivationBuildingGoal::tryToBuild` (2.36 `derivation-building-goal.cc:308`)
acquires `PathLocks` on the output paths in its `acquireResources` lambda
(`:418`) *before* calling `tryBuildHook`, and releases them only after the hook
exits — success at `:804`, failure at `:754`.

The primitive is `flock`, in `lockFile` (`unix/pathlocks.cc:38`) via
`PathLocks::lockPaths` (`:71`). `flock` is per *open file description*, so a
second `open()` conflicts even inside the same process.

**[measured]** on 2.34.8 with a probe hook that accepts and then tests the lock:

```
parent_cmd=nix-daemon    euid=0
lockfile=/nix/store/cqal7caw…-reent-probe-….lock
lockfile_exists=yes
RESULT=lock_HELD_by_parent
```

So on a normal multi-user install the goal system runs *inside `nix-daemon`*,
which is a `LocalStore`, and any independent writer of that output path blocks.
A naive `--builders` loop back to the same store deadlocks rather than erroring.

## Fact 2 — the lock is conditional on the store type, and upstream says why

**[read]**, identical in 2.34.8 (`:382`), 2.35.0 (`:451`), 2.36.0 (`:398`), all
inside `DerivationBuildingGoal::tryToBuild`:

```c
std::set<std::filesystem::path> lockFiles;
/* FIXME: Should lock something like the drv itself so we don't build same CA drv concurrently */
if (auto * localStore = dynamic_cast<LocalStore *>(&worker.store)) {
    /* If we aren't a local store, we might need to use the local store as
       a build remote, but that would cause a deadlock. */
    /* FIXME: Make it so we can use ourselves as a build remote even if we
       are the local store (separate locking for building vs scheduling? */
    /* FIXME: find some way to lock for scheduling for the other stores so
       a forking daemon with --store still won't farm out redundant builds. */
```

**If the scheduling store is not a `LocalStore`, `lockFiles` stays empty and no
lock is ever taken.** Upstream wrote this comment for precisely our use case.

The stated price is in the third FIXME: no cross-process build dedup. So
`BuildQueue` dedup keyed by `.drv` stops being an optimisation and becomes
load-bearing.

Relevant hierarchy **[read]**: `LocalStore : IndirectRootStore : LocalFSStore`
(`local-store.hh:182`), and `UDSRemoteStore : IndirectRootStore, RemoteStore`
(`uds-remote-store.hh:62`). A daemon-backed store **is** a `LocalFSStore` but is
**not** a `LocalStore` — which is what makes it lock-free as a scheduler, while
still satisfying the `LocalFSStore` casts elsewhere in the goal.

## Fact 3 — `locksHeld` is the sanctioned escape, and it is narrow

`LocalStore::locksHeld` (`local-store.hh:258`) is a public `PathSet` consulted
in exactly one place: `LocalStore::addToStore` (`local-store.cc:1075`, check at
`:1092`) skips `PathLocks` for paths listed there. `nix __build-remote` sets it
before copying outputs back (`build-remote.cc:417-419`, commented
`/* FIXME: ugly */`).

**The other locking write path does not honour it**: `LocalStore::addToStoreFromDump`
(`local-store.cc:1192`) locks unconditionally at `:1313`. So the only safe
in-lock ingest call is `addToStore(ValidPathInfo, Source)`.

## Fact 4 — running the goal machinery over a non-`LocalStore` needs the C++ API

No CLI can do it, in any version **[read]**:

- 2.36: `daemon::processConnection` takes the builder from `store->getBuilder()`
  (`daemon.cc:1136`); `RemoteStore::getBuilder` returns a forwarding
  `RemoteBuilder` (`remote-store.cc:741`). `Store::buildPaths` no longer exists.
- 2.34: `Store::buildPathsWithResults` is virtual with a Worker-based default
  (`entry-points.cc:48`) and `RemoteStore` overrides it (`remote-store.hh:113`).

So `nix daemon --stdio --store daemon` relays builds upstream and runs no goals;
the upstream daemon's own `max-jobs`/`builders` decide. That topology is a trap.

The C++ entry points, which nanopynix can reach:

| Version | Call |
|---|---|
| 2.36 | `make_ref<LocalBuilder>(store, evalStore)->buildPathsWithResults(reqs, mode)` — `Store::getBuilder` default (`store-api.cc:151`), `LocalBuilder` in `build/worker.hh:80` |
| 2.34 | qualified call bypassing virtual dispatch: `store->Store::buildPathsWithResults(reqs, mode, evalStore)` |

`LocalBuilder::getWorker` (`worker.hh:96`) constructs a fresh `Worker` per call,
commented *"to avoid reusing a worker between calls, allowing for thread
safety"* — so concurrent pynixd requests are naturally isolated.

Caveat **[read]**: `Worker::Worker` binds `settings(nix::settings.getWorkerSettings())`
(`worker.cc:30`), so `max-jobs` and `builders` are **process-global**, not
per-request. In-process scheduling cannot vary them per client.

## Fact 5 — prior art: Nix already does this to itself

Recursive Nix serves a nested daemon connection over the same store while the
parent `Worker` is blocked, using a fresh `Worker`
(`derivation-building-goal.cc:905-917`):

> *"We create a fresh Worker here because the parent Worker is blocked waiting
> for the current build to finish… Ideally we should reuse the same Worker to
> share scheduling state."*

Two Workers over one store in one process is a supported shape, and upstream's
own answer to sharing scheduling state is: don't.

## Fact 6 — the build hook protocol

**[measured]** — a shell script implementing this got accepted and logged
`building '…drv' on 'probe://fake'`. Layout from `HookInstance::HookInstance`
(`unix/build/hook-instance.cc:36-88`):

| Channel | Direction | Content |
|---|---|---|
| stdin (fd 0) | in | settings (`1,name,value`… terminated by `0`), then per attempt: `try`, amWilling, system, drvPath, requiredFeatures; after accept: inputPaths, wantedOutputs |
| stderr (fd 2) | out | control: `# accept` / `# decline` / `# decline-permanently` / `# postpone`, then one line of machine name; other lines are relayed as log |
| fd 4 | out | build log |
| fd 5 | in | read side of the log pipe (hack for ssh stderr) |

Selection in `DerivationBuildingGoal::tryBuildHook` (`:1165`) requires only that
`build-hook` is non-empty and the drv is valid at `:1172` — **`builders` is not
consulted**, so a custom hook needs no machines file. `amWilling` is
`getNrLocalBuilds() < maxBuildJobs` (`:1182`), i.e. 0 under `--max-jobs 0`.

After the hook exits, `DerivationBuildingGoal::buildWithHook` (`:591`) re-checks
validity and throws `"some outputs are unexpectedly invalid"` (`:775-777`) if
the outputs are not registered. That message is the failure mode for every
"ingest didn't land" bug.

## Fact 7 — what stock `nix __build-remote` does

`main_build_remote` (`src/nix/build-remote/build-remote.cc`):

- `:331` — **if the remote reports untrusted and the drv is not CA, it falls
  back to `copyClosure` + `buildPathsWithResults` at `:378`.** pynixd must
  report **trusted** in the handshake (`RemoteStore::isTrustedClient`,
  `remote-store.cc:889`, reads `conn->remoteTrustsUs`) or the hook recurses into
  pynixd's own scheduler. This is the single sharpest trap in the stock path.
- `:337-362` — the `BasicDerivation` sent to us has its `inputs` replaced by the
  full input closure. It is self-contained; we never read the `.drv`.
- `:309` / `:420` — inputs copied in, outputs copied back, both `NoCheckSigs`.
- `:389-405` — for CA, every wanted output must appear in `builtOutputs` or the
  `assert` at `:399` aborts the hook; realisations then registered at `:427`.
- `:281` — one exclusive `<uri>.upload-lock` per builder URI, serialising input
  copies across concurrent hooks. `openSlotLock` (`:42`) in
  `<stateDir>/current-load` is the only concurrency cap, so the machine's
  `maxJobs` field governs how many hooks exist at once.
- `Machine::systemSupported` (`machines.cc:39`) returns true for `"builtin"`
  unconditionally — so `builtin:fetchurl` derivations are shipped to us too.
- Feature routing is `Machine::allSupported` (`machines.cc:44`); with
  `--max-jobs 0` there is no local fallback, so a missing feature is a hard
  failure.

Daemon-side, `BuildDerivation` refuses input-addressed drvs from untrusted
clients (`daemon.cc:625-676`, long comment on the trust model).

## Fact 8 — `external-builders`: the minimal-ingest option

Present as far back as **2.34** (`local-settings.hh:652`,
`unix/build/external-derivation-builder.cc`), experimental feature
`external-builders`.

`ExternalDerivationBuilder::startChild` writes a JSON file with `builder`,
`args`, `env`, `inputPaths`, `outputs`, `realStoreDir`, `storeDir`, `system`,
`tmpDir`, and execs our program with its path. We materialise the output
directories and exit; **Nix does registration, reference scanning,
canonicalisation, CA hashing and realisation creation itself**, because the
class inherits `DerivationBuilderImpl`. stdout/stderr are already the build-log
pty (`openSlave()`). It runs as the build user (`setUser()`), unsandboxed.

Three structural catches:

1. Requires a `LocalStore` — it is selected inside the local-build branch of
   `DerivationBuildingGoal::tryToBuild` (`:344-382`), after the
   `dynamic_cast<LocalStore *>` at `:347`.
2. **It is chosen before the `maxJobsZero` / platform / feature checks**
   (`if (ext) return LocalBuildCapability{...}` at `:357`), so it silently
   overrides `--max-jobs 0` and foreign-platform rejection. But
   `DerivationBuildingGoal::buildLocally` then gates on
   `curBuilds >= maxBuildJobs` (`:857`) — so with `max-jobs 0` the goal parks on
   `waitForBuildSlot` for a slot that never opens. **[inferred, not measured]**
   Concurrency is therefore `max-jobs`, and each build also consumes a `nixbld`
   user.
3. Dispatch is by `drv.platform` alone
   (`LocalSettings::findExternalDerivationBuilderIfSupported`, `globals.cc:283`);
   the JSON carries **no drv path** and no `requiredSystemFeatures`. Fleet dedup
   cannot be keyed on the `.drv`, and for floating CA the `outputs` map holds
   *scratch* paths, not final ones.

The hook is tried before the local build unless `preferLocalBuild`
(`:568-583`), so the two compose: hook as the routed lane, external builder as
the fallback lane.

The JSON stays at `"version": 1` at HEAD **[read]** (`local-settings.hh:711`).
It carries no RPC. Fact 9 is the separate feature that does.

## Fact 9 — `builder-rpc-v0` inverts the ingest direction

New since the rest of this note. `55eea4554`, Artemis Tosini, 2025-11-17,
"Implement new builder-rpc-v0 derivation feature". It is in HEAD **[read]**.
The manual section is `doc/manual/source/store/building.md:225-270`.

A derivation asks for it by putting `builder-rpc-v0` in
`requiredSystemFeatures`. Nix then gives the builder a **restricted daemon
socket** and **no output paths in the environment**. The builder submits each
output itself, with `nix store add` and `nix store submit-output`, or by
speaking the protocol.

Three properties matter to pynixd:

- **Every output is content-addressed.** The IPC path supports no
  input-addressed output.
- **Order is the contract.** Reference scanning covers the inputs and every
  store object added so far, so the builder must add `foo` before a `bar` that
  refers to it. Nix returns each computed store path in the response.
- **Self-references are not supported yet**, and the manual says why: only
  rewriting can resolve them, and the point of the IPC path is to stop Nix
  rewriting opaque data.

Two new wire operations carry it (`worker-protocol.hh:275-279`):
`SubmitOutput = 1000` and `AddToStoreScanning = 1001`. Both sit above the
normal opcode range on purpose. A new serialiser for `SingleDerivedPath`
arrives with them (`:349`). `SubmitStore` (`submit-store.hh`) is the store
interface, and `nix store submit-output` (`src/nix/store-submit-output.cc`) is
the command, gated on `Xp::DynamicDerivations`.

`DerivationBuildingGoal::buildLocally` now passes a `daemon::RecursiveFlag`
into `processDaemonConnection` instead of the constant `daemon::Recursive`, so
the restricted daemon serves two different surfaces from one call site.

**This is a second re-entrancy direction, and it points the other way.** The
rest of this note is about pynixd calling *out* to a Nix goal system. This one
is a build calling *in* to a daemon that pynixd may be proxying. A pynixd that
proxies a builder-rpc-v0 build must pass these operations through.

## Fact 10 — the protocol version is a pair now, and pynixd models it as an int

**[read]** `worker-protocol.cc:20-54`. `WorkerProto::Version` is
`{number: {major, minor}, features: set<string>}`. `operator<=>` (`:57`)
compares the number and then requires the feature set to be a subset, so it
returns `partial_ordering` and two versions can be unordered.

The three constants:

| Constant | Number | Features |
|---|---|---|
| `latest` | 1.38 | `realisation-with-path-not-hash`, `delete-dead-specific-referrers` |
| `minimum` | 1.18 | none |
| `builderRpcV0` | 1.38 | `realisation-with-path-not-hash`, `disable-set-options`, `add-to-store-scanning`, `submit-output` |

Read again at `origin/master` `4401a297c`, 2026-08-16. This table gave
`latest` a third feature, `build-result-memory`. That name is in no file of
`src/`, and `worker-protocol.cc:26-30` gives `latest` the two above. The
correction is here rather than in a new fact, because a wrong row is worse
than a missing one.

`builderRpcV0` is pinned, and the comment at `worker-protocol.hh:122-125` says
why: *"Should never change, as any modification would be derivation-visible."*

**`builderRpcV0` is not the recursive-nix connection.** It serves the
`builder-rpc-v0` derivation feature of Fact 9, and that surface starts no
build. `processDaemonConnection` takes a `daemon::RecursiveFlag` and serves
both from one call site, which is what makes the two easy to confuse. Nix's
own comment at `worker-protocol.hh:140` names recursive-nix for
`disable-set-options`, and both surfaces take that feature for the same
reason: a builder cannot change the settings of the daemon that serves it.

The five feature names are at `worker-protocol.hh:132-152`.

`nix-daemon-protocol` holds `PROTOCOL_VERSION = proto(1, 38)` as one integer
(`constants.py:20`), and `SUPPORTED_PROTOCOL_VERSIONS` is a range of minors
(`:22`). **A bare 1.38 is no longer the whole handshake.** Two peers can both
say 1.38 and still disagree on `submit-output`. The AGENTS.md line "pynixd will
advertise 1.38 support" is now under-specified.

## Fact 11 — upstream started the split this note asks for

**[read]** `4125ece6c`, John Ericson, 2026-03-20, "Separate building/scheduling
from storage". It is in HEAD. The message calls it *"the major first step of
#5025"*.

It pulls the build methods out of `Store` into a new `Builder` class, and lets
some stores give a `Builder`. `Worker` implements `Builder`, so a `Worker` can
own or borrow stores and be one scheduler, rather than each `Store` method
making a `Worker` behind the scenes.

Issue #5025 is *"use the store interface for `--builders`"*. Its end state
deletes the build hook: a `--builders` entry becomes a plain `Store` that the
local `Worker` drives. That is the seam this whole note is trying to build by
hand.

Fact 4 records the consequence that already landed: 2.36 takes the builder from
`store->getBuilder()` and `Store::buildPaths` is gone.

## Ruled out

- **`local-overlay` store** as a way to give the inner Nix a harmless place to
  write: the lower store directory "must not change at all" while mounted
  (`local-overlay-store.md`), which a live cache violates by definition.
- **`nix daemon --stdio --store daemon`**: relays, runs no goals (Fact 4).
- **pynixd as a substituter** instead of a builder: re-entrancy evaporates since
  substitution holds no long-lived lock, but `queryPathInfo` becomes a
  synchronous fleet build and we lose `BuildResult` and build logs.

## The decision that collapses the option matrix

**Does pynixd run as root?**

- **Root** — pynixd can hold its own `LocalStore` handle, seed `locksHeld`, and
  write directly. Every option is open, including `external-builders`.
- **Not root** — writes go through the real daemon, which takes the lock
  (`local-store.cc:1085`). The scheduling store **must not** be a `LocalStore`,
  and `external-builders` is unavailable.

## Topologies

| | Scheduler runs on | Locks held | Who ingests | Needs |
|---|---|---|---|---|
| **A** | spawned `nix daemon --stdio`, `LocalStore` | yes | the hook must; pynixd must **not** register outputs | root |
| **B** | in-process Worker over `UDSRemoteStore` | **no** | either | nanopynix |
| **C** | `LocalStore` + `external-builders` | yes (we are the child) | Nix itself | root, Xp feature |

Under **A**, pynixd's nested endpoint degenerates to a virtual builder that
proxies one build to a chosen fleet machine — `BuildDerivation`,
`QueryValidPaths`, `QueryPathInfo`, `NarFromPath` — and `copyPaths`
(`build-remote.cc:420`) populates the cache for us. The `OutputPuller` in
`architecture.md` disappears.

Under **B**, no lock is ever held, so pynixd may register outputs itself and the
existing puller stays. This is the topology nanopynix unlocks and it is the only
one that works unprivileged.

## Open questions

1. **Custom hook vs stock `nix __build-remote`.** A custom `build-hook` drops
   the daemon-protocol callback, the trust negotiation, the upload lock and the
   slot locks, and puts ingest in a process that is a child of the lock holder
   by construction. Cost: we own reference metadata, CA realisation
   registration, and `postpone`/`decline` backpressure — the last of which is
   also the only real queueing primitive Nix offers a hook.
2. **Fix the FIXME upstream.** `derivation-building-goal.cc:399-406` asks for
   "separate locking for building vs scheduling" and names our deadlock. A
   `scheduler-only` store param, or exposing `locksHeld` as a store setting,
   would make the topologies above stop being workarounds.

   **Partly answered: see Fact 11.** Upstream is doing the larger version of
   this in #5025, and the first step is in HEAD. The remaining question is
   whether to wait, or to add the narrow `scheduler-only` param now. Waiting
   costs nothing if topology B is correct, because topology B takes no lock in
   the first place. Decide this after the Fact 2 measurement.
3. **Stale negative path-info cache.** `Store::queryPathInfo`
   (`store-api.cc:636`) caches negatives for `narinfo-cache-negative-ttl`
   (default 3600s); `Store::isValidPath` (`:541`) reads that cache but never
   writes negatives to it. Whether a scheduler can observe a stale negative and
   trip "some outputs are unexpectedly invalid" is **unverified**. Cheap
   insurance: `path-info-cache-size = 0` on the scheduling store — which is what
   `runDaemon` already does for daemons (`nix/unix/daemon.cc:483`).
4. **Suspected inversion in 2.36 `nix daemon --stdio`.**
   `nix/unix/daemon.cc:497` reads
   `processOps |= !forceTrustClientOpt || *forceTrustClientOpt != NotTrusted;`
   while the comment two lines above says the opposite — as written,
   `--force-untrusted` is the *only* case that forwards. Not verified against
   upstream master; pass `--process-ops` explicitly regardless.

5. **Model the feature set in `nix-daemon-protocol`.** Fact 10 shows one
   integer is no longer the version. This is independent of the delegation
   decision: a pynixd that proxies any recent Nix needs it either way, and it
   is a prerequisite for proxying a `builder-rpc-v0` build. Size it first —
   `Version` becomes a pair, and `SUPPORTED_PROTOCOL_VERSIONS` becomes a
   negotiation rather than a membership test.

## Reproducing the measurement

The probe hook lives in this session's scratchpad; it is ~20 lines of bash that
writes `# accept\n<name>\n` to stderr, sleeps, then `flock -n` tests
`<out>.lock`. Run it against a **private chroot store**, never the machine's
shared daemon — a `--builders` loop back to the same store wedges the daemon
instead of erroring.
