"""The growth benchmark's verdict on synthetic rows.

`assess` is pure, so these run without a daemon: flat costs pass, a leak,
a slowdown, a wire mismatch, or a cap breach each fail by name.
"""

from __future__ import annotations

from tests.benchmark.growth_probe import assess


def _row(**overrides: float) -> dict[str, float]:
    row = {
        "wall": 10.0,
        "cpu": 8.0,
        "tm_current": 100_000_000.0,
        "tm_peak": 120_000_000.0,
        "gc_objects": 50_000.0,
        "rss": 400_000_000.0,
        "wire_in": 1000.0,
        "wire_out": 5_000_000.0,
        "log_lines": 50_000.0,
        "out_bytes": 1_048_576.0,
    }
    row.update(overrides)
    return row


def test_flat_costs_pass() -> None:
    assert assess([_row() for _ in range(5)]) == []


def test_empty_rows_fail() -> None:
    assert assess([]) == ["no measured builds"]


def test_growing_allocations_fail() -> None:
    rows = [_row(), _row(), _row(tm_current=150_000_000.0)]
    violations = assess(rows)
    assert len(violations) == 1
    assert "tm_current" in violations[0]


def test_growing_objects_fail() -> None:
    rows = [_row(), _row(gc_objects=60_000.0)]
    assert any("gc_objects" in violation for violation in assess(rows))


def test_wall_spike_fails() -> None:
    rows = [_row(), _row(wall=14.0)]
    assert any("wall" in violation for violation in assess(rows))


def test_wire_bytes_must_match_exactly() -> None:
    rows = [_row(), _row(wire_out=5_000_001.0)]
    violations = assess(rows)
    assert len(violations) == 1
    assert "wire_out" in violations[0]


def test_rss_slack_covers_arenas_but_not_leaks() -> None:
    assert assess([_row(), _row(rss=400_000_000.0 + 10 * 2**20)]) == []
    assert any("rss" in violation for violation in assess([_row(), _row(rss=600_000_000.0)]))
