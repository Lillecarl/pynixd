"""Rich tables for `pynixd state` on a terminal.

It walks the `StateReport` model: a list of rows is a table with a column
per field, rows keyed by name get a `name` column, and scalars become a
two-column table under their section's title.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from rich.console import Console, Group, RenderableType
from rich.table import Table
from rich.text import Text

from ..state_models import WAITS_FOR, BuildRow, FederatedError, StateReport


def render_state(report: StateReport, console: Console | None = None) -> None:
    """Print every section the report carries."""
    out = console or Console()
    for renderable in _sections(report, prefix=""):
        out.print(renderable)


def _sections(report: StateReport, prefix: str) -> list[RenderableType]:
    parts: list[RenderableType] = []
    for name in type(report).model_fields:
        value = getattr(report, name)
        if value is None:
            continue
        title = f"{prefix}{name}"
        if name == "federated":
            for store, answer in value.items():
                if isinstance(answer, FederatedError):
                    parts.append(Text(f"{title} / {store}: {answer.error}", style="red"))
                else:
                    parts.extend(_sections(answer, prefix=f"{title} / {store} / "))
            continue
        parts.append(_render(title, value))
    return parts


def _cell(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, dict):
        return ", ".join(f"{k}={_cell(v)}" for k, v in value.items()) or "-"
    if isinstance(value, BaseModel):
        return "(" + " ".join(f"{k}={_cell(v)}" for k, v in value) + ")"
    if isinstance(value, list):
        return ", ".join(_cell(v) for v in value) or "-"
    return str(value)


def _row_cells(row: BaseModel, columns: list[str]) -> list[str]:
    cells = []
    for column in columns:
        value = getattr(row, column)
        if isinstance(row, BuildRow) and column == "waits_for" and value is not None:
            value = WAITS_FOR.get(value, value)
        cells.append(_cell(value))
    return cells


def _table(title: str, rows: list[BaseModel], names: list[str] | None = None) -> Table:
    columns = list(type(rows[0]).model_fields)
    table = Table(title=title, title_justify="left")
    if names is not None:
        table.add_column("name", style="bold")
    for column in columns:
        table.add_column(column)
    for index, row in enumerate(rows):
        lead = [names[index]] if names is not None else []
        table.add_row(*lead, *_row_cells(row, columns))
    return table


def _pairs(title: str, pairs: dict[str, Any]) -> Table:
    table = Table(title=title, title_justify="left", show_header=False)
    table.add_column("key", style="bold")
    table.add_column("value")
    for key, value in pairs.items():
        table.add_row(str(key), _cell(value))
    return table


def _render(title: str, value: Any) -> RenderableType:
    if isinstance(value, list):
        return _table(title, value) if value else Text(f"{title}: none", style="dim")
    if isinstance(value, dict):
        if value and all(isinstance(v, BaseModel) for v in value.values()):
            return _table(title, list(value.values()), names=[str(k) for k in value])
        return _pairs(title, value) if value else Text(f"{title}: none", style="dim")
    if isinstance(value, BaseModel):
        scalars = {k: v for k, v in value if not isinstance(v, dict | list)}
        nested = [_render(f"{title} / {k}", v) for k, v in value if isinstance(v, dict | list)]
        return Group(_pairs(title, scalars), *nested) if nested else _pairs(title, scalars)
    return Text(f"{title}: {_cell(value)}")
