"""The reverse path pins keys in both directions. Issue #75.

The initiator serves `nix-daemon --stdio` to whatever controller
connects, and the acceptor registers whatever builder dials in, so
an unpinned setup trusts the network. Pinned setups fail closed:
any other key is refused before any channel opens, on each side.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import asyncssh
import pytest

from pynixd.reverse_client import _ReverseSSHServer
from pynixd.ssh_auth import load_pinned_keys

if TYPE_CHECKING:
    from pathlib import Path


def _key() -> asyncssh.SSHKey:
    return asyncssh.import_public_key(asyncssh.generate_private_key("ssh-ed25519").export_public_key())


def _keypair() -> tuple[asyncssh.SSHKey, asyncssh.SSHKey]:
    """Two distinct objects over one key: identity never matches for free."""
    public_bytes = asyncssh.generate_private_key("ssh-ed25519").export_public_key()
    return asyncssh.import_public_key(public_bytes), asyncssh.import_public_key(public_bytes)


def test_pinned_server_accepts_the_pinned_key() -> None:
    presented, pinned = _keypair()
    assert presented is not pinned
    assert _ReverseSSHServer([pinned]).validate_public_key("builder", presented) is True


def test_pinned_server_rejects_any_other_key() -> None:
    server = _ReverseSSHServer([_key()])
    assert server.validate_public_key("builder", _key()) is False


def test_unpinned_server_accepts_any_key() -> None:
    """No pins is the explicit loopback opt-out, and it stays open."""
    assert _ReverseSSHServer().validate_public_key("builder", _key()) is True
    assert _ReverseSSHServer([]).validate_public_key("builder", _key()) is True


def test_pinned_keys_load_from_files(tmp_path: Path) -> None:
    """Several keys per file, several files: the union, in order."""
    first = asyncssh.generate_private_key("ssh-ed25519")
    second = asyncssh.generate_private_key("ssh-ed25519")
    one = tmp_path / "one.pub"
    two = tmp_path / "two.pub"
    first.write_public_key(one)
    second.write_public_key(two)

    assert load_pinned_keys([one, two]) == [
        asyncssh.import_public_key(first.export_public_key()),
        asyncssh.import_public_key(second.export_public_key()),
    ]


def test_missing_pin_file_raises(tmp_path: Path) -> None:
    """An unreadable pin fails, never an open setup."""
    with pytest.raises(OSError):
        load_pinned_keys([tmp_path / "absent.pub"])
