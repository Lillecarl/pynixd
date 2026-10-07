"""Connect to a Unix socket Python would otherwise be unable to reach.

`sockaddr_un.sun_path` holds 108 bytes on Linux and 104 on darwin, and an
absolute path over that fails `connect` with `OSError: AF_UNIX path too
long`. Nix binds such a path anyway: `bindConnectProcHelper`
(`src/libutil/unix/unix-domain-socket.cc`) forks a helper that `chdir`s
into the directory and binds the base name, so the length of the directory
stops mattering. The socket file then exists at the long path, and only the
client side cannot reach it.

On Linux the connect is rerouted through the directory itself: the
directory is opened once with `O_PATH`, and the base name is addressed as
`/proc/self/fd/<n>/<basename>`, which is short whatever the directory is.
The descriptor is only needed while `connect` resolves the path, so it
closes as soon as the connection is made. Any other platform keeps the
refusal, because there is no `/proc/self/fd` to reroute through. A base
name over the limit is refused everywhere: even the helper that Nix binds
with cannot bind that. Issue #44.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

_SUN_PATH_MAX = 107
"""Bytes of a Unix socket path, without the terminating NUL.

`sockaddr_un.sun_path` is 108 bytes on Linux and 104 on darwin, and the
shorter of the two is the safe bound for a store that a darwin client may
reach. This is 107 because Linux is where the managed daemon runs, and the
error below names the number it measured. Issue #44.
"""


def _refusal(socket_path: Path, measured: int) -> RuntimeError:
    """The error for a path no connect can reach, with both numbers in it."""
    return RuntimeError(
        f"The socket path is {measured} bytes and a Unix socket takes {_SUN_PATH_MAX}: "
        f"{socket_path}. Nix binds such a path with a helper that chdirs, so the socket "
        "may exist and still be unreachable from here. Put the store somewhere shorter.",
    )


async def open_unix_connection(socket_path: Path) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Connect to `socket_path`, rerouting an over-long one on Linux.

    A path within the limit connects directly. A longer one connects
    through `/proc/self/fd`, as the module docstring says. A base name over
    the limit, or a long path off Linux, raises `RuntimeError` with the
    numbers rather than failing later with `OSError: AF_UNIX path too long`.
    """
    if len(os.fsencode(str(socket_path))) <= _SUN_PATH_MAX:
        return await asyncio.open_unix_connection(str(socket_path))
    if len(os.fsencode(socket_path.name)) > _SUN_PATH_MAX:
        raise _refusal(socket_path, len(os.fsencode(str(socket_path))))
    if sys.platform != "linux":
        raise _refusal(socket_path, len(os.fsencode(str(socket_path))))
    fd = os.open(socket_path.parent, os.O_PATH | os.O_CLOEXEC)
    try:
        return await asyncio.open_unix_connection(f"/proc/self/fd/{fd}/{socket_path.name}")
    finally:
        os.close(fd)
