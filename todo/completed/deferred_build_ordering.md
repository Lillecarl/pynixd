# Deferred Derivation Build Ordering

## IMPORTANT KEEP AFTER COMPACTION — NEXT RESEARCH TASK

**Next step**: Investigate how the Nix **client** builds CA derivations via `BuildDerivation` (op 19). The daemon-side code is understood (see below), but we need to know what the **client** sends. Specifically:

1. Read `~/Code/nix/src/nix-store/build-derivation.cc` or similar for the client-side `BuildDerivation` sender code
2. Check `~/Code/nix/src/libstore/daemon.cc` lines 584-660 for the full `BuildDerivation` handler (how the daemon processes the request)
3. **Key question**: Does the client include the `.drv` file path in `input_srcs` of the `BasicDerivation`? My (user's) hypothesis is that the `.drv` is added as an input_src so the daemon can read the full `Derivation` (with `inputDrvs`) from disk even when using `BuildDerivation` wire format
4. Check what `Store::buildDerivation` / `DerivationGoal::haveDerivation` does when the `.drv` IS on disk — does it read the full `.drv` to get `inputDrvs`?
5. Research `~/Code/nix/src/libstore/build/derivation-goal.cc` — the `haveDerivation` flow: how does it proceed when `hasKnownOutputPaths()` is false but the `.drv` file exists on disk?
6. If the `.drv` IS on disk, `queryPartialDerivationOutputMap` can read it via `readInvalidDerivation` — does the building flow use this?

**Where to begin**: `~/Code/nix/src/nix-store/build-derivation.cc` (client sender) → then `~/Code/nix/src/libstore/daemon.cc:584` (daemon handler) → then `~/Code/nix/src/libstore/build/derivation-goal.cc` (goal flow)

## Problem

When a deferred (non-CA) derivation depends on a CA derivation, building
through pynixd fails because the builder store doesn't have the CA
dependency's realisation registered before the deferred build starts.

The deferred derivation's `.drv` file has empty output path and empty
hash for the CA dependency — these can only be resolved at build time
by looking up the realisation. Without it, the builder can't compute
the deferred derivation's `$out` path, resulting in:

```
sh: can't create : nonexistent directory
```

## Root Cause (Wire Protocol Level)

`BuildDerivation` (op 19) sends a `BasicDerivation` over the wire which
has **no `inputDrvs`** — only `inputSrcs`. For deferred derivations:

1. `to_basic_derivation()` resolves `inputDrv` output paths → gets
   `StorePath("")` for CA floating deps → adds nothing to `inputSrcs`
2. The daemon receives a derivation with empty `inputDrvs` and empty
   `inputSrcs` (no CA output paths)
3. The daemon's resolution step (`shouldResolve()`) returns false because
   `inputDrvs` is empty
4. The daemon can't compute `$out` for the deferred derivation

When the root store test passes, it works because the client calls
`BuildPaths` (op 9) which sends `DerivedPaths`, and the daemon reads the
**full `.drv` file from disk** (with `inputDrvs` intact) and performs
proper resolution.

## What We've Implemented (and works)

### 1. DAG Dependency Tracking (`QueuedBuild.depends_on`)
- `QueuedBuild` now has `depends_on: set[int]`, `ca_realisations: list[dict]`, `assigned_store_id: str | None`
- `_decompose_build_paths()` links builds via `parsed.input_drvs` → `drv_to_build_id` mapping
- `BuildQueue.by_id` dict for looking up builds by ID
- `schedule()` skips builds whose `depends_on` contains unfinished builds

### 2. CA Realisation Registration on Builder Store
- `_register_dep_realisations()` sends `RegisterDrvOutput` to the builder store before the build
- The registration succeeds (bare basename format for `outPath` — NOT `/nix/store/` prefixed)

### 3. Deferred Input Patching (attempted, doesn't solve the core issue)
- `_patch_deferred_inputs()` adds resolved CA output paths to `input_srcs`
- This doesn't help because the daemon's resolution code needs `inputDrvs`, not `inputSrcs`

## Key Discovery: Daemon-Side BuildDerivation Flow

From researching `~/Code/nix/src/libstore/`:

- `daemon.cc:584-596`: Reads `BasicDerivation` from wire (no `inputDrvs`)
- `daemon.cc:642-653`: For untrusted clients, recomputes derivation path. For deferred (IA with `deferred=true`), only trusted clients can build
- `derivation-goal.cc:64-80`: `hasKnownOutputPaths()` returns false for deferred → requires `CaDerivations` feature
- `derivation-resolution-goal.cc`: `shouldResolve()` checks `inputDrvs.map.empty()` → returns false → no resolution
- Without resolution, the daemon computes `$out` via `hashDerivationModulo` on the empty-`inputDrvs` derivation, producing a **wrong** output path (or failing to compute at all)

## Test

`test_non_ca_depends_on_ca_via_pynixd` — currently xfail was removed, test fails.
`test_non_ca_depends_on_ca_root_store` — passes (uses root store directly).

## Key Files (pynixd)

- `pynixd/scheduler.py` — `execute_build()`, `_register_dep_realisations()`, `_patch_deferred_inputs()`
- `pynixd/build_queue.py` — `QueuedBuild` with DAG fields, `by_id`, `set_depends_on()`
- `pynixd/operations/build_paths.py` — `_decompose_build_paths()` with DAG linking
- `pynixd/operations/build_derivation.py` — `BuildDerivationRequest`