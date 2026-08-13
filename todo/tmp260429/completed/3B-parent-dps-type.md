# 3B: Add type argument to parent_dps parameter

**Severity**: Low (Type Safety)
**Category**: Type Safety & Annotations

## Problem
In `trampoline.py:_fire_trampoline()`, `parent_dps: set` uses bare `set` generic
without type argument. Should be `set[DerivedPath]`.

## Fix
Change `parent_dps: set` to `parent_dps: set[DerivedPath]`.

## Files
- `pynixd/trampoline.py` — `_fire_trampoline()`
