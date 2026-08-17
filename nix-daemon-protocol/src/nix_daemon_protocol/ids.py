"""Strongly-typed identifiers for stores, builds, and requests.

.. py:data:: StoreId

    Opaque identifier for a Nix store backend (e.g. ``"local"``, ``"ssh-builder1"``).

.. py:data:: BuildId

    Opaque identifier for an in-flight build in the build queue.

.. py:data:: RequestId

    Opaque identifier for a client request being tracked by the proxy.
"""

from __future__ import annotations

from typing import NewType

StoreId = NewType("StoreId", str)
BuildId = NewType("BuildId", int)
RequestId = NewType("RequestId", int)

LOCAL_STORE_ID = StoreId("local")
"""The identifier of the store that runs beside pynixd itself.

Every deployment has this store, and pynixd makes one when the configuration
names none. Sixteen sites wrote `StoreId("local")` or the bare string
`"local"`, so a reader had to know that the two spellings mean one thing."""
