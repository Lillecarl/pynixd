"""A store under a long path fails at once, and says what is long about it.

`sockaddr_un.sun_path` is 108 bytes on Linux. Nix binds a longer path by
forking a helper that `chdir`s into the directory and binds the base name
(`bindConnectProcHelper`, `src/libutil/unix/unix-domain-socket.cc`), so the
socket file exists at the long path. Python has no such helper: an absolute
`socket.connect()` answers `OSError: AF_UNIX path too long`.

pynixd therefore waited its whole 30 second start-up budget and reported
`Managed daemon socket not accepting connections ... '(the daemon wrote
nothing)'`. Every word of that was true and none of it named the fault.
Issue #44.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path

import pytest

from pynixd.store.local_daemon import _SUN_PATH_MAX, _refuse_a_socket_path_python_cannot_reach


def _a_path_of(length: int) -> Path:
    """An absolute path of exactly `length` bytes."""
    head = "/tmp/"
    return Path(head + "a" * (length - len(head)))


class TestTheGuard:
    def test_a_long_path_is_refused(self):
        with pytest.raises(RuntimeError, match="Unix socket takes"):
            _refuse_a_socket_path_python_cannot_reach(_a_path_of(_SUN_PATH_MAX + 1))

    def test_the_error_carries_both_numbers(self):
        long_one = _a_path_of(200)

        with pytest.raises(RuntimeError) as caught:
            _refuse_a_socket_path_python_cannot_reach(long_one)

        assert "200 bytes" in str(caught.value)
        assert str(_SUN_PATH_MAX) in str(caught.value)

    def test_a_path_at_the_limit_passes(self):
        """The negative control. A guard that refuses everything would pass
        the case above and break every ordinary store."""
        _refuse_a_socket_path_python_cannot_reach(_a_path_of(_SUN_PATH_MAX))

    def test_an_ordinary_path_passes(self):
        _refuse_a_socket_path_python_cannot_reach(Path("/nix/var/nix/daemon-socket/socket"))


class TestTheTestStoresFit:
    def test_the_longest_test_name_still_leaves_room(self, tmp_path):
        """`tests/_conftest/fixtures.py` truncates a test's name so that the
        daemon socket of its store fits. This is that budget, measured
        against the real fixture rather than against its arithmetic."""
        socket_of_a_store = tmp_path / "store/nix/var/nix/daemon-socket/pynixd-nix"

        _refuse_a_socket_path_python_cannot_reach(socket_of_a_store)


class TestTheLimitIsReal:
    """The constant is measured against the kernel, not copied from a header.

    A number nobody checks drifts. These two run in a hundredth of a second
    and they are what makes `_SUN_PATH_MAX` a fact rather than a belief.
    """

    def test_the_kernel_refuses_one_byte_more(self, tmp_path):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            with pytest.raises(OSError, match="too long"):
                sock.bind(str(_a_path_of(_SUN_PATH_MAX + 1)))
        finally:
            sock.close()

    def test_the_kernel_takes_the_limit_itself(self, tmp_path):
        # Inside tmp_path, so the bind leaves nothing behind. The name is
        # padded to reach the limit exactly.
        room = _SUN_PATH_MAX - len(os.fsencode(str(tmp_path))) - 1
        if room < 1:
            pytest.skip(f"tmp_path is already {len(os.fsencode(str(tmp_path)))} bytes")
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.bind(str(tmp_path / ("s" * room)))
        finally:
            sock.close()
