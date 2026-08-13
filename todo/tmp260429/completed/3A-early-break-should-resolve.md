# 3A: Add early break in _should_resolve loop

**Severity**: Low (Micro-optimization)
**Category**: Type Safety & Style

## Problem
In `derivation_resolver.py:_should_resolve()`, once `has_resolve_trigger` is set to
`True`, the loop over output_kinds() continues unnecessarily. It doesn't change
correctness but wastes cycles.

## Fix
Add `break` after setting `has_resolve_trigger = True`.

## Files
- `pynixd/derivation_resolver.py` — `_should_resolve()`
