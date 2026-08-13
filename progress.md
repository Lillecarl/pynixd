# Progress

## Status
In Progress — removing path tracking

## Completed
- [x] Remove sync_paths from DaemonStore
- [x] Remove tracker from Store base class (base.py)
- [x] Fix scheduler.py — remove tracker usage, always schedulable
- [x] Fix allocator.py — drop data locality scoring
- [x] Fix store/transfer.py — replace tracker with QueryValidPaths
- [x] Fix store/local_db.py — remove all tracker calls and PathTracker import
- [x] Fix context.py — remove path_tracker field
- [x] Fix instance.py — remove path_tracker setup, add_store tracker code
- [x] Cleaned 7 handler files: query_closure, query_valid_paths, query_path_from_hash_part, query_path_info, is_valid_path, ca_derivations, query_derivation_output_map

## Remaining
- [ ] Clean remaining handler files: add_to_store, add_multiple_to_store, add_to_store_nar, build_paths, build_derivation, collect_garbage, query_closure_with_info, query_path_infos
- [ ] Remove known_paths from local_store_db.py
- [ ] Delete DB_PynixdKnownPaths table and queries
- [ ] Delete pynixd/path_tracker.py
- [ ] Fix tests: test_persistence.py, mock_store.py, session config
- [ ] Delete test_persistence.py
