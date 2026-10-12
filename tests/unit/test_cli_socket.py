"""The CLI finds the pynixd that serves Nix's own socket. Issue #90.

`pynixd state` dialled `unix_path` alone, whose default is
`/run/pynixd/pynixd.sock`. dynhetz runs pynixd on
`/nix/var/nix/daemon-socket/socket`, so the command failed there for every
user, and `NIX_REMOTE` changed nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pynixd.cli.base import NIX_DAEMON_SOCKET, PYNIXD_SOCKET, socket_candidates
from pynixd.config import PynixdSettings


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("NIX_REMOTE", "NIX_DAEMON_SOCKET_PATH", "PYNIXD_UNIX_PATH"):
        monkeypatch.delenv(name, raising=False)


def test_the_default_tries_pynixd_then_nix() -> None:
    assert socket_candidates(PynixdSettings(), None) == [PYNIXD_SOCKET, NIX_DAEMON_SOCKET]


def test_a_named_socket_is_the_only_candidate() -> None:
    assert socket_candidates(PynixdSettings(), Path("/x.sock")) == [Path("/x.sock")]


def test_unix_path_from_the_environment_is_the_only_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYNIXD_UNIX_PATH", "/env.sock")
    assert socket_candidates(PynixdSettings(), None) == [Path("/env.sock")]


def test_nix_remote_unix_comes_first(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NIX_REMOTE", "unix:///remote.sock?trusted=1")
    assert socket_candidates(PynixdSettings(), None) == [Path("/remote.sock"), PYNIXD_SOCKET, NIX_DAEMON_SOCKET]


def test_nix_daemon_socket_path_moves_nix_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NIX_DAEMON_SOCKET_PATH", "/moved.sock")
    assert socket_candidates(PynixdSettings(), None) == [PYNIXD_SOCKET, Path("/moved.sock")]
