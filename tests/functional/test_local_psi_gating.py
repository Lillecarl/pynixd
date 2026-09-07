"""
Functional tests for LocalSocketStore pressure gating.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import anyio
import pytest
import structlog

from pynixd.config import PynixdSettings
from pynixd.exceptions import ResourceExhaustedError
from pynixd.monitor import ResourceMonitor
from pynixd.store.local import LocalSocketStore
from tests.conftest import make_test_spec
from tests.test_features import TestFeatures as F

if TYPE_CHECKING:
    from pathlib import Path

log = structlog.get_logger(__name__)


class MockMonitor(ResourceMonitor):
    """Monitor that doesn't actually poll, allows manual trigger."""

    async def run(self) -> None:
        while self.running:  # noqa: ASYNC110 — test mock
            await anyio.sleep(1)


@pytest.mark.covers(F.SERVER_PSI_GATING | F.STORE_LOCAL)
async def test_local_socket_store_gating(tmp_path: Path) -> None:
    """Test that LocalSocketStore gates connections based on memory gate state."""

    settings = PynixdSettings(gate_timeout=0.2)  # short timeout for testing
    store = LocalSocketStore(
        make_test_spec(store_id="test-gate", store_path=tmp_path, no_probe=True, settings=settings),
    )

    # 1. Replace the real monitor with a mock one we can control
    if store.monitor:
        await store.monitor.stop()
    store.monitor = MockMonitor(store.gate, settings)
    store.monitor.start()

    try:
        # 2. Test CPU Gate (Should NOT block anymore)
        store.gate.cpu_clear.clear()  # Simulate high pressure

        async with store.build_conn() as conn:
            assert conn is not None

        # 3. Test Memory Gate (Should still block)
        store.gate.mem_clear.clear()  # Simulate low memory

        # Build connections should wait for memory
        with pytest.raises(ResourceExhaustedError) as excinfo:
            async with store.build_conn():
                pass
        assert "Memory pressure" in str(excinfo.value)

        # Transfer connections should wait for memory
        with pytest.raises(ResourceExhaustedError) as excinfo:
            async with store.transfer_conn():
                pass
        assert "Memory pressure" in str(excinfo.value)

        # Clear and retry
        store.gate.mem_clear.set()
        async with store.transfer_conn() as conn:
            assert conn is not None

    finally:
        await store.close()


async def test_gate_wait_timeout_success(tmp_path: Path) -> None:
    """Test that gating succeeds if pressure subsides within timeout."""

    settings = PynixdSettings(gate_timeout=2.0)
    store = LocalSocketStore(
        make_test_spec(store_id="test-gate-timeout", store_path=tmp_path, no_probe=True, settings=settings),
    )

    if store.monitor:
        await store.monitor.stop()
    store.monitor = MockMonitor(store.gate, settings)
    store.monitor.start()

    try:
        store.gate.cpu_clear.clear()

        # Start build_conn in background
        async def acquire():
            async with store.build_conn():
                return True

        task = asyncio.create_task(acquire())

        # Wait a bit, then set the gate
        await anyio.sleep(0.5)
        store.gate.cpu_clear.set()

        result = await task
        assert result is True

    finally:
        await store.close()
