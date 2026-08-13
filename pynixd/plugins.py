"""Pluggable Python module loading for pynixd.

Imports Python modules from arbitrary filesystem paths without
writing .pyc files (sets ``sys.dont_write_bytecode`` during import).
Used by configuration-driven features such as log filtering and
future custom ranking or dynamic feature providers.

Consumers call ``import_plugin(path)`` and inspect the returned
module for the callables or attributes they need.
"""

from __future__ import annotations

import importlib.util
import sys
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType

log = structlog.get_logger(__name__)


def import_plugin(path: Path) -> ModuleType | None:
    """Import a Python module from an arbitrary filesystem path.

    Returns the imported module or ``None`` on failure.
    Uses ``spec_from_file_location`` so the module is not tied to
    ``sys.path``.  ``sys.dont_write_bytecode`` is set to ``True``
    during import to avoid leaving ``.pyc`` files.

    The module is registered under a fixed name (``pynixd_plugin``)
    so repeated calls replace the previous plugin.
    """
    spec = importlib.util.spec_from_file_location("pynixd_plugin", str(path))
    if spec is None or spec.loader is None:
        log.warning(
            "plugin_import_failed",
            path=str(path),
            reason="spec_from_file_location failed",
        )
        return None

    module = importlib.util.module_from_spec(spec)

    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    except Exception:
        log.warning("plugin_import_failed", path=str(path), exc_info=True)
        return None
    finally:
        sys.dont_write_bytecode = False

    return module
