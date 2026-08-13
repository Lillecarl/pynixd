# 4B: Use self.logs instead of creating new OperationLogs()

**Severity**: Low (Consistency)
**Category**: Style

## Problem
Some `OpResponse.from_reader()` methods do:
```python
self.logs = await OperationLogs().from_reader(...)
```
but `OpResponse` already provides `self.logs = OperationLogs()` via `default_factory`.
Re-creating it overwrites the existing instance. This is inconsistent — some
use `self.logs.from_reader(...)` and some recreate it.

## Fix
Replace `self.logs = await OperationLogs().from_reader(...)` with
`await self.logs.from_reader(...)` in all files. The `OpResponse.__init__` already
creates the logs instance.

Files to check:
- `add_build_log.py`
- `add_indirect_root.py`
- `add_temp_root.py`
- `add_to_store.py`
- `add_to_store_nar.py`
- `ca_derivations.py` (RegisterDrvOutputResponse, QueryRealisationResponse)
- `collect_garbage.py`
- `is_valid_path.py`
- `nar_from_path.py`
- `query_closure_with_info.py`
- `query_derivation_output_map.py`
- `query_path_info.py`
- `query_path_infos.py`
