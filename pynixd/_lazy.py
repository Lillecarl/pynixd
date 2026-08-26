"""Names that belong to a module which a configuration may never load.

`pynixd daemon` over a Unix socket uses no SSH and no HTTP binary cache.
Loading either costs 0.28 s of the 1.28 s that a daemon start takes, and Nix's
functional suite restarts the daemon 344 times, so the two imports were about
96 s of a gate. Issue #290 holds the measurement.

**A class that nothing imported has no instance, and raises nothing.** That is
what makes the answer here exact rather than a guess. This module reads
`sys.modules` and imports nothing, so an absent module is an answer and not a
failure.
"""

from __future__ import annotations

import sys

NOTHING: tuple[type[BaseException], ...] = ()
"""An `except` clause that catches no exception at all."""


def ssh_errors() -> tuple[type[BaseException], ...]:
    """`asyncssh.misc.Error`, the base of every error that asyncssh raises.

    Empty when nothing imported `asyncssh`, for the reason above. `DaemonStore`
    adds it to the transport errors that make it retire a connection.
    """
    asyncssh = sys.modules.get("asyncssh")
    if asyncssh is None:
        return NOTHING
    return (asyncssh.misc.Error,)


def ssh_connection_lost() -> tuple[type[BaseException], ...]:
    """`asyncssh.misc.ConnectionLost`, for an `except` clause.

    Empty when nothing imported `asyncssh`. No SSH connection is open then, so
    no such exception can be in flight, and an empty tuple catches nothing.

    The name is necessary, because `ConnectionLost` subclasses `Exception`
    alone. No wider `except` already covers it.
    """
    asyncssh = sys.modules.get("asyncssh")
    if asyncssh is None:
        return NOTHING
    return (asyncssh.misc.ConnectionLost,)
