# 4A: Convert printf-style logging to kwargs

**Severity**: Low (Consistency)
**Category**: Style

## Problem
`pynixd/operations/build_paths.py` uses old-style `self.logger.debug("BuildPaths len(paths)=%d", len(...))`
while every other module uses structured keyword arguments like
`log.debug("msg", count=n)`.

## Fix
Convert the printf-style calls to kwargs:
`self.logger.debug("build_paths_count", count=len(self.derived_paths))`

## Files
- `pynixd/operations/build_paths.py`
