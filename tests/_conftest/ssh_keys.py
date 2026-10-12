"""Client keys for the SSH listener, which admits listed keys only. Issue #94.

The admin key is in `admin_keys`, so its sessions are `Role.ADMIN`; the user
key is in `user_keys`. A Nix client names its key in the store URL with
`?ssh-key=`.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

import asyncssh

from tests._conftest.constants import SESSION_STORE_PREFIX

if TYPE_CHECKING:
    from pathlib import Path

SESSION_SSH_KEYS = SESSION_STORE_PREFIX / "ssh"
"""The keys every `Server` of a test process admits. `install_session_keys` writes them."""


@dataclass(frozen=True)
class SSHTestKeys:
    admin: Path
    user: Path
    admin_keys: Path
    user_keys: Path


def make_ssh_test_keys(directory: Path) -> SSHTestKeys:
    """Write an admin key and a user key, and an `authorized_keys` file for each."""
    directory.mkdir(parents=True, exist_ok=True)
    written: dict[str, tuple[Path, Path]] = {}
    for name in ("admin", "user"):
        key = asyncssh.generate_private_key("ssh-ed25519")
        private = directory / f"id_{name}"
        private.unlink(missing_ok=True)
        key.write_private_key(str(private))
        # OpenSSH refuses a private key that others can read.
        private.chmod(0o600)
        listed = directory / f"{name}_keys"
        listed.write_bytes(key.export_public_key())
        written[name] = (private, listed)
    return SSHTestKeys(
        admin=written["admin"][0],
        user=written["user"][0],
        admin_keys=written["admin"][1],
        user_keys=written["user"][1],
    )


def install_session_keys() -> SSHTestKeys:
    """Write the session keys, and make every `Server` of this process admit them.

    Through the environment, which `PynixdSettings` reads, so a test that
    starts its own server needs no key setup. A setting passed to `Server`
    still wins.
    """
    keys = make_ssh_test_keys(SESSION_SSH_KEYS)
    os.environ["PYNIXD_SSH_USER_KEYS"] = json.dumps([str(keys.user_keys)])
    os.environ["PYNIXD_SSH_ADMIN_KEYS"] = json.dumps([str(keys.admin_keys)])
    return keys
