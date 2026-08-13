# TODO: Test Store Maintenance Operations

Implement functional tests for store maintenance and integrity operations.

## Operations to Test
*   **`CollectGarbage` (`nix store gc`)**:
    *   Verify that paths are correctly deleted from the backend store.
    *   Verify that deleted paths are removed from `pynixd`'s `known_paths` cache.
    *   Test different `action` types (e.g., `DeleteSpecific`, `DeleteDead`).
*   **`OptimiseStore` (`nix store optimise`)**:
    *   Verify that the operation is correctly forwarded to the backend.
*   **`VerifyStore` (`nix store verify / repair`)**:
    *   Verify that integrity checks are performed on the backend.
    *   Verify that the `repair` flag is correctly propagated.

## Verification Criteria
- [ ] GC correctly updates `pynixd` internal state.
- [ ] Operations return success/failure consistent with the backend.
- [ ] Large deletions don't cause timeouts or memory issues.

---

## Completion (2026-05-04)

**Maintenance operations tested.** `tests/functional/test_collect_garbage.py` covers
GC as admin, non-admin, and via Unix socket. `tests/functional/test_admin_ops.py` covers
OptimiseStore, VerifyStore, AddBuildLog, and AddSignatures — all with admin/non-admin gating.
All tests pass.
