"""Automatic GC triggers share one cooldown window. Issue #72."""

from __future__ import annotations

import pytest

from pynixd.instance import _trigger_due


@pytest.mark.anyio
async def test_first_trigger_is_due() -> None:
    """No pass has run, so even a fresh boot may relieve a flood."""
    assert _trigger_due(10000.0, 0.0, 900.0)


@pytest.mark.anyio
async def test_recent_trigger_stands_down() -> None:
    """A hovering disk fires at most once per window, whichever rule asks."""
    assert not _trigger_due(500.0, 100.0, 900.0)


@pytest.mark.anyio
async def test_lapsed_window_fires_again() -> None:
    """The boundary itself is due: the comparison is inclusive."""
    assert _trigger_due(1000.0, 100.0, 900.0)


@pytest.mark.anyio
async def test_zero_cooldown_disables() -> None:
    """Every due trigger fires when the operator sets no window."""
    assert _trigger_due(100.0, 100.0, 0.0)
