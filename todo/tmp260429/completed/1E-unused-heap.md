# 1E: Remove unused heap in BuildQueue

**Severity**: Medium (Inefficiency)
**Category**: Cleanup

## Problem
In `build_queue.py`, `enqueue()` uses `heapq.heappush(self._queue, build)`, but
`get_pending()` does `sorted([...], key=lambda b: b.id)` which destroys heap order.
The heap invariant is maintained but never consumed — the sort makes the heap
useless.

## Fix
Replace `heapq.heappush` with `self._queue.append(build)` and remove the
`heapq` import. Builds are always sorted by ID when retrieved, so heap ordering
provides zero benefit.

## Files
- `pynixd/build_queue.py` — `enqueue()` and imports
