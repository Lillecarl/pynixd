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
