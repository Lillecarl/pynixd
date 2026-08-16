"""Functional tests for reverse store (builder-initiated connections)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import anyio
import pytest
import structlog

from pynixd import Server
from pynixd.config import (
    PynixdSettings,
    ReverseAcceptorSettings,
    ReverseInitiatorSettings,
)
from pynixd.serde.ids import StoreId
from pynixd.store import DaemonStore, LocalSocketStore
from tests.conftest import STORE_PREFIX, make_test_spec, rmtree_robust
from tests.test_features import TestFeatures as F

if TYPE_CHECKING:
    from pathlib import Path

log = structlog.get_logger(__name__)


@pytest.mark.covers(F.STORE_REVERSE | F.ADD_TO_STORE_NAR)
async def test_reverse_store_registration(tmp_path: Path) -> None:
    """Builder connects to controller via reverse initiator, registers as a store.

    Verifies the builder's store appears in the controller's store registry
    with the expected properties.
    """
    builder_store_id = "test-builder"
    builder_path = STORE_PREFIX / builder_store_id
    rmtree_robust(builder_path)

    ctrl_settings = PynixdSettings(
        ssh_port=None,
        reverse_acceptor=ReverseAcceptorSettings(enabled=True, host="127.0.0.1", port=0),
    )

    async with Server(settings=ctrl_settings) as controller:
        if controller.reverse_acceptor is None:
            pytest.fail("Reverse acceptor did not start")
        acceptor_port = controller.reverse_acceptor.get_port()
        log.info("controller_acceptor_listening", port=acceptor_port)

        builder_local = LocalSocketStore(
            make_test_spec(store_id="local", store_path=builder_path, no_probe=True),
        )

        builder_settings = PynixdSettings(
            ssh_port=None,
            reverse_acceptor=ReverseAcceptorSettings(enabled=False),
            reverse_initiator=ReverseInitiatorSettings(
                enabled=True,
                acceptor_host="127.0.0.1",
                acceptor_port=acceptor_port,
                store_id=builder_store_id,
                systems=["x86_64-linux"],
                reconnect_min_delay=0.1,
                reconnect_max_delay=1.0,
            ),
        )

        builder = Server(
            stores={StoreId("local"): builder_local},
            settings=builder_settings,
        )
        await builder.start()

        try:
            store_id = StoreId(builder_store_id)

            for _ in range(50):
                if store_id in controller.stores:
                    break
                await anyio.sleep(0.1)
            else:
                pytest.fail("Builder did not register within 5 seconds")

            store = controller.stores[store_id]
            assert store.store_id == store_id
            assert isinstance(store, DaemonStore)
            assert store.systems == {"x86_64-linux"}

        finally:
            await builder.close()
            rmtree_robust(builder_path)
