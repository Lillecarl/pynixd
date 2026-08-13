# 03 — Dynamic derivation trampoline (build inner .drv from text-hashed output)

**Status**: Completed  
**Depends on**: 01 (DerivedPath), 02 (text-hashed resolution)  
**Priority**: High — this is the core "dynamic" behavior

## Problem

After building a text-hashed CA derivation (e.g., `producingDrv`), its output IS a `.drv` file. The next step is to **build that inner derivation** — this is the "trampoline" pattern from Nix's `DerivationTrampolineGoal`.

Previously pynixd had no mechanism to discover that a build output is a `.drv` file and automatically schedule its inner derivation for building.

## The trampoline flow

```
Client requests: BuildPaths([producingDrv.drv^out^out])
                                    │
                          ┌─────────┘
                          ▼
              1. Build producingDrv (text-hashed CA)
                          │
                          ▼
              2. Output is /nix/store/...-hello.drv
                          │
                          ▼
              3. _on_build_complete: detect .drv output + nested DerivedPath
                          │
                          ▼
              4. Parse inner .drv, enqueue BuildDerivation(hello.drv)
                          │
                          ▼
              5. Inner build completes → request resolves with hello's result
```

## Implementation: Scheduler-level trampoline with SchedulerBuildRequest

We chose Option A (scheduler-level), but refined it with a `SchedulerBuildRequest` that tracks the full lifecycle of a `build_derived_paths()` call. Individual `QueuedBuild`s complete normally (slot freed, future resolved with their own result). The trampoline is handled in `_on_build_complete()` which fires after `queue.complete()`.

### Key design

- **`SchedulerBuildRequest`** (in `build_queue.py`): tracks original DerivedPaths, active build IDs, result mapping (DerivedPath → BuildResult), and a future that resolves only when ALL transitive builds complete.
- **`QueuedBuild.scheduler_request_id`**: back-reference linking a build to its parent request. Set when enqueued via `build_derived_paths()`.
- **`_on_build_complete()`**: called after `queue.complete()` when the build belongs to a request. Detects `.drv` outputs from dynamic builds with nested DerivedPaths, parses inner .drv, enqueues inner builds.
- **`_on_build_complete_failed()`**: records failure result for all parent DerivedPaths.

### Trampoline condition

Trampoline only fires when ALL three are true:
1. `derivation.has_dynamic_outputs` (text-hashed CA)
2. Any parent `DerivedPath.is_nested` (e.g., `^out^out`)
3. Build succeeded and output IS a `.drv` file

This means `producingDrv^out` returns the `.drv` directly (no trampoline), while `producingDrv^out^out` triggers the trampoline to build the inner derivation.

### Nested DerivedPath handling

`QueryMissing` doesn't understand nested DerivedPaths (`a.drv^out^out`). Before sending to QueryMissing, nested paths are flattened to their outermost `.drv` (`a.drv`), since the inner chain is handled by the trampoline.

### Result mapping

- Non-trampolined builds: result recorded directly under parent DerivedPath.
- Trampolined builds: the outer build's result is skipped; the inner build inherits the same DerivedPaths. When the inner build completes, its result is recorded under those DerivedPaths.
- A derivation with both `.drv` and non-`.drv` outputs: non-`.drv` outputs are recorded immediately, `.drv` outputs are trampolined.

## Files changed

- **`pynixd/build_queue.py`**: `SchedulerBuildRequest` class, `QueuedBuild.scheduler_request_id`, `BuildQueue.create_request()`, `BuildQueue.enqueue()` with request linking.
- **`pynixd/scheduler.py`**: `build_derivation()` (renamed from `enqueue`), `build_derived_paths()` creates SchedulerBuildRequest and awaits its future, `_on_build_complete()` trampoline, `_on_build_complete_failed()`, nested DerivedPath flattening before QueryMissing.
- **`pynixd/operations/build_derivation.py`**: updated `scheduler.enqueue` → `scheduler.build_derivation`.
- **`pynixd/operations/build_paths.py`**: removed unused imports after decomposition was moved to scheduler.
- **`tests/functional/test_ca_ops.py`**: `test_dynamic_drv_trampoline` — builds `producingDrv^out^out` through pynixd, verifies final output is non-`.drv`.

## Verification

```bash
pytest tests/functional/test_ca_ops.py::test_dynamic_drv_trampoline -v
```