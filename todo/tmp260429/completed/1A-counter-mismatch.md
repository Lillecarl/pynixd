# 1A: Fix metrics counter mismatch in build_queue.py:fail()

**Severity**: High (Bug)
**Category**: Correctness

## Problem
`QueuedBuild::fail()` in `pynixd/build_queue.py` always decrements
`QUEUE_SIZE.labels(status="pending")`, but by the time `fail()` is called from
`execute_build()` crash paths, the build has already been transitioned from
"pending" to "building" during assignment in `schedule()`. This means the
"building" counter never decrements on crash, leaking the metric.

## Fix
Check the build state and decrement the correct label:
- If `build.is_building`: decrement "building"
- Otherwise: decrement "pending"

## Files
- `pynixd/build_queue.py` — `fail()` method
