"""The test config names the host's daemon only when the host has one.

`DEFAULT_SUBSTITUTERS` listed `unix:///nix/var/nix/daemon-socket/socket?root=/`
unconditionally. A multi-user NixOS machine has that socket; a GitHub runner
does not. Every build in the suite therefore opened its log with

    error: cannot connect to socket at
    '/nix/var/nix/daemon-socket/socket': No such file or directory

in CI and nowhere else, which is why no developer ever saw it. It surfaced as
the first line of a capability probe's build log in CI run 35192705495, once a
refused probe started carrying that log. Issue #47.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests._conftest import nix_config

if TYPE_CHECKING:
    import pytest


def test_the_socket_is_named_when_it_exists(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    present = tmp_path / "socket"
    present.touch()
    monkeypatch.setattr(nix_config, "HOST_DAEMON_SOCKET", present)
    assert nix_config.host_daemon_substituters() == (nix_config.HOST_DAEMON_SUBSTITUTER,)


def test_the_socket_is_left_out_when_it_does_not_exist(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(nix_config, "HOST_DAEMON_SOCKET", tmp_path / "absent")
    assert nix_config.host_daemon_substituters() == ()


def test_a_missing_socket_never_reaches_a_config(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """The shape that mattered: a substituter that cannot answer, in a config."""
    monkeypatch.setattr(nix_config, "HOST_DAEMON_SOCKET", tmp_path / "absent")
    rendered = nix_config.for_test_store(
        substituters=("https://nixkube.cachix.org/", *nix_config.host_daemon_substituters()),
    ).to_nix_config_env()
    assert "daemon-socket" not in rendered
    assert "nixkube.cachix.org" in rendered, "the caches that do answer stay"
