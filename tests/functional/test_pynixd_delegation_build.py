"""Functional test for pynixd-to-pynixd build delegation."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import asyncssh
import pytest
import structlog
from pynixd.serde.ids import StoreId

from pynixd import Server
from pynixd.config import SSHSubprocessStoreSpec
from pynixd.serde import IsValidPathRequest
from pynixd.serde import StorePath as SerdeStorePath
from pynixd.store import LocalSocketStore, SSHSubprocessStore
from pynixd.store_path import StorePath
from tests.conftest import (
    CLIENT_BIN,
    NIX_BIN,
    STORE_PREFIX,
    make_test_spec,
    rmtree_robust,
    run_subproc,
)
from tests.test_features import TestFeatures as F

if TYPE_CHECKING:
    from pathlib import Path

log = structlog.get_logger(__name__)


@pytest.mark.covers(F.EXTENSION_BUILD | F.BUILD_DERIVATION | F.BUILD_PATHS | F.BUILD_PATHS_WITH_RESULTS | F.STORE_LOCAL)
@pytest.mark.no_pynixd
@pytest.mark.xfail(reason="build output path tracking mismatch between servers")
async def test_pynixd_delegation_build(tmp_path: Path) -> None:
    """Test that pynixd can delegate build OPs to another pynixd instance.

    Setup:
    - Server B: Real Nix daemon backend.
    - Server A: Pynixd proxy, uses Server B as its only remote store.
    - Client: 'nix build' targeting Server A via Unix socket.

    Success:
    - Nix sends build request to Server A.
    - Server A's scheduler delegates build to Server B.
    - Server B executes build against its local Nix daemon.
    """

    # 0. Setup SSH keys and stores
    key = asyncssh.generate_private_key("ssh-rsa")

    # Store for Server B
    store_b_path = STORE_PREFIX / "delegation-build-store-b"
    rmtree_robust(store_b_path)
    store_b_path.mkdir(parents=True, exist_ok=True)
    store_b = LocalSocketStore(
        make_test_spec(
            store_id="b-local",
            store_path=store_b_path,
            no_probe=True,
        ),
    )

    # 1. Start Server B (The Actual Builder)
    async with Server(
        stores={StoreId("local"): store_b},
        ssh_port=0,
        http_port=None,
    ) as server_b:
        port_b = server_b.port
        log.info("server_b_started", port=port_b)

        # 2. Start Server A (The Proxy)
        # It has server_b as its remote store.
        store_a_b = SSHSubprocessStore(
            SSHSubprocessStoreSpec(
                store_id=StoreId("builder-b"),
                host="127.0.0.1",
                port=port_b,
                username=server_b.username,
                client_keys=[key],
                nix_bin=str(NIX_BIN),
                monitor=False,
            ),
        )

        # Server A's local store
        store_a_path = STORE_PREFIX / "delegation-build-store-a"
        rmtree_robust(store_a_path)
        store_a_path.mkdir(parents=True, exist_ok=True)
        store_a = LocalSocketStore(
            make_test_spec(
                store_id="a-local",
                store_path=store_a_path,
                no_probe=True,
            ),
        )

        unix_path_a = tmp_path / "server-a.sock"

        async with Server(
            stores={StoreId("local"): store_a, StoreId("builder-b"): store_a_b},
            ssh_port=None,
            unix_path=unix_path_a,
            http_port=None,
        ) as server_a:
            log.info("server_a_started", unix=unix_path_a)

            # Wait for Server A to probe Server B
            # (In a real scenario, this happens in background tasks)
            # We can wait until builder-b is in scheduler.stores
            for _ in range(50):
                if "builder-b" in server_a.stores:
                    break
                await asyncio.sleep(0.1)
            assert "builder-b" in server_a.stores

            # 3. Issue a 'nix build' to Server A
            uri = f"unix://{unix_path_a}?root={store_a_path}"

            nix_expr = """
            with import <nixpkgs> {};
            runCommand "delegation-test" {
                ts = builtins.currentTime;
            } "echo 'delegated build' > $out"
            """
            expr_file = tmp_path / "test.nix"
            expr_file.write_text(nix_expr)

            cmd = [
                str(CLIENT_BIN),
                "build",
                "--file",
                str(expr_file),
                "--store",
                uri,
                "--no-link",
                "--print-out-paths",
                "--impure",
            ]

            log.info("starting_nix_build", cmd=cmd)
            rc, stdout, stderr, _ = await run_subproc(cmd)

            if rc != 0:
                log.error("nix_build_failed", stdout=stdout, stderr=stderr)

            assert rc == 0
            out_path = stdout.strip()
            log.info("nix_build_success", out_path=out_path)

            # 4. Verify results
            # The path should exist in server_b's store (it built it)
            StorePath(out_path)
            resp_b = await server_b.local_store.execute(IsValidPathRequest(path=SerdeStorePath(path=out_path)))
            assert resp_b.valid

            # The path should also be known by server_a (it tracked the result)
            resp_a = await server_a.local_store.execute(IsValidPathRequest(path=SerdeStorePath(path=out_path)))
            assert resp_a.valid
            log.info("delegation_build_verified")
