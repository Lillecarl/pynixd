"""Pinned SSH public keys for the reverse builders.

Both directions of the reverse path pin keys: the acceptor names the
builder host keys it registers, the initiator names the controller
keys it serves. Each side reads OpenSSH public key files —
`authorized_keys` format, several keys per file — and a missing file
raises instead of silently pinning nothing: an unreadable pin must
fail the component, never open it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import asyncssh

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path


def load_pinned_keys(paths: Iterable[Path]) -> list[asyncssh.SSHKey]:
    """Every key in every file of *paths*."""
    return [key for path in paths for key in asyncssh.read_public_key_list(str(path))]
