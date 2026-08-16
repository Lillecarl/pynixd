"""Gates and fixtures for the differential suite.

Three things have to be true before a differential test can say anything, and
each one is a skip rather than a failure:

1. The host is Linux. A chroot store cannot *build* anywhere else -- Nix says
   `building using a diverted store is not supported on this platform`. The
   whole design of this suite rests on two separated stores, so on darwin
   there is nothing to run. Use `vzrun` there.
2. nanopynix is installed. It is the oracle, and it is a dependency of this
   suite alone. `pynixd` itself does not depend on it and must not: the
   shipped proxy is pure Python and links no C++.
3. The case's experimental features are enabled in the linked Nix.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from typing import TYPE_CHECKING

import pytest

from tests._conftest.constants import STORE_PREFIX
from tests._conftest.helpers import rmtree_robust

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path

# The root every differential store lives under. Separate from the rest of the
# suite's prefix, so a teardown here cannot remove a store the session server
# is using.
#
# Short on purpose. `LocalDaemon` puts its socket at
# `<store>/nix/var/nix/daemon-socket/pynixd-nix`, and a Unix socket path cannot
# exceed 108 bytes. `_SOCKET_BUDGET` below states that, and the fixture checks
# it, because the failure it produces otherwise says nothing: the daemon starts,
# the socket file never appears, `ensure_daemon` probes for five seconds and
# then raises about a socket "not accepting connections".
DIFFERENTIAL_PREFIX = STORE_PREFIX.parent / "pynixd-diff"

# The suffix `LocalDaemon` appends to the store path, and the limit it must fit
# in. `sun_path` is `char[108]` on Linux and `char[104]` on darwin, and the
# smaller one is used here so a path that passes on one host passes on both.
_DAEMON_SOCKET_SUFFIX = "nix/var/nix/daemon-socket/pynixd-nix"
_SUN_PATH_LIMIT = 104

_NANOPYNIX_PRESENT = importlib.util.find_spec("nanopynix") is not None


def _unmet_requirement() -> str | None:
    """Why this host cannot answer the question, or `None` when it can."""
    if sys.platform != "linux":
        return (
            "a chroot store cannot build off Linux "
            "(`building using a diverted store is not supported on this platform`); "
            "run this suite under vzrun"
        )
    if not _NANOPYNIX_PRESENT:
        return (
            "nanopynix is the oracle of this suite and is not installed; "
            "it is a test dependency only, and pynixd itself does not depend on it"
        )
    if shutil.which("nix-instantiate") is None:
        return "nix-instantiate is not on PATH, and both arms instantiate with it"
    return None


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark every test in this directory, and skip the lot on a host that cannot run it.

    The skip is added here, and not raised from a fixture, because a fixture is
    too late. `pynixd_server` in the conftest above is session-scoped and
    autouse, so pytest sets it up before any fixture of this directory, and on
    darwin it fails rather than skips -- it wants a chroot-store daemon, which
    is the very thing this platform cannot do. A test that carries a skip
    marker never reaches fixture setup at all, so the outer fixture is never
    built.
    """
    reason = _unmet_requirement()
    for item in items:
        if "differential" not in str(item.path.parent):
            continue
        item.add_marker(pytest.mark.differential)
        if reason is not None:
            item.add_marker(pytest.mark.skip(reason=reason))


@pytest.fixture
async def differential_roots(request: pytest.FixtureRequest) -> AsyncGenerator[tuple[Path, Path]]:
    """Two empty store roots for one case: the pynixd arm and the Nix arm.

    Named after the case, so a failed run leaves two directories a person can
    open and compare by hand.

    The name is the parameter id and not `request.node.name`. The node name
    carries the function name too, which is 38 characters here, and the socket
    that `LocalDaemon` puts under the pynixd root then passes 108 bytes and
    cannot be bound. That was measured, not guessed: the first run of this
    suite produced a 114-byte path and `AF_UNIX path too long`.
    """
    callspec = getattr(request.node, "callspec", None)
    stem = callspec.id if callspec is not None else request.node.name
    stem = stem.replace("/", "_").replace("[", "-").replace("]", "")
    root_a = DIFFERENTIAL_PREFIX / stem / "pynixd"
    root_b = DIFFERENTIAL_PREFIX / stem / "nix"

    socket_path = root_a / _DAEMON_SOCKET_SUFFIX
    if len(str(socket_path)) > _SUN_PATH_LIMIT:
        pytest.fail(
            f"the daemon socket of this case would be {len(str(socket_path))} bytes, "
            f"and a Unix socket path holds {_SUN_PATH_LIMIT}. Shorten the case name "
            f"or `DIFFERENTIAL_PREFIX`.\n  {socket_path}"
        )

    for root in (root_a, root_b):
        rmtree_robust(root)
        root.mkdir(parents=True, exist_ok=True)
    yield root_a, root_b
