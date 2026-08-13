# 1D: Fix stale dynamic_input_drvs in _build_dynamic_output_paths

**Severity**: High (Bug)
**Category**: Correctness

## Problem
In `derivation_resolver.py:resolve()`, `parsed` is read fresh from disk,
`parsed.dynamic_input_drvs` is checked, then `_build_dynamic_output_paths()`
is called. But the method iterates `build.dynamic_input_drvs` (set during
decomposition, potentially stale) instead of the freshly-read
`parsed.dynamic_input_drvs`. If the .drv was updated between decomposition
and resolution, they'll diverge.

## Fix
Pass `parsed.dynamic_input_drvs` (from the freshly-parsed derivation) to
`_build_dynamic_output_paths()` instead of reading `build.dynamic_input_drvs`.
This requires adding a `dynamic_input_drvs` parameter to the method.

## Files
- `pynixd/derivation_resolver.py` — `resolve()` and `_build_dynamic_output_paths()`
