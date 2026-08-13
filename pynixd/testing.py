"""Internal instrumentation for pynixd tests.

Provides a global stash for recording events or state during test execution.
All functions are NOOPs when not running under pytest.
"""

from __future__ import annotations

import os
from typing import Any

# Detection: check for the environment variable set by pytest
_IS_TESTING = "PYTEST_VERSION" in os.environ

_stash: dict[str, Any] = {}


def set_test_value(key: str, value: Any) -> None:
    """Record a value for test assertion. NOOP in production."""
    if _IS_TESTING:
        _stash[key] = value


def get_test_value(key: str, default: Any = None) -> Any:
    """Retrieve a recorded value. Returns default if not testing or key missing."""
    if not _IS_TESTING:
        return default
    return _stash.get(key, default)


def clear_test_stash() -> None:
    """Clear all recorded values. NOOP in production."""
    if _IS_TESTING:
        _stash.clear()
