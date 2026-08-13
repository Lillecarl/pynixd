# 2A: Deduplicate deferred output resolution

**Severity**: Medium (Code Duplication)
**Category**: Maintainability

## Problem
Both `resolve_derivation()` and `resolve_dynamic_derivation()` in
`pynixd/derivation_resolution.py` end with identical code that:
1. Creates a `resolved` BasicDerivation with outputs + input_srcs + rewrites
2. Computes `_hash_derivation_modulo(resolved, mask_outputs=True)`
3. Converts Deferred outputs `("", "", "")` to InputAddressed via `_make_output_path`
4. Updates `resolved.env[name]` with the new output path
5. Sets `resolved.outputs = new_outputs`

This is ~15 lines of exact duplication between two ~90-line functions.

## Fix
Extract a `_resolve_deferred_outputs(resolved: BasicDerivation, drv_name: str) -> BasicDerivation`
helper that takes a partially-resolved derivation and converts Deferred outputs.

## Files
- `pynixd/derivation_resolution.py`
