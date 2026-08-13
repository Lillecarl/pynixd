"""Autouse fixtures for store cleanup, profiling, and test isolation."""

from __future__ import annotations

import asyncio
import os
import random
import time
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import structlog

from pynixd.testing import clear_test_stash
from tests._conftest.constants import (
    HAS_PYINSTRUMENT,
    SESSION_STORE_PREFIX,
    STORE_PREFIX,
    ConsoleRenderer,
    Profiler,
    _default_store_ids,
)
from tests._conftest.helpers import rmtree_robust, rmtree_robust_glob

if TYPE_CHECKING:
    from collections.abc import Generator

    from pynixd import Server

log = structlog.get_logger(__name__)


# ── Profiling helpers ─────────────────────────────────────────────


def _prune_client_processor(frame, options):
    """Custom pyinstrument processor to remove client-side subprocess frames."""
    if frame is None:
        return None
    for child in list(frame.children):
        if child.function and ("run_nix_build" in child.function or "run_subproc" in child.function):
            child.remove_from_parent()
        else:
            _prune_client_processor(child, options)
    return frame


# ── Autouse fixtures ──────────────────────────────────────────────


@pytest.fixture(autouse=True)
def clear_instrumentation():
    """Clear internal test stash before each test."""
    clear_test_stash()
    return


@pytest.fixture(autouse=True)
def _fixed_test_ts():
    """Pin PYNIXD_TEST_TS for each test so build+eval get consistent .drv paths."""
    ts = str(int(time.time()))
    original = os.environ.get("PYNIXD_TEST_TS")
    os.environ["PYNIXD_TEST_TS"] = ts
    yield
    if original is None:
        os.environ.pop("PYNIXD_TEST_TS", None)
    else:
        os.environ["PYNIXD_TEST_TS"] = original


@pytest.fixture(autouse=True)
def profiler(request: pytest.FixtureRequest, test_logging: Path):
    """Profile every test and save to a .pyinstrument file."""
    if not HAS_PYINSTRUMENT:
        yield None
        return

    if request.node.get_closest_marker("no_profile"):
        yield None
        return

    # ── Setup: ensure no stale profiler is running ──
    # The pyinstrument StackSampler is a singleton. If a previous test's
    # profiler wasn't stopped (e.g., due to crash), its subscriber is
    # still registered and start() will fail. Detect and clean up.
    from pyinstrument.stack_sampler import active_profiler_context_var, get_stack_sampler

    sampler = get_stack_sampler()
    if any(sampler.subscribers) or active_profiler_context_var.get() is not None:
        sampler.subscribers.clear()
        active_profiler_context_var.set(None)

    p = Profiler(async_mode="enabled")
    p.start()

    try:
        yield p
    finally:
        # ── Teardown: always stop, even if test failed ──
        try:
            if p.is_running:
                p.stop()
        except Exception:
            pass  # best-effort cleanup

        # Write profile output
        try:
            session = p.last_session
            if session:
                profile_file = test_logging / "pyinstrument.txt"
                renderer = ConsoleRenderer(unicode=True, color=False)
                renderer.processors.insert(0, _prune_client_processor)
                with profile_file.open("w") as f:
                    f.write(renderer.render(session))
        except Exception:
            pass  # don't let output writing crash teardown


@pytest.fixture(autouse=True)
def cleanup_stores():
    """Remove any leftover test stores before and after each test."""
    yield
    rmtree_robust_glob(f"{STORE_PREFIX}/*")
    rmtree_robust_glob("/tmp/pynixd-test-*")


# ── Non-autouse fixtures ──────────────────────────────────────────


@pytest.fixture(scope="session")
def anyio_backend() -> tuple[str, dict[str, bool]]:
    """Override anyio's default backend fixture to use uvloop (session-scoped)."""
    return ("asyncio", {"use_uvloop": True})


@pytest.fixture
def tmp_path(request: pytest.FixtureRequest) -> Generator[Path]:
    """Override pytest's tmp_path to use rmtree_robust for teardown.

    Uses a dedicated prefix to avoid pytest's shutil.rmtree which
    fails on read-only Nix store files.
    """
    suffix = f"{request.node.name}-{random.getrandbits(32):08x}"
    path = Path(f"/tmp/pynixd-test-{suffix}")
    path.mkdir(parents=True, exist_ok=True)
    yield path
    with suppress(Exception):
        rmtree_robust(path)


@pytest.fixture
async def cleanup_extra_stores(pynixd_server: Server | tuple | None):
    """Remove non-default stores added by tests between each test.

    Not autouse — sync tests cannot consume async fixtures without
    pytest-asyncio.  The sync ``cleanup_stores`` fixture handles
    basic store directory cleanup for all tests.
    """
    yield
    if pynixd_server is None:
        return

    actual_server = pynixd_server[0] if isinstance(pynixd_server, tuple) else pynixd_server
    if not hasattr(actual_server, "stores"):
        return

    extra_ids = [sid for sid in actual_server.stores if sid not in _default_store_ids]

    for sid in extra_ids:
        store = actual_server.stores[sid]
        store_path = getattr(store, "store_path", None)
        await actual_server.remove_store(sid)
        if store_path and str(store_path).startswith(str(SESSION_STORE_PREFIX)):
            await asyncio.to_thread(rmtree_robust, store_path)
