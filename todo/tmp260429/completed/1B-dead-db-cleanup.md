# 1B: Fix dead/broken DB cleanup in remove_store()

**Severity**: High (Bug)
**Category**: Correctness

## Problem
`Server.remove_store()` in `pynixd/instance.py` has a commented-out DB cleanup
section with the literal comment "I will skip the DB part for now until I
confirm the correct method name." No `PynixdKnownPaths` records are removed,
so stale path data lingers for removed stores forever.

`LocalStoreDB` already has constant `DELETE_STORE_KNOWN_PATHS` and the
method already does `store.tracker.remove_known_paths(...)` which could work.

## Fix
1. After removing from `ctx._stores`, query the DB to delete known paths
   for the removed store_id.
2. Use the `DELETE_STORE_KNOWN_PATHS` SQL constant already defined in
   `local_store_db.py`.

## Files
- `pynixd/instance.py` — `remove_store()` method
