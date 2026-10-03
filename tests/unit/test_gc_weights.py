"""The collector deletes heaviest first, and pressure bounds the pass.

`_heaviest_first` and `_take_until_below_target` are pure on purpose: the
order a pass deletes in is asserted here, without a daemon, a store, or a
disk. What they order -- sizes from the path infos, ages from the access
table -- is read elsewhere; these two only decide.
"""

from __future__ import annotations

from pynixd.gc import _heaviest_first, _take_until_below_target


def test_size_leads() -> None:
    """One big delete frees what dozens of small ones do."""
    assert _heaviest_first({"small": (1, 9999), "big": (10**9, 0)}) == ["big", "small"]


def test_age_breaks_size_ties() -> None:
    """Equals in size go oldest first: the longer unseen is the safer delete."""
    assert _heaviest_first({"fresh": (100, 10), "stale": (100, 10**6)}) == ["stale", "fresh"]


def test_empty_weights_delete_nothing() -> None:
    assert _heaviest_first({}) == []


def test_a_target_already_met_takes_nothing() -> None:
    """Usage under the target is a pass that stays home."""
    assert _take_until_below_target([("a", 100)], used=10, total=100, target=0.5) == []


def test_the_walk_stops_at_the_first_prefix_under_target() -> None:
    """Pressure bounds the pass: two big deletes relieve what the plan would
    have emptied at once."""
    ordered = [("big", 40), ("mid", 30), ("small", 20)]
    assert _take_until_below_target(ordered, used=95, total=100, target=0.5) == ["big", "mid"]


def test_a_target_no_prefix_meets_takes_everything() -> None:
    assert _take_until_below_target([("a", 10)], used=100, total=100, target=0.0) == ["a"]


def test_exactly_at_target_takes_nothing_more() -> None:
    """Under, not under-or-equal: at the line the disk is relieved already."""
    assert _take_until_below_target([("a", 50)], used=50, total=100, target=0.5) == []
