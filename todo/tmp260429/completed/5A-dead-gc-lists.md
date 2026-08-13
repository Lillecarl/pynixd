# 5A: Remove dead coroutine list building in gc.py

**Severity**: Low (Dead Code)
**Category**: Cleanup

## Problem
In `garbage_collector.py:run_gc_pass()`, a `tasks` list is populated with
`self.gc_store(...)` coroutine objects, then checked with `if not tasks: return`,
but the actual execution happens later via a separate loop inside `TaskGroup`.
The `tasks` list and `stores_for_tasks` list are dead code after the check.

## Fix
Remove the dead list building and simply check `if not self.stores and not local_stale: return`.

## Files
- `pynixd/gc.py` — `run_gc_pass()`
