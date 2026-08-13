"""
Shared application context for pynixd dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .serde.ids import StoreId

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .config import PynixdSettings
    from .local_store_db import LocalStoreDB
    from .scheduler import Scheduler
    from .store.base import Store
    from .store.local_daemon import LocalStore


@dataclass
class PynixdContext:
    """Encapsulates shared services and state for dependency injection.

    All stores (including local) live in ``_stores``. The ``local_store``
    property looks up ``StoreId(\"local\")`` in the dict.
    """

    settings: PynixdSettings
    _stores: dict[StoreId, Store]
    db: LocalStoreDB | None = None
    scheduler: Scheduler | None = None
    output_locations: dict[str, StoreId] = field(default_factory=dict)

    @property
    def local_store(self) -> LocalStore:
        """The primary local Nix daemon store for this context."""
        return self._stores[StoreId("local")]  # type: ignore[return-value]

    @property
    def stores(self) -> Mapping[StoreId, Store]:
        """Read-only view of all connected stores (including local)."""
        return self._stores
