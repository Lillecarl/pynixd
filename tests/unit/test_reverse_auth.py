"""The reverse path authenticates one way: the acceptor names builders.

The builder serves whatever answers its dial — the dial travels an
already-authenticated tunnel, and the builder's own host key is ephemeral
by default — so no controller pin exists on that side. Issue #75.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import asyncssh
import pytest

from pynixd.reverse_client import _ReverseSSHServer
from pynixd.reverse_server import _known_hosts_matcher
from pynixd.ssh_auth import load_pinned_keys

if TYPE_CHECKING:
    from pathlib import Path


def _key() -> asyncssh.SSHKey:
    return asyncssh.import_public_key(asyncssh.generate_private_key("ssh-ed25519").export_public_key())


def _keypair() -> tuple[asyncssh.SSHKey, asyncssh.SSHKey]:
    """Two distinct objects over one key: identity never matches for free."""
    public_bytes = asyncssh.generate_private_key("ssh-ed25519").export_public_key()
    return asyncssh.import_public_key(public_bytes), asyncssh.import_public_key(public_bytes)


def test_builder_serves_any_controller_key() -> None:
    """The builder performs no client authentication; the acceptor does."""
    assert _ReverseSSHServer().validate_public_key("controller", _key()) is True


def test_acceptor_trusts_the_pinned_builder_key() -> None:
    presented, pinned = _keypair()
    assert presented is not pinned
    trusted, _, _ = _known_hosts_matcher([pinned])("builder", "10.0.0.2", 2235)
    assert presented in trusted


def test_acceptor_trusts_no_other_builder_key() -> None:
    trusted, _, _ = _known_hosts_matcher([_key()])("builder", "10.0.0.2", 2235)
    assert _key() not in trusted


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
