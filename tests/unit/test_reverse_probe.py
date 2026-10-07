"""A registering builder proves its serve path before it may take builds.

`ReverseStore.prove_serves` sends one echo build and answers with the
refusal reason, or None when the builder proved itself. A hung serve path
answers never, so the probe carries its own timeout. Issue #80.
"""

from __future__ import annotations

import time

import anyio

from nix_daemon_protocol.ids import StoreId
from pynixd.config import ReverseStoreSpec
from pynixd.store.reverse import ReverseStore


def _unstarted_store() -> ReverseStore:
    """A reverse store with no connection: `_send_probe` is always faked."""
    return ReverseStore(
        ReverseStoreSpec(store_id=StoreId("probe-unit")),
        None,  # type: ignore[arg-type]
    )


async def test_prove_serves_accepts_a_working_builder() -> None:
    store = _unstarted_store()

    async def _ok(
        name: str,
        system: str,
        required_features: str,
        args: list[str],
        extra_env: dict[str, str] | None = None,
    ) -> tuple[str, bool, str]:
        return name, True, ""

    store._send_probe = _ok  # type: ignore[method-assign]
    assert await store.prove_serves("x86_64-linux", 10.0) is None


async def test_prove_serves_reports_a_refusal() -> None:
    store = _unstarted_store()

    async def _refused(
        name: str,
        system: str,
        required_features: str,
        args: list[str],
        extra_env: dict[str, str] | None = None,
    ) -> tuple[str, bool, str]:
        return name, False, "builder for 'probe.drv' failed to produce output path"

    store._send_probe = _refused  # type: ignore[method-assign]
    reason = await store.prove_serves("x86_64-linux", 10.0)
    assert reason == "builder for 'probe.drv' failed to produce output path"


async def test_prove_serves_times_out_a_hung_builder() -> None:
    store = _unstarted_store()

    async def _hang(
        name: str,
        system: str,
        required_features: str,
        args: list[str],
        extra_env: dict[str, str] | None = None,
    ) -> tuple[str, bool, str]:
        await anyio.sleep(60.0)
        return name, True, ""

    store._send_probe = _hang  # type: ignore[method-assign]
    started = time.monotonic()
    reason = await store.prove_serves("x86_64-linux", 0.2)
    assert time.monotonic() - started < 10.0
    assert reason is not None and "timed out" in reason
