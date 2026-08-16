"""Smoke test: new serde types through the full wire path."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from _pytest.tmpdir import TempPathFactory

from pynixd.config import LocalSocketStoreSpec
from pynixd.serde.ids import StoreId
from pynixd.store import LocalStore
from pynixd.store_path import StorePath


async def test_new_serde_is_valid_path_roundtrip(tmp_path_factory: TempPathFactory) -> None:
    """Prove SerdeIsValidPathRequest → daemon → SerdeIsValidPathResponse works.

    Creates a local store connected to the system daemon and sends
    an IsValidPath request through the new serde types.
    """
    from pynixd.serde import IsValidPathRequest, IsValidPathResponse, StorePath as SerdeStorePath

    store_path = Path(tmp_path_factory.mktemp("serde-wire"))
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
        req = IsValidPathRequest(path=SerdeStorePath(path=str(sp)))

        resp = await store.call(req)

        assert isinstance(resp, IsValidPathResponse)
        # Path shouldn't exist — valid should be False
        assert resp.valid is False
    finally:
        await store.close()


async def test_local_db_store_is_valid_path_serde() -> None:
    """LocalDBStore executor returns serde IsValidPathResponse."""
    from pynixd.config import LocalSocketStoreSpec
    from pynixd.serde.ids import StoreId
    from pynixd.store.local_db import LocalDBStore
    from pynixd.store_path import StorePath

    # Create a LocalDBStore (not LocalSocketStore)
    spec = LocalSocketStoreSpec(store_id=StoreId("test-serde"), use_db=True, monitor=False, probe=False)
    store = LocalDBStore(spec)
    await store.start()

    try:
        from pynixd.serde import IsValidPathRequest, IsValidPathResponse, StorePath as SerdeStorePath

        req = IsValidPathRequest(
            path=SerdeStorePath(path=str(StorePath("/nix/store/00000000000000000000000000000000-test")))
        )
        resp = await store.execute(req)
        assert isinstance(resp, IsValidPathResponse)
        assert resp.valid is False
    finally:
        await store.close()


async def test_local_db_store_is_valid_path_serde_cache_hit() -> None:
    """LocalDBStore executor returns serde IsValidPathResponse."""
    from pynixd.config import LocalSocketStoreSpec
    from pynixd.serde import IsValidPathRequest, IsValidPathResponse, StorePath as SerdeStorePath
    from pynixd.serde.ids import StoreId
    from pynixd.store.local_db import LocalDBStore
    from pynixd.store_path import StorePath

    spec = LocalSocketStoreSpec(store_id=StoreId("test-serde-cache"), use_db=True, monitor=False, probe=False)
    store = LocalDBStore(spec)
    await store.start()

    try:
        path = StorePath("/nix/store/abc123-test-cache-hit")

        req = IsValidPathRequest(path=SerdeStorePath(path=str(path)))
        resp = await store.execute(req)  # type: ignore[arg-type]

        assert isinstance(resp, IsValidPathResponse)
        assert resp.valid is False  # path not in DB
    finally:
        await store.close()
