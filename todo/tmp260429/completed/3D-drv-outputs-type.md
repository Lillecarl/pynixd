# 3D: Add type argument to drv_outputs parameter

**Severity**: Low (Type Safety)
**Category**: Type Safety & Annotations

## Problem
In `trampoline.py:_should_trampoline()`, `drv_outputs: dict` uses bare `dict` generic
without type arguments. Should be `dict[str, Realisation]`.

But `Realisation` is already a TypedDict in `types/ca.py`.

## Fix
Change `drv_outputs: dict` to `drv_outputs: dict[str, Realisation]`.

## Files
- `pynixd/trampoline.py` — `_should_trampoline()`
