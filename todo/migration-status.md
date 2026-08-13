# from_reader/to_writer → deserialize/serialize Migration Status

## Goal
Remove all old `from_reader`/`to_writer` methods. The new `deserialize`/`serialize` methods with `ReadContext`/`WriteContext` are the replacement.

## Current State
- All types and operations have BOTH old and new methods
- Tests have been migrated to use new API
- `connection.py`, `proxy.py`, `store/transfer.py` all use new API
- All operation `handle` methods use new API
- **Old `from_reader`/`to_writer` methods still exist everywhere**
- Some cross-type calls within `deserialize`/`serialize` still reference old API (e.g., `UnkeyedValidPathInfo.from_reader` instead of `.deserialize`)

## What Was Learned (CRITICAL)
- Subagents CANNOT be trusted to do this migration — they introduce subtle bugs
- Must do SERIALLY, one file at a time, comparing old vs new side-by-side
- Must run `just precommit` after EACH file
- The `from_reader`/`to_writer` methods are the known-good reference
- Common bugs introduced:
  1. Calling `OtherType.from_reader(reader)` instead of `OtherType.deserialize(ctx)` within new methods
  2. Forgetting to `await` async deserialize calls
  3. Using `super().to_writer(writer)` instead of `super().serialize(ctx)` in new methods
  4. Accidentally removing methods that weren't supposed to be removed
  5. `ValidPathInfo.to_bytes()` still references old `to_writer` — needs update

## Files Already Cleaned
- `pynixd/types/path_info.py` — old methods removed, `to_bytes()` updated to use `serialize`

## Files Still Needing Old Method Removal (in order)

### Type files (no dependencies on other types' from_reader/to_writer)
1. `pynixd/types/build.py` — BuildResult, KeyedBuildResult
2. `pynixd/types/derivation.py` — BasicDerivation
3. `pynixd/types/path_info.py` — DONE

### Stderr (leaf type, but many call sites)
4. `pynixd/stderr.py` — OperationLogs, all StderrMsg subclasses

### Operation files (depend on types above)
5. `pynixd/operations/add_build_log.py`
6. `pynixd/operations/add_indirect_root.py`
7. `pynixd/operations/add_multiple_to_store.py` — COMPLEX: has `forward_stream` that reads/writes ValidPathInfo
8. `pynixd/operations/add_perm_root.py`
9. `pynixd/operations/add_signatures.py`
10. `pynixd/operations/add_temp_root.py`
11. `pynixd/operations/add_to_store.py`
12. `pynixd/operations/add_to_store_nar.py`
13. `pynixd/operations/build_derivation.py`
14. `pynixd/operations/build_paths.py`
15. `pynixd/operations/ca_derivations.py`
16. `pynixd/operations/collect_garbage.py`
17. `pynixd/operations/ensure_path.py`
18. `pynixd/operations/find_roots.py`
19. `pynixd/operations/is_valid_path.py`
20. `pynixd/operations/nar_from_path.py`
21. `pynixd/operations/optimise_store.py`
22. `pynixd/operations/probe_features.py`
23. `pynixd/operations/probe_systems.py`
24. `pynixd/operations/query_all_valid_paths.py`
25. `pynixd/operations/query_closure.py`
26. `pynixd/operations/query_closure_with_info.py`
27. `pynixd/operations/query_derivation_output_map.py`
28. `pynixd/operations/query_derivation_output_map_batch.py`
29. `pynixd/operations/query_missing.py`
30. `pynixd/operations/query_path_from_hash_part.py`
31. `pynixd/operations/query_path_info.py`
32. `pynixd/operations/query_path_infos.py`
33. `pynixd/operations/query_referrers.py`
34. `pynixd/operations/query_substitutable_paths.py`
35. `pynixd/operations/query_subst_path_info.py`
36. `pynixd/operations/query_subst_path_infos.py`
37. `pynixd/operations/query_valid_derivers.py`
38. `pynixd/operations/query_valid_paths.py`
39. `pynixd/operations/set_options.py`
40. `pynixd/operations/sign_path_info.py`
41. `pynixd/operations/verify_store.py`

### Base and infrastructure
42. `pynixd/operations/base.py` — remove abstract from_reader/to_writer declarations and dispatcher fallback

### Cross-type call sites to fix within deserialize/serialize bodies
- `ValidPathInfo.deserialize` calls `UnkeyedValidPathInfo.from_reader(ctx.reader)` → should call `UnkeyedValidPathInfo.deserialize(ctx)` (but this needs care — the ctx has version/client/buffer_logs that may not be appropriate for the inner call)
- `ValidPathInfo.serialize` calls `super().to_writer(ctx.writer)` → should call `super().serialize(ctx)`
- `AddMultipleToStoreRequest.forward_stream` uses `ValidPathInfo.from_reader(fsrc)` → needs `ValidPathInfo.deserialize(ReadContext(reader=fsrc, version=0))`
- `AddToStoreResponse.deserialize` calls `ValidPathInfo.from_reader(ctx.reader)` → `ValidPathInfo.deserialize(ctx)`
- `AddToStoreNarRequest.forward` uses old API for both read and write
- `BuildDerivationResponse.deserialize` calls `BuildResult.from_reader(ctx.reader, ctx.version)` → `BuildResult.deserialize(ctx)`
- `BuildDerivationRequest.deserialize` calls `BasicDerivation.from_reader(ctx.reader, ctx.version)` → `BasicDerivation.deserialize(ctx)`
- `BuildPathsResponse.deserialize` calls `KeyedBuildResult.from_reader(ctx.reader, ctx.version)` → `KeyedBuildResult.deserialize(ctx)`
- `QueryClosureWithInfoResponse.deserialize` calls `ValidPathInfo.from_reader(ctx.reader)` → `ValidPathInfo.deserialize(ctx)`
- `QueryPathInfoResponse.deserialize` calls `UnkeyedValidPathInfo.from_reader(ctx.reader)` → `UnkeyedValidPathInfo.deserialize(ctx)`
- `QueryPathInfosResponse.deserialize` calls `ValidPathInfo.from_reader(ctx.reader)` → `ValidPathInfo.deserialize(ctx)`
- And many more...

## Pattern for Each File
1. Read the file
2. For each class, compare `from_reader` body with `deserialize` body field-by-field
3. For each class, compare `to_writer` body with `serialize` body field-by-field
4. Fix any discrepancies in the NEW method (not the old one)
5. Update any cross-type calls within deserialize/serialize to use new API
6. Remove old `from_reader`/`to_writer` methods
7. Remove unused imports (NixReader, NixWriter from TYPE_CHECKING)
8. Run `pyright` and `ruff check` on the file
9. Run `just precommit` (full test suite)
10. Commit with `jj commit -m "refactor: remove old from_reader/to_writer from <filename>"`

## Key Insight for Cross-Type Calls
When `TypeA.deserialize` calls `TypeB.deserialize(ctx)`, it passes the SAME ctx. This is correct because the reader position advances through the sequential wire data. The version/client/buffer_logs in ctx are for the outer operation, but TypeB only uses ctx.reader and ctx.version (and version is only used for conditional fields in BuildResult). For types that don't use version/client/buffer_logs, passing the same ctx is fine.

## Test Command
```
cd ~/Code/pynixd && just precommit
```
Takes ~3 minutes. Must pass after each file.
