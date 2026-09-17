"""Smoke test: new serde types through the full wire path."""

from __future__ import annotations

from nix_daemon_protocol.ids import StoreId
from pynixd.config import LocalSocketStoreSpec
from pynixd.store import LocalStore
from pynixd.store_path import StorePath
from tests._conftest.constants import STORE_PREFIX
from tests._conftest.helpers import rmtree_robust


async def test_new_serde_is_valid_path_roundtrip() -> None:
    """Prove SerdeIsValidPathRequest → daemon → SerdeIsValidPathResponse works.

    Creates a local store connected to the system daemon and sends
    an IsValidPath request through the new serde types.

    **`STORE_PREFIX`, and not `tmp_path_factory`.** A store holds the socket
    of its daemon, and a Unix socket path takes 107 bytes. pytest's temporary
    directory sits under `TMPDIR`, which `nix develop` points inside the work
    directory of the job, so the socket came to 115 bytes on a GitHub runner
    and to far less on a developer machine. The failure names the daemon and
    not the path. Issue #47.
    """
    from pynixd.serde import IsValidPathRequest, IsValidPathResponse

    store_path = STORE_PREFIX / "serde-wire"
    rmtree_robust(store_path)
    store = LocalStore(
        LocalSocketStoreSpec(
            store_id=StoreId("local"),
            store_path=store_path,
            monitor=False,
            probe=False,
        ),
    )
    await store.start()

    try:
        # Use a specific path — just verify the wire path doesn't crash
        sp = StorePath("/nix/store/00000000000000000000000000000000-doesnotexist")
        req = IsValidPathRequest(path=sp)

        resp = await store.call(req)

        assert isinstance(resp, IsValidPathResponse)
        # Path shouldn't exist — valid should be False
        assert resp.valid is False
    finally:
        await store.close()


async def test_local_db_store_is_valid_path_serde() -> None:
    """LocalDBStore executor returns serde IsValidPathResponse."""
    from nix_daemon_protocol.ids import StoreId
    from pynixd.config import LocalSocketStoreSpec
    from pynixd.store.local_db import LocalDBStore

    # Create a LocalDBStore (not LocalSocketStore)
    spec = LocalSocketStoreSpec(store_id=StoreId("test-serde"), use_db=True, monitor=False, probe=False)
    store = LocalDBStore(spec)
    await store.start()

    try:
        from pynixd.serde import IsValidPathRequest, IsValidPathResponse

        req = IsValidPathRequest(path=StorePath("/nix/store/00000000000000000000000000000000-test"))
        resp = await store.execute(req)
        assert isinstance(resp, IsValidPathResponse)
        assert resp.valid is False
    finally:
        await store.close()


async def test_local_db_store_is_valid_path_serde_cache_hit() -> None:
    """LocalDBStore executor returns serde IsValidPathResponse."""
    from nix_daemon_protocol.ids import StoreId
    from pynixd.config import LocalSocketStoreSpec
    from pynixd.serde import IsValidPathRequest, IsValidPathResponse
    from pynixd.store.local_db import LocalDBStore

    spec = LocalSocketStoreSpec(store_id=StoreId("test-serde-cache"), use_db=True, monitor=False, probe=False)
    store = LocalDBStore(spec)
    await store.start()

    try:
        path = StorePath("/nix/store/abc123-test-cache-hit")

        req = IsValidPathRequest(path=path)
        resp = await store.execute(req)  # type: ignore[arg-type]

        assert isinstance(resp, IsValidPathResponse)
        assert resp.valid is False  # path not in DB
    finally:
        await store.close()
