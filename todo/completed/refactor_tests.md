# Refactoring Tests to Use Shared pynixd Session Fixture

## Context

The test suite now has a session-scoped autouse `pynixd_server` fixture in `tests/conftest.py` that starts a shared `Server` with local+builder stores once per test session. Tests that are compatible should use `pynixd_server` instead of creating their own `Server`.

### What the fixture provides

- `pynixd_server: Server` — a running server via the fixture parameter
- Local store at `/tmp/pynixd-session-stores/local`
- Builder store at `/tmp/pynixd-session-stores/builder`
- SSH on ephemeral port (`server.port` for URI construction)
- HTTP on ephemeral port (`server.http_bound_port`)
- Unix socket at `/tmp/pynixd-session-stores/pynixd.sock`
- Stores use `SESSION_NIX_CONFIG` which includes `nix-command`, `flakes`, `read-only-local-store`, `ca-derivations`, `dynamic-derivations`, and `recursive-nix` experimental features
- `cleanup_extra_stores` autouse fixture removes non-default stores between tests

### Key patterns

**Before (old style):**
```python
pynixd_local_path = STORE_PREFIX / "my-test-local"
pynixd_builder_path = STORE_PREFIX / "my-test-builder"
rmtree_robust(pynixd_local_path)
rmtree_robust(pynixd_builder_path)

pynixd_local = LocalSocketStore(id="local", store_path=pynixd_local_path, **get_test_store_kwargs())
pynixd_builder = LocalSocketStore(id="builder", store_path=pynixd_builder_path, **get_test_store_kwargs())

async with Server(local_store=pynixd_local, stores={"builder": pynixd_builder}, ssh_port=0) as server:
    uri = f"ssh-ng://{username}@127.0.0.1:{server.port}"
    ...
```

**After (new style):**
```python
from pynixd import Server


async def test_something(pynixd_server: Server) -> None:
    uri = pynixd_server.uri()
    ...
```

### URI helper methods

- `pynixd_server.uri()` — returns `ssh-ng://{user}@{host}:{port}`
- `pynixd_server.builder_uri()` — same with `max_jobs=4`
- `pynixd_server.uri_for("unix")` — returns `unix://...` URI
- `pynixd_server.port` — SSH port (int)
- `pynixd_server.http_bound_port` — HTTP port (int or None)

### Cleanup convention

Tests that need extra stores can use `await pynixd_server.add_store(store)`. The `cleanup_extra_stores` fixture removes any store whose id is not in `{"local", "builder"}` after each test, including deleting the store directory if it's under `SESSION_STORE_PREFIX`.

---

## File-by-file instructions

### SKIP — Do NOT migrate these files

| File | Reason |
|------|--------|
| `test_run.py` | No server needed. Pure subprocess tests. |
| `test_psi_parsers.py` | No server needed. Pure parsing unit tests. |
| `test_param_logs.py` | No server needed. Tests `run_subproc` helper. |
| `test_stats.py` | Custom `StatsTestStore`/`CpuUtilTestStore` subclasses with mock `build_conn()`. Needs isolated server with mocked stores. |
| `test_persistence.py` | Restarts server with different store subclasses between halves of test. Needs isolated server lifecycle. |
| `test_feature_probe.py` | Uses `SSHSubprocessStore` to external host and creates single stores directly (no `Server`). `test_feature_probe_nixbuild_net` reaches external host. |
| `test_extension_delegation.py` | Creates TWO servers (A and B), uses `SSHSubprocessStore`, root store at `Path("/")`. Complex multi-server topology. |
| `test_http_cache.py` | Creates server with `http_port=0` for HTTP cache testing. The shared server may or may not have HTTP enabled — needs separate server for isolation. |
| `test_http_upload.py` | Same as http_cache — needs HTTP, isolation. |
| `test_http_htpasswd.py` | Needs HTTP + htpasswd config. Isolated. |
| `test_stream_nar.py` | No `Server` at all. Uses standalone `LocalSocketStore` instances, one with `store_path=Path("/")`. |
| `test_ca_ops.py` | **Partial migration.** The `*_via_pynixd` tests that use `ca_env`/`dyn_env` fixtures can use the shared server since SESSION_NIX_CONFIG includes `ca-derivations` and `dynamic-derivations`. The `*_root_store` tests (no Server, uses standalone `LocalSocketStore`) must stay independent. See detailed instructions below. |
| `test_copy.py` | Creates single local store, no builder. Uses `server.uri()` with `nix copy`. Simple but single-store topology. |

### MIGRATE — These files can use the shared fixture

#### `test_simple.py` — DONE

Already migrated. Use as reference pattern.

#### `test_dag.py`

Two tests: `test_builders` and `test_store`. Both create default local+builder stores with default NixConfig. Direct migration:

1. Add `pynixd_server: Server` parameter to both tests
2. Replace manual Server setup with `uri = pynixd_server.uri()` and `builder_spec = pynixd_server.builder_uri()`
3. For `test_builders`: keep `client_store_path = tmp_path / "client-store"` (the client store is separate from pynixd)
4. Remove `LocalSocketStore`, `os.environ.get("USER", "root")`, `STORE_PREFIX` path setup, `rmtree_robust` for local/builder paths
5. Keep `set_log_levels` usage in `test_store`

#### `test_queries.py`

Has a `query_env` fixture that creates a server, builds `minimal.leaf`, and yields `(server, uri, out_path)`. Migrate the fixture:

1. Change `query_env` to accept `pynixd_server: Server` and use it instead of creating its own server
2. `uri = pynixd_server.uri()` instead of manual URI construction
3. Remove manual Server/local/builder setup from fixture
4. Remove `STORE_PREFIX`, `rmtree_robust`, `LocalSocketStore` imports
5. All tests that use `query_env` don't need further changes since they get `(server, uri, out_path)` from the fixture

#### `test_rbac.py`

Two tests, both simple single-store setups. But note `test_rbac_ssh_admin_vs_user` uses `admin_users={"admin-user"}` and `test_rbac_unix_implicit_admin` uses `unix_path=socket_path`. The shared fixture has `admin_users` unset and uses the session unix socket path.

**Migrate `test_rbac_ssh_admin_vs_user` carefully:** The shared server has no `admin_users` set, which means all users are admins by default. This test NEEDS `admin_users` restriction. **SKIP this test** — it needs its own server with custom config.

**Migrate `test_rbac_unix_implicit_admin`:** Can use `pynixd_server` with `pynixd_server.uri_for("unix")`. The shared server already has a unix socket at `SESSION_STORE_PREFIX / "pynixd.sock"`. Just construct the URI and run `nix store gc --store unix://...`.

Actually, on second thought — the unix socket test needs `root={pynixd_local_path}` in the URI which points to the session local store path. This should work with the shared server:
```python
uri = f"unix://{SESSION_STORE_PREFIX / 'pynixd.sock'}?root={SESSION_STORE_PREFIX / 'local'}"
```

But the RBAC test fundamentally requires `admin_users` which the shared server doesn't have. **SKIP both RBAC tests.**

#### `test_add_to_store_nar.py`

Single test creating a local-only server (no builder). Can use shared server:

1. Add `pynixd_server: Server` parameter
2. Use `uri = pynixd_server.uri()` for `nix store add-path --store`
3. Remove manual server setup

#### `test_copy.py`

Creates single local store, no builder. Uses `nix copy --from daemon --to server.uri()`. Can use shared server:

1. Add `pynixd_server: Server` parameter
2. Use `uri = pynixd_server.uri()` for `nix copy --to`
3. Remove manual server setup

But note: `test_copy` does `nix build nixpkgs#hello` first, then `nix copy --from daemon --to`. The `nix build nixpkgs#hello` is against the system daemon, not pynixd. Then copies to pynixd. This should work with the shared server — paths get added to the session's local store.

#### `test_ca_ops.py` — Partial migration

The session server uses `SESSION_NIX_CONFIG` which includes `ca-derivations` and `dynamic-derivations` experimental features. This means the `*_via_pynixd` tests can use the shared server.

**Migrate `ca_env` fixture** to use `pynixd_server`:
- The current `ca_env` creates its own server with `CA_NIX_CONFIG`. Since the session server already has CA enabled, the `*_via_pynixd` tests can use `pynixd_server` directly.
- Yield `(pynixd_server, pynixd_server.uri())` instead of `(server, uri)`.
- Note: `run_subproc` calls in CA tests pass `nix_config=CA_NIX_CONFIG` explicitly — this sets the NIX_CONFIG env var for the nix client, which is separate from the server's config. Keep these `nix_config=` parameters.

**Migrate `dyn_env` fixture** similarly:
- The session server also has `dynamic-derivations`. Yield `(pynixd_server, pynixd_server.uri())`.
- Keep `nix_config=DYN_NIX_CONFIG` in `run_subproc` calls.

**Do NOT migrate** these tests:
- `test_ca_simple_build_root_store` — standalone `LocalSocketStore`, no Server
- `test_ca_multi_output_build_root_store` — standalone `LocalSocketStore`, no Server
- `test_ca_depends_on_ca_root_store` — standalone `LocalSocketStore`, no Server
- `test_non_ca_depends_on_ca_root_store` — standalone `LocalSocketStore`, no Server
- `test_ca_query_derivation_output_map_root_store` — standalone `LocalSocketStore`, no Server
- `test_text_hashed_ca_build_root_store` — standalone `LocalSocketStore`, no Server
- `test_dynamic_drv_producing_via_pynixd` — uses `dyn_env`, could migrate but complex
- `test_dynamic_drv_wrapper_via_pynixd` — uses `dyn_env`, could migrate but complex

**Mark skipped tests** with `@pytest.mark.no_pynixd` since they create their own isolated stores.

**Bug in current migration**: `test_dynamic_drv_producing_via_pynixd` (line ~609) references `STORE_PREFIX / "pynixd-local-dyn"` which won't exist with the shared server. This line checks if a file exists on disk:
```python
pynixd_local_path = STORE_PREFIX / "pynixd-local-dyn"
full_path = pynixd_local_path / producing_out.lstrip("/")
```
This needs to be changed to use the session store path: `SESSION_STORE_PREFIX / "local" / producing_out.lstrip("/")`.

**Bug in current migration**: Only `test_ca_simple_build_root_store` has `@pytest.mark.no_pynixd`. The other `*_root_store` tests also need it since they create their own stores.

---

## Issues found in AI's migration

### test_ca_ops.py

1. **Missing `@pytest.mark.no_pynixd`**: All `*_root_store` tests create their own `LocalSocketStore` and don't use the shared server. Currently only `test_ca_simple_build_root_store` has the marker. These also need it:
   - `test_ca_multi_output_build_root_store`
   - `test_ca_depends_on_ca_root_store`
   - `test_non_ca_depends_on_ca_root_store`
   - `test_ca_query_derivation_output_map_root_store`
   - `test_text_hashed_ca_build_root_store`

2. **`test_dynamic_drv_producing_via_pynixd`** references `STORE_PREFIX / "pynixd-local-dyn"` on line ~609. With the shared server, the local store is at `SESSION_STORE_PREFIX / "local"`. This path needs to be updated.

### test_queries.py

3. **`query_env` fixture has `async def` but shouldn't need it** now that it just uses `pynixd_server`. The fixture yields `(pynixd_server, uri, out_path)` which is correct, but since it waits for a build to complete, it needs to remain async. This is fine.

### test_dag.py, test_simple.py, test_copy.py, test_add_to_store_nar.py

These look correct. No issues found.

---

## Migration checklist for each test file

For each file to migrate, follow these steps:

1. **Add** `from pynixd import Server` import
2. **Add** `pynixd_server: Server` to the test function parameters (or fixture parameters)
3. **Remove** all `LocalSocketStore(...)` constructor calls for the local and builder stores
4. **Remove** all `STORE_PREFIX / "some-path"` and `rmtree_robust(...)` for local/builder paths (the session fixture handles this)
5. **Remove** `async with Server(...) as server:` blocks — use `pynixd_server` directly
6. **Replace** manual URI construction (`f"ssh-ng://{username}@..."`) with `pynixd_server.uri()` or `pynixd_server.builder_uri()`
7. **Remove** `os.environ.get("USER", "root")` — handled by `server.uri()`
8. **Remove** unused imports: `LocalSocketStore`, `STORE_PREFIX`, `rmtree_robust`, `get_test_store_kwargs`, `os`
9. **Keep** `tmp_path` usage for client-side stores (the nix client's `--store` directory is separate from pynixd's stores)
10. **Keep** `set_log_levels` usage if the test had it
11. **Keep** any test-specific logic (NixConfig, custom stores added via `add_store`, etc.)
12. **Run** `just check` to verify lint+types pass after changes
13. **Run** `pytest tests/functional/YOUR_FILE.py -v &> /tmp/test.log ; tail -15 /tmp/test.log` to verify tests pass