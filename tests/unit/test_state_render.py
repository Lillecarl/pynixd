"""`pynixd state` on a terminal renders each section of the report as a table."""

from __future__ import annotations

from rich.console import Console

from pynixd.cli.render import render_state
from pynixd.state_models import BuildRow, FederatedError, StateReport, StoreRow, UpstreamRow


def _text(report: StateReport) -> str:
    console = Console(record=True, width=240)
    render_state(report, console)
    return console.export_text()


def test_rows_become_one_table() -> None:
    row = UpstreamRow(store="local", conn="c1", op="IsValidPathRequest", op_s=13, silent_s=12.5)
    text = _text(StateReport(upstream=[row]))
    assert "upstream" in text
    assert "IsValidPathRequest" in text
    assert "12.5" in text


def test_a_pending_build_says_what_it_waits_for_in_words() -> None:
    row = BuildRow(id=3, drv="/nix/store/x.drv", state="pending", age_s=1, requests={7: 1}, waits_for="local_slot")
    assert "a local slot" in _text(StateReport(builds=[row]))


def test_rows_keyed_by_name_get_a_name_column() -> None:
    text = _text(StateReport(stores={"local": StoreRow(healthy=True, systems=["x86_64-linux"])}))
    assert "name" in text
    assert "local" in text
    assert "x86_64-linux" in text


def test_an_empty_section_says_none_and_a_failed_store_says_why() -> None:
    text = _text(StateReport(builds=[], federated={"old": FederatedError(error="state op not implemented")}))
    assert "builds: none" in text
    assert "federated / old: state op not implemented" in text
