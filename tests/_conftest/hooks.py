"""Pytest hooks: collection, session lifecycle, and reporting."""

from __future__ import annotations

import asyncio
import functools
import json
import time
from pathlib import Path

import pytest
import structlog

from tests._conftest.constants import _covered_features_key, _log_dir_key
from tests._conftest.subsumption import _sort_by_subsumption
from tests.test_features import TestFeatures

log = structlog.get_logger(__name__)


# ── Collection filtering ──────────────────────────────────────────


def pytest_ignore_collect(collection_path: Path) -> bool:
    """Skip integration test directories during normal collection."""
    return "nar_integration" in str(collection_path) or "drv_integration" in str(collection_path)


# ── Session lifecycle ─────────────────────────────────────────────


def pytest_sessionstart(session: pytest.Session) -> None:
    """Create session-wide log directory and clean up leftovers."""
    run_id = str(int(time.time()))
    log_dir = Path(f"/tmp/pynixd-logs/{run_id}")
    log_dir.mkdir(parents=True, exist_ok=True)
    session.config.stash[_log_dir_key] = log_dir

    tr = session.config.pluginmanager.get_plugin("terminalreporter")
    if tr:
        tr.write_line(f"\nIMPORTANT: Test run logs: {log_dir}")

    from tests._conftest.helpers import rmtree_robust_glob

    # Only the directories that this project makes. `/tmp/pytest-of-lillecarl/*`
    # was here as well, and that directory is the shared temporary root of
    # pytest: every suite of this repository puts its `tmp_path` under it. The
    # line therefore deleted the leftovers of another project at the start of
    # each run, and one of those leftovers was a root-owned overlayfs work
    # directory that no cleanup can remove.
    #
    # pytest keeps the last three roots of its own and removes the rest, so
    # nothing here has to.
    rmtree_robust_glob("/tmp/pynixd-test-*")


def pytest_terminal_summary(
    terminalreporter: pytest.TerminalReporter,
    exitstatus: int,
    config: pytest.Config,
) -> None:
    """Print log directory path at the end of the test run."""
    log_dir = config.stash.get(_log_dir_key, None)
    if log_dir:
        terminalreporter.write_line(f"\nIMPORTANT: Test run logs: {log_dir}")


# ── Collection / modification ─────────────────────────────────────


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]):
    """Wrap async tests in asyncio.timeout and sort by subsumption."""

    # Sort by descending covers-popcount so broad tests run first.
    if not config.getoption("no_test_subsumption"):
        _sort_by_subsumption(items)

    for item in items:
        if (
            isinstance(item, pytest.Function)
            and asyncio.iscoroutinefunction(item.obj)
            and not getattr(item.obj, "_pynixd_timeout_wrapped", False)
        ):
            item.obj = _wrap_with_asyncio_timeout(item)
            item.obj._pynixd_timeout_wrapped = True  # type: ignore[reportAttributeAccessIssue]


def _wrap_with_asyncio_timeout(item: pytest.Function):
    """Wrap an async test function with asyncio.timeout for timeout protection."""
    original_func = item.obj

    @functools.wraps(original_func)
    async def wrapped(*args, **kwargs):
        timeout = item.config.getoption("async_test_timeout")
        try:
            async with asyncio.timeout(timeout):
                return await original_func(*args, **kwargs)
        except TimeoutError:
            log.exception(
                "test_timeout_triggered",
                test=item.nodeid,
                timeout=timeout,
            )
            raise

    return wrapped


# ── Test reporting ────────────────────────────────────────────────


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call):
    """Record covered features for subsumption and write failure details to log file."""
    outcome = yield
    report = outcome.get_result()

    if report.when == "call":
        if report.passed and not item.config.getoption("no_test_subsumption"):
            marker = item.get_closest_marker("covers")
            if marker is not None and marker.args:
                features: TestFeatures = marker.args[0]
                covered = item.config.stash.get(_covered_features_key, TestFeatures(0))
                item.config.stash[_covered_features_key] = covered | features

        if report.failed:
            log_dir = item.config.stash.get(_log_dir_key, None)
            if log_dir:
                test_file = item.path.stem
                test_name = item.name.replace("/", "_").replace("[", "_").replace("]", "_")
                folder = log_dir / test_file / test_name
                folder.mkdir(parents=True, exist_ok=True)

                # Write exception to JSONL for machine consumption
                exc_info = call.excinfo
                if exc_info:
                    import traceback as tb

                    entry = {
                        "timestamp": f"{time.monotonic():.3f}",
                        "type": exc_info.type.__name__,
                        "message": str(exc_info.value),
                        "traceback": "".join(tb.format_exception(exc_info.type, exc_info.value, exc_info.tb)),
                    }
                    with (folder / "exceptions.jsonl").open("a") as f:
                        f.write(json.dumps(entry, default=str) + "\n")

                # Suppress traceback from CLI output; it's in exceptions.jsonl
                report.longrepr = f"logs: {folder}"
                report.sections = []
