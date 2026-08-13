# 05 — Resolve wrapper derivations with dynamic inputDrvs

**Status**: COMPLETED  
**Depends on**: 01 (DerivedPath), 02 (text-hashed resolution), 03 (trampoline), 04 (DAG linking)  

## Problem

The `wrapper` derivation uses `DrvWithVersion("xp-dyn-drv",...)` format. Its `inputDrvs` have nested structure:
```
[("producingDrv.drv", ([], [("out", ["out"])]))]
```

The wrapper's `out` env variable is a **DownstreamPlaceholder**:
```
env["out"] = "/11q8m77b1abq9lpb9x7d57dcj389449a7vfrarhznkgfh51wfy8d"
```

Before building the wrapper, these placeholders must be resolved to actual store paths.

## Implementation

### New functions in `derivation_resolution.py`

1. **`downstream_placeholder_unknown_derivation(parent_placeholder_hash, output_name)`** — implements `DownstreamPlaceholder::unknownDerivation()`. Computes nested placeholder by hashing `nix-computed-output:{compressed_parent_hash}:{output_name}`.

2. **`downstream_placeholder_from_chain(chain)`** — recursively computes placeholder from a `[(drv, output), ...]` chain. First element uses `unknownCaOutput`, subsequent elements use `unknownDerivation`.

3. **`resolve_dynamic_derivation(drv, drv_path, dynamic_output_paths)`** — resolves a DrvWithVersion wrapper derivation. Takes `dynamic_output_paths: dict[tuple[StorePath, str, str], StorePath]` mapping `(outer_drv, outer_output, inner_output)` to actual store paths. Computes level-1 and level-2+ placeholders, builds rewrite map, applies rewrites, and derives output paths.

### Scheduler changes (`scheduler.py`)

1. **`_resolve_dynamic_derivation(build, store)`** — called in `execute_build()` alongside `_resolve_deferred_derivation()`. Scans dep builds for CA realisations, builds `dynamic_output_paths` from the inner and outer builds, calls `resolve_dynamic_derivation()`, writes resolved .drv via AddToStore.

2. **Trampoline condition broadened** — `has_nested_dp or has_dynamic_dependent`. The `has_dynamic_dependent` flag checks if any queued build has `dynamic_input_drvs` referencing the current build's drv path. This triggers the trampoline when the client builds `wrapper!out` which implicitly needs `producingDrv` and `hello.drv` to be built first.

3. **Decomposition: dynamic_input_drvs expansion** — after QueryMissing, recursively adds `dynamic_input_drvs` targets to the build plan if their outputs aren't already available. This ensures `producingDrv` is built even though it's a "valid" path already in the store.

### ATerm escaping fix

`_unparse_basic_derivation()` was missing proper ATerm string escaping. Added `_aterm_escape()` that escapes `\`, `"`, `\n`, `\r`, `\t` — critical for correct `hashDerivationModulo` computation.

## Verification

- `test_dynamic_drv_wrapper_via_pynixd` — builds the `wrapper` derivation through pynixd, verifies output matches direct `nix build` output (`/nix/store/pvbc125xdkglhhalb9j4cgjjyis9nsb7-use-dynamic-drv`).
- All 15 CA tests pass, all 89 functional tests pass.