# 3C: Remove dead _holder_task attribute

**Severity**: Low (Dead Code)
**Category**: Cleanup

## Problem
`Store.__init__()` in `pynixd/store/base.py` declares `self._holder_task: asyncio.Task | None = None`
but this attribute is never read or written anywhere in the entire codebase.

## Fix
Remove the attribute.

## Files
- `pynixd/store/base.py` — `Store.__init__()`
