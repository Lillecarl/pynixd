"""Functional tests for reverse store (builder-initiated connections)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import anyio
import asyncssh
import pytest
import structlog

from nix_daemon_protocol.ids import StoreId
from pynixd import Server
from pynixd.config import (
    PynixdSettings,
    ReverseAcceptorSettings,
    ReverseInitiatorSettings,
)
from pynixd.store import DaemonStore, LocalSocketStore
from tests.conftest import STORE_PREFIX, make_test_spec, rmtree_robust

if TYPE_CHECKING:
    from pathlib import Path

log = structlog.get_logger(__name__)


def _keypair(tmp_path: Path, name: str) -> tuple[Path, Path]:
    """A fresh ed25519 pair: `(private_path, public_path)`."""
    key = asyncssh.generate_private_key("ssh-ed25519")
    priv = tmp_path / name
    pub = tmp_path / f"{name}.pub"
    key.write_private_key(priv)
    key.write_public_key(pub)
    return priv, pub


def _controller_settings(tmp_path: Path, builder_pub: Path) -> tuple[PynixdSettings, Path]:
    ctrl_priv, ctrl_pub = _keypair(tmp_path, "controller")
    return (
        PynixdSettings(
            ssh_port=None,
            unix_path=tmp_path / "controller.sock",
            reverse_acceptor=ReverseAcceptorSettings(
                enabled=True,
                host="127.0.0.1",
                port=0,
                host_key_path=ctrl_priv,
                authorized_builder_keys=[builder_pub],
            ),
        ),
        ctrl_pub,
    )


def _builder_settings(
    tmp_path: Path,
    acceptor_port: int,
    builder_priv: Path,
    ctrl_pub: Path | None,
    store_id: str,
) -> tuple[PynixdSettings, LocalSocketStore]:
    builder_path = STORE_PREFIX / store_id
    rmtree_robust(builder_path)
    builder_local = LocalSocketStore(
        make_test_spec(store_id="local", store_path=builder_path, no_probe=True),
    )
    settings = PynixdSettings(
        ssh_port=None,
        unix_path=tmp_path / "builder.sock",
        reverse_acceptor=ReverseAcceptorSettings(enabled=False, authorized_builder_keys=None),
        reverse_initiator=ReverseInitiatorSettings(
            enabled=True,
            acceptor_host="127.0.0.1",
            acceptor_port=acceptor_port,
            store_id=store_id,
            systems=["x86_64-linux"],
            server_host_key_paths=[builder_priv],
            authorized_controller_keys=[ctrl_pub] if ctrl_pub is not None else None,
            reconnect_min_delay=0.1,
            reconnect_max_delay=1.0,
        ),
    )
    return settings, builder_local


async def test_reverse_store_registration(tmp_path: Path) -> None:
    """Builder connects to controller via reverse initiator, registers as a store.

    Both directions pin the other's key: the acceptor names the builder
    host key, the initiator names the controller key. Registration with
    the expected properties proves the pinned handshake.
    """
    builder_store_id = "test-builder"
    builder_priv, builder_pub = _keypair(tmp_path, "builder")

    # Each server's socket in this test's own directory. The default is
    # /run/pynixd, which exists only where the NixOS module runs pynixd,
    # and there it is the live service's.
    ctrl_settings, ctrl_pub = _controller_settings(tmp_path, builder_pub)

    async with Server(settings=ctrl_settings) as controller:
        if controller.reverse_acceptor is None:
            pytest.fail("Reverse acceptor did not start")
        acceptor_port = controller.reverse_acceptor.get_port()
        log.info("controller_acceptor_listening", port=acceptor_port)

        builder_settings, builder_local = _builder_settings(
            tmp_path, acceptor_port, builder_priv, ctrl_pub, builder_store_id
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
            rmtree_robust(STORE_PREFIX / builder_store_id)


async def test_reverse_wrong_builder_key_registers_nothing(tmp_path: Path) -> None:
    """A builder key the acceptor did not pin never registers. Issue #75."""
    builder_store_id = "test-rogue-builder"
    _builder_priv, builder_pub = _keypair(tmp_path, "builder")
    rogue_priv, _rogue_pub = _keypair(tmp_path, "rogue")

    ctrl_settings, ctrl_pub = _controller_settings(tmp_path, builder_pub)

    async with Server(settings=ctrl_settings) as controller:
        if controller.reverse_acceptor is None:
            pytest.fail("Reverse acceptor did not start")
        acceptor_port = controller.reverse_acceptor.get_port()

        builder_settings, builder_local = _builder_settings(
            tmp_path, acceptor_port, rogue_priv, ctrl_pub, builder_store_id
        )

        builder = Server(
            stores={StoreId("local"): builder_local},
            settings=builder_settings,
        )
        await builder.start()

        try:
            for _ in range(20):
                if StoreId(builder_store_id) in controller.stores:
                    pytest.fail("Rogue builder registered with an unpinned key")
                await anyio.sleep(0.1)
        finally:
            await builder.close()
            rmtree_robust(STORE_PREFIX / builder_store_id)
