# Global NewType Refactor Completion

The project is transitioning from raw `str`/`int` to `StoreId`, `BuildId`, and `RequestId`. Many files are updated, but several `pyright` errors remain due to invariance and missed parameters.

## Remaining Pyright Fixes
- [ ] **`pynixd/allocator.py`**: Fix `is_blacklisted` signature to accept `StoreId`.
- [ ] **`pynixd/gc.py`**: Fix `Mapping` invariance in `GarbageCollector.stores`.
- [ ] **`pynixd/proxy.py`**: Fix `Mapping` invariance in `DaemonProxy.stores`.
- [ ] **`pynixd/scheduler.py`**: Fix `Mapping` invariance in `__init__` and fix `ca_realisations` list assignment type.
- [ ] **`pynixd/instance.py`**:
    - Update `Server.__init__` to use `dict[StoreId, Store]`.
    - Update `drain_store` and `pop` calls to use `StoreId`.
    - Restore `remove_store_paths` database call once the method is updated in `LocalStoreDB`.
- [ ] **Store Subclasses**:
    - `pynixd/store/local.py`: Update `LocalSocketStore.__init__` to use `StoreId`.
    - `pynixd/store/ssh.py`: Update `SSHStore` and `KeyedSSHStore` to use `StoreId`.
- [ ] **`pynixd/local_store_db.py`**: Update `remove_store_paths` signature to use `StoreId`.

## Global Sweep
- [ ] Audit all `log` calls to ensure `build_id` and `store_id` fields are passed the correctly typed objects (though `NewType` is a runtime no-op, consistency is better).
- [ ] Run `just precommit` and ensure 0 errors.
