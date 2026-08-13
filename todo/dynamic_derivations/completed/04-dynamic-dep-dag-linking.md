# 04 — Dynamic dependency DAG linking

**Status**: Completed  
**Depends on**: 01 (DerivedPath), 03 (trampoline)  
**Priority**: High — without this, builds depending on dynamic outputs execute in wrong order

## Problem

`build_derived_paths()` builds DAG edges by walking `parsed.input_drvs`. But for `DrvWithVersion("xp-dyn-drv",...)` derivations, the relevant dependencies are in `parsed.dynamic_input_drvs`. Without walking them, the wrapper build has no DAG edges and gets scheduled before its dynamic deps are built.

## Implementation

### Decomposition-time linking

During `build_derived_paths()`, after the existing `input_drvs` DAG linking loop, a second loop walks `parsed.dynamic_input_drvs`. For each dynamic drv reference, add a `depends_on` edge to the outer build. Also store `dynamic_input_drvs` on the `QueuedBuild` for later use by the trampoline.

### Trampoline-time linking

When the trampoline fires in `_on_build_complete()` and enqueues the inner build, `_link_dynamic_deps()` scans all queued builds for ones that have `dynamic_input_drvs` referencing the outer build's drv path. For each match:
- Add `depends_on` edge to the inner build
- Add the inner build's output paths to `required_paths`

This is a two-phase approach: initial linking to the outer build (blocking the dependent until the outer completes), then retroactive linking to the inner build when it's created by the trampoline.

### required_paths update

Adding the inner build's output paths to dependent builds' `required_paths` ensures:
- The scheduler won't schedule the dependent until those paths are in the local store
- `execute_build()` will send those paths to the builder before running the build

## Files changed

- **`pynixd/build_queue.py`**: `QueuedBuild.dynamic_input_drvs` field
- **`pynixd/scheduler.py`**: DAG linking for `dynamic_input_drvs` in `build_derived_paths()`, `_link_dynamic_deps()` method called from trampoline

## Full verification

Requires task 05 (wrapper derivation resolution) to build a complete dynamic chain end-to-end. The DAG linking alone can be tested indirectly through the trampoline test.