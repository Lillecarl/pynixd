"""Authentication related types."""

from __future__ import annotations

from enum import IntEnum


class Role(IntEnum):
    """Client authorization roles."""

    USER = 0
    ADMIN = 1
