"""Tests for parameterized log file naming."""

from __future__ import annotations

import pytest

from tests.conftest import run_subproc


@pytest.mark.parametrize("value", [1, 2, 3])
async def test_param(value: int) -> None:
    """Parameterized test to verify separate log files.

    Store operations triggered:
    - None: This test only checks parameterized logging without triggering Store operations
    """
    rc, stdout, _, _ = await run_subproc(["echo", str(value)])
    assert rc == 0
    assert stdout.strip() == str(value)
