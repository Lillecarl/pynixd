"""A daemon spawned by `start` does not outlive a `start` that raises.

`LocalStore.start` calls `ensure_daemon` and then `DaemonStore.start`, which
probes. Nothing else owns the spawned process until `start` returns:
`Server.__aenter__` is the caller, so a raise means `__aexit__` never runs and
`close()` never reaps it.

Measured in CI run 35147170272: the probe raised on a daemon that refused it,
149 fixtures of `tests/functional` failed at setup, pytest printed
`1 failed, 567 passed, 87 skipped, 10 xfailed, 149 errors in 11.91s`, and then
the process did not exit. The job was cancelled six hours later, and the
runner named the orphans it had to terminate: `.pytest-wrapped` and one `nix`.
Issue #47.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nix_daemon_protocol.ids import StoreId
from pynixd.config import LocalSocketStoreSpec
from pynixd.store import daemon as daemon_module
from pynixd.store.local_daemon import LocalStore


class _ProbeRefused(Exception):
    """What a daemon that refuses the capability probe raises past `start`."""


def _store(tmp_path: Path) -> LocalStore:
    return LocalStore(
        LocalSocketStoreSpec(
            store_id=StoreId("local"),
            store_path=tmp_path,
            socket_path=tmp_path / "daemon.socket",
        ),
    )


async def test_a_failed_start_reaps_the_daemon_it_spawned(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spawned: list[str] = []
    reaped: list[str] = []
    store = _store(tmp_path)

    async def ensure_daemon() -> None:
        spawned.append("daemon")

    async def probing_start(_self: object, sync_paths: bool = True) -> None:
        raise _ProbeRefused("you are not privileged to build input-addressed derivations")

    async def kill_daemon() -> None:
        reaped.append("daemon")

    monkeypatch.setattr(store, "ensure_daemon", ensure_daemon)
    monkeypatch.setattr(store, "_kill_daemon", kill_daemon)
    monkeypatch.setattr(daemon_module.DaemonStore, "start", probing_start)

    with pytest.raises(_ProbeRefused):
        await store.start()

    assert spawned == ["daemon"], "the test must spawn before it fails, or it proves nothing"
    assert reaped == ["daemon"], "a daemon nobody owns is the orphan that held pytest open for six hours"


async def test_a_start_that_returns_keeps_its_daemon(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The negative control: the reap belongs to the failure path alone."""
    reaped: list[str] = []
    store = _store(tmp_path)

    async def ensure_daemon() -> None:
        return None

    async def quiet_start(_self: object, sync_paths: bool = True) -> None:
        return None

    async def kill_daemon() -> None:
        reaped.append("daemon")

    monkeypatch.setattr(store, "ensure_daemon", ensure_daemon)
    monkeypatch.setattr(store, "_kill_daemon", kill_daemon)
    monkeypatch.setattr(daemon_module.DaemonStore, "start", quiet_start)

    await store.start()

    assert reaped == []
