"""Shared CLI helpers for pynixd subcommands."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import structlog

from ..config import PynixdSettings
from ..plugins import import_plugin

if TYPE_CHECKING:
    from collections.abc import Callable


_DROP_SENTINEL = "_pynixd_drop"


def _load_filter_from_plugins(plugins: list) -> Callable | None:
    """Import each plugin path and return the first callable named ``filter``."""
    log = structlog.get_logger(__name__)
    for path in plugins:
        module = import_plugin(path)
        if module is None:
            continue
        if hasattr(module, "filter") and callable(module.filter):
            return module.filter
        if callable(module):
            return module  # type: ignore[return-value]
        log.warning("log_filter_plugin_missing_filter", path=str(path))
    return None


def _build_filter_processor(filter_fn: Callable) -> Callable:
    """Wrap a plugin filter for the structlog chain.

    Raises ``DropEvent`` when the filter drops the event.
    """

    def _proc(logger: object, method_name: str, event_dict: dict) -> dict:
        try:
            result = filter_fn(logger, method_name, event_dict)
        except structlog.DropEvent:
            raise
        except Exception:
            return event_dict
        if not result:
            raise structlog.DropEvent
        if isinstance(result, dict):
            return result
        return event_dict

    return _proc


def _build_foreign_filter_processor(filter_fn: Callable) -> Callable:
    """Wrap a plugin filter for ``ProcessorFormatter.foreign_pre_chain``.

    ``ProcessorFormatter`` does not catch ``DropEvent``, so this variant
    returns a sentinel dict ``{"_pynixd_drop": True}``.  The renderer
    in ``processors`` detects the sentinel and discards the entry.
    """

    def _proc(logger: object, method_name: str, event_dict: dict) -> dict:
        try:
            result = filter_fn(logger, method_name, event_dict)
        except structlog.DropEvent:
            return {**event_dict, _DROP_SENTINEL: True}
        except Exception:
            return event_dict
        if not result:
            return {**event_dict, _DROP_SENTINEL: True}
        if isinstance(result, dict):
            return result
        return {**event_dict, _DROP_SENTINEL: True}

    return _proc


def _build_processors(is_foreign: bool, filter_fn: Callable | None) -> list:
    """Build the structlog processor chain.

    Args:
        is_foreign: True for ``ProcessorFormatter.processors`` (formatter
            downstream), False for the structlog.configure processor list.
        filter_fn: Plugin filter callable or None.
    """
    procs: list = []
    if is_foreign:
        # Shared formatter chain: strip metadata, render.
        procs.append(_DropFilteringRenderer())
    else:
        # Structlog-native chain.
        procs.extend(
            [
                structlog.stdlib.filter_by_level,
                structlog.stdlib.add_logger_name,
                structlog.stdlib.add_log_level,
                structlog.contextvars.merge_contextvars,
            ],
        )
        if filter_fn is not None:
            procs.append(_build_filter_processor(filter_fn))
        procs.extend(
            [
                structlog.processors.TimeStamper(fmt="iso"),
                structlog.processors.StackInfoRenderer(),
                structlog.processors.format_exc_info,
                structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
            ],
        )
    return procs


def _build_foreign_pre_chain(filter_fn: Callable | None) -> list:
    """Build ``foreign_pre_chain`` for ``ProcessorFormatter``.

    These processors run only on non-structlog log records and prepare
    the event dict for the shared rendering chain.
    """
    chain: list = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
    ]
    if filter_fn is not None:
        chain.append(_build_foreign_filter_processor(filter_fn))
    return chain


class _DropFilteringRenderer:
    """A ``ProcessorFormatter`` processor that renders to JSON.

    Strips ``_record`` and ``_from_structlog`` metadata added by the
    formatter, and discards entries marked with the filter sentinel.
    The sentinel is set by the plugin filter in ``foreign_pre_chain``.
    """

    def __init__(self) -> None:
        self._json = structlog.processors.JSONRenderer()

    def __call__(self, logger: object, method_name: str, event_dict: dict) -> str:
        if event_dict.get(_DROP_SENTINEL):
            return ""
        event_dict.pop("_record", None)
        event_dict.pop("_from_structlog", None)
        rendered = self._json(logger, method_name, event_dict)
        if isinstance(rendered, bytes):
            return rendered.decode("utf-8")
        return rendered


def setup_logging(settings: PynixdSettings) -> None:
    """Configure structlog and stdlib logging with plugin-defined filtering."""
    log_level_str = settings.log_level.upper()
    filter_fn = _load_filter_from_plugins(settings.plugins)

    # ── Structlog configure ────────────────────────────────────────
    structlog.configure(
        processors=_build_processors(is_foreign=False, filter_fn=filter_fn),
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # ── Stdlib bridge via ProcessorFormatter ───────────────────────
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=_build_foreign_pre_chain(filter_fn),
        processors=_build_processors(is_foreign=True, filter_fn=filter_fn),
    )

    logging.basicConfig(
        level=log_level_str,
        handlers=[logging.StreamHandler()],
        force=True,
    )
    for handler in logging.getLogger().handlers:
        handler.setFormatter(formatter)


def load_settings() -> PynixdSettings:
    """Load and return pynixd settings from config files and environment variables."""
    return PynixdSettings()
