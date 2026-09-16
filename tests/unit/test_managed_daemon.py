"""`LocalSocketStoreSpec.managed` says who runs the daemon, when the path cannot.

`LocalStore` derived `managed` from the store path alone: a relocated store, or
a root that is not `/`, is ours to serve. That reads a store at `/` as the store
of the machine, whose database is protected and cannot safely be served by an
unprivileged private daemon.

A container breaks that premise. nixkube's builder Pods each mount a
copy-on-write volume seeded from the node store, so the store sits at `/` with
the same prefix and the same path hashes as everyone else's, with its own
`db/db.sqlite`, root in its own namespace, and no system daemon. `ensure_daemon`
then raised instead of spawning one, and every builder died at startup.

Relocating the store would have made the inference true, and would have changed
the store prefix and therefore every path hash -- giving up the path identity
that makes the volume worth having, to signal something unrelated to it.

See nixkube issue #19.
"""

from __future__ import annotations

from pathlib import Path

from nix_daemon_protocol.ids import StoreId
from pynixd.config import LocalSocketStoreSpec
from pynixd.store.local_daemon import LocalStore

SYSTEM_SOCKET = Path("/nix/var/nix/daemon-socket/socket")


def _store(**kwargs: object) -> LocalStore:
    return LocalStore(LocalSocketStoreSpec(store_id=StoreId("local"), **kwargs))  # type: ignore[arg-type]


def test_a_root_store_still_wants_the_system_daemon_by_default() -> None:
    """The inference is unchanged when `managed` is not set."""
    store = _store(store_path=Path("/"))
    assert store.managed is False
    assert store.socket_path == SYSTEM_SOCKET


def test_a_chroot_store_is_still_ours_by_default() -> None:
    """A root that is not `/` is served by us, as before."""
    store = _store(store_path=Path("/var/lib/builder"))
    assert store.managed is True
    assert store.socket_path != SYSTEM_SOCKET


def test_managed_true_serves_a_root_store_ourselves() -> None:
    """The builder Pod case: a store at `/` with no system daemon."""
    store = _store(store_path=Path("/"), managed=True)
    assert store.managed is True
    assert store.socket_path != SYSTEM_SOCKET, (
        "a managed store must not point at the system socket, because nothing is listening on it"
    )


def test_managed_false_demands_the_system_daemon() -> None:
    """The override works in both directions, not only towards `True`."""
    store = _store(store_path=Path("/var/lib/builder"), managed=False)
    assert store.managed is False
    assert store.socket_path == SYSTEM_SOCKET


def test_an_absolute_socket_path_still_wins() -> None:
    """`managed` picks who runs the daemon, not where it listens."""
    chosen = Path("/run/pynixd/sock")
    store = _store(store_path=Path("/"), managed=True, socket_path=chosen)
    assert store.socket_path == chosen
