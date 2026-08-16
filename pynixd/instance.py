"""Core pynixd instance orchestration."""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

import anyio
import structlog

from . import wire
from .config import ExternalUnixStoreSpec, HTTPBinaryCacheSpec, LocalSocketStoreSpec, PynixdSettings
from .context import PynixdContext
from .http_server import PynixdHttpServer
from .reverse_client import ReverseInitiator
from .reverse_server import start_reverse_acceptor
from .scheduler import Scheduler
from .serde import PynixdCollectGarbageRequest
from .serde.ids import StoreId
from .serde.protocol import PynixdGCAction
from .ssh_server import start_ssh_server
from .store import DaemonStore, ExternalUnixStore, HTTPBinaryCacheStore, LocalDBStore, LocalStore, Store
from .unix_server import start_unix_server

if TYPE_CHECKING:
    from collections.abc import Mapping

    import asyncssh
    from aiohttp import web

log = structlog.get_logger(__name__)

# The size of `sun_path` in `struct sockaddr_un`, per platform. The kernel
# copies the path into that array, so a longer one cannot be bound at all.
# `sys.platform` and not a `uname` call, because the value is a property of
# the C library this process was built against.
_SUN_PATH_LIMIT = {"linux": 108, "darwin": 104}
_SUN_PATH_LIMIT_DEFAULT = 104
"""The smaller of the two, for a platform this table does not name. A path
that a stricter limit accepts is bindable everywhere."""


def _default_http_substituter_urls(local_store: Store) -> list[str]:
    nix_config = getattr(local_store, "nix_config", None)
    urls = [url for url in getattr(nix_config, "substituters", None) or [] if url.startswith(("http://", "https://"))]
    urls.append("https://cache.nixos.org/")

    seen: set[str] = set()
    result: list[str] = []
    for url in urls:
        key = url.rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        result.append(url)
    return result


def _default_unix_substituters(local_store: Store) -> list[tuple[Path, Path]]:
    urls: list[tuple[Path, Path]] = []
    nix_config = getattr(local_store, "nix_config", None)
    for url in getattr(nix_config, "substituters", None) or []:
        split = urlsplit(url)
        if split.scheme != "unix":
            continue
        socket_path = Path(split.path)
        roots = parse_qs(split.query).get("root")
        store_path = Path(roots[0]) if roots else Path("/")
        urls.append((socket_path, store_path))

    seen: set[tuple[Path, Path]] = set()
    result: list[tuple[Path, Path]] = []
    for item in urls:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _http_store_id(url: str) -> str:
    split = urlsplit(url)
    host = split.netloc or split.path
    return f"http-{host.rstrip('/').replace(':', '-')}"


def _unix_store_id(socket_path: Path, store_path: Path) -> str:
    payload = f"{socket_path}:{store_path}"
    safe = payload.strip("/").replace("/", "-").replace(":", "-")
    return f"unix-{safe}"[:120]


class Server:
    """Programmatic pynixd server instance.

    Accepts either a ``PynixdSettings`` object (from config file + env vars)
    or individual kwargs for programmatic/test use.
    """

    def __init__(
        self,
        stores: dict[StoreId, Store] | None = None,
        settings: PynixdSettings | None = None,
        **kwargs: Any,
    ) -> None:
        if settings is None:
            # ``PynixdSettings`` describes the daemon CLI, whose default Unix
            # socket lives under /run.  A programmatic Server is commonly an
            # ephemeral SSH or HTTP endpoint, however, and must not require
            # permission to create a system runtime directory unless its
            # caller explicitly requested a Unix socket.
            kwargs.setdefault("unix_path", None)
            settings = PynixdSettings(**kwargs)

        if stores is None:
            spec = LocalSocketStoreSpec(store_id=StoreId("local"), monitor=False)
            stores = {StoreId("local"): spec.to_store(str(StoreId("local")))}
        elif StoreId("local") not in stores:
            stores = dict(stores)
            spec = LocalSocketStoreSpec(store_id=StoreId("local"), monitor=False)
            stores[StoreId("local")] = spec.to_store(str(StoreId("local")))

        local_store = stores[StoreId("local")]

        existing_http_urls = {
            store.url.rstrip("/") for store in stores.values() if isinstance(store, HTTPBinaryCacheStore)
        }
        for url in _default_http_substituter_urls(local_store):
            if url.rstrip("/") in existing_http_urls:
                continue
            store_id = StoreId(_http_store_id(url))
            cache_spec = HTTPBinaryCacheSpec(
                store_id=store_id,
                url=url,
            )
            stores[store_id] = cache_spec.to_store(str(store_id))
            existing_http_urls.add(url.rstrip("/"))

        existing_unix = {
            (store.socket_path, store.store_path) for store in stores.values() if isinstance(store, ExternalUnixStore)
        }
        for socket_path, store_path in _default_unix_substituters(local_store):
            if (socket_path, store_path) in existing_unix:
                continue
            store_id = StoreId(_unix_store_id(socket_path, store_path))
            unix_spec = ExternalUnixStoreSpec(
                store_id=store_id,
                socket_path=socket_path,
                store_path=store_path,
            )
            stores[store_id] = unix_spec.to_store(str(store_id))
            existing_unix.add((socket_path, store_path))

        self.ctx = PynixdContext(
            settings=settings,
            _stores=stores,
        )

        self.ctx.scheduler = Scheduler(self.ctx)

        self.background_tasks: list[asyncio.Task[Any]] = []
        self.ssh_server: asyncssh.SSHAcceptor | None = None
        self.unix_server: asyncio.Server | None = None
        self.reverse_acceptor: asyncssh.SSHAcceptor | None = None
        self.http_server: web.AppRunner | None = None
        self.http_bound_port: int | None = None
        self.https_server: web.AppRunner | None = None
        self.https_bound_port: int | None = None
        self._started = False
        self._done_event = anyio.Event()

    @staticmethod
    def _ensure_unix_socket_parent(socket_path: Path) -> None:
        Server._check_unix_socket_length(socket_path)
        parent = socket_path.parent
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RuntimeError(f"Cannot create unix socket directory {parent}") from exc
        if not parent.is_dir():
            raise RuntimeError(f"Unix socket parent is not a directory: {parent}")
        if not os.access(parent, os.W_OK):
            raise RuntimeError(f"Unix socket directory is not writable: {parent}")

    @staticmethod
    def _check_unix_socket_length(socket_path: Path) -> None:
        """Refuse a socket path the operating system cannot bind.

        `sun_path` is `char[108]` on Linux and `char[104]` on darwin, and a
        path over the limit fails at `bind`. The failure arrives late and from
        the wrong place: the directory is made, the server starts, and uvloop
        raises `OSError: AF_UNIX path too long` with no path in the message.

        The check is here, beside the directory check, so the error names the
        path and the limit before anything is created.
        """
        limit = _SUN_PATH_LIMIT.get(sys.platform, _SUN_PATH_LIMIT_DEFAULT)
        encoded = len(os.fsencode(socket_path))
        if encoded > limit:
            raise RuntimeError(
                f"Unix socket path is {encoded} bytes, and {sys.platform} allows {limit}: {socket_path}. "
                f"Shorten `unix_path`."
            )

    @property
    def local_store(self) -> LocalStore:
        """The primary local Nix daemon store."""
        return self.ctx.local_store

    @property
    def stores(self) -> Mapping[StoreId, Store]:
        """Read-only view of all connected stores (including local)."""
        return self.ctx.stores

    @property
    def settings(self) -> PynixdSettings:
        """The server's configuration settings."""
        return self.ctx.settings

    @property
    def scheduler(self) -> Scheduler | None:
        """The build scheduler, or ``None`` if scheduling is disabled."""
        return self.ctx.scheduler

    async def add_store(self, store: Store, dynamic: bool = False) -> None:
        """Add a store to the server.

        If dynamic=True, the store's feature_matrix is also registered in
        the scheduler's dynamic_feature_matrix, so builds for that platform
        continue to queue even after the store is removed.
        """
        # Wire reconnect callback before start so the loop is ready
        captured_dynamic = dynamic

        async def _on_store_reconnect() -> None:
            if self.scheduler and isinstance(store, DaemonStore) and not store.no_schedule:
                self.scheduler.on_store_added(store, dynamic=captured_dynamic)

        if isinstance(store, DaemonStore) and not store.no_schedule:
            store._on_reconnect = _on_store_reconnect

        try:
            await store.start()
        except Exception:
            log.warning(
                "store_start_failed",
                store_id=store.store_id,
                exc_info=True,
            )
            # Store is already in ctx._stores from construction.
            # Don't register with scheduler — it's unavailable.
            return

        # Server is the primary owner and mutator of the stores collection
        self.ctx._stores[store.store_id] = store

        if self.scheduler and isinstance(store, DaemonStore) and not store.no_schedule:
            self.scheduler.on_store_added(store, dynamic=dynamic)

    async def remove_store(self, store_id: StoreId, drain_timeout: float = 300.0) -> None:
        """Remove a remote store, cleaning DB records and closing connections.

        NOTE: Used by external projects — do not remove.
        """
        if store_id == StoreId("local"):
            raise RuntimeError("Cannot remove local store")
        if self.scheduler:
            # First, drain the store in the scheduler to cancel/requeue jobs
            await self.scheduler.drain_store(store_id, drain_timeout=drain_timeout)

        # Then remove from the context
        store = self.ctx._stores.pop(store_id, None)
        if store is None:
            log.warning("remove_store_not_found", store_id=store_id)
            return

        local_store = self.local_store
        if isinstance(local_store, LocalDBStore):
            # DB cleanup is handled at the DB layer when paths are flushed.
            pass

        # Finally, close the store connection
        await store.close()

    async def _gc_tick(self) -> None:
        """Periodic GC loop. Runs at gc_interval."""
        log.info("gc_loop_started", interval=self.ctx.settings.gc_interval)
        while True:
            await anyio.sleep(self.ctx.settings.gc_interval)
            try:
                await self.local_store.execute(PynixdCollectGarbageRequest(action=PynixdGCAction.EXECUTE))
            except anyio.get_cancelled_exc_class():
                return
            except Exception:
                log.exception("gc_pass_failed")

    @property
    def host(self) -> str:
        """SSH bind host for remote connections."""
        return self.settings.ssh_host

    @property
    def port(self) -> int:
        """Bound SSH port, or the configured port if the server is not yet listening."""
        if self.ssh_server and self.ssh_server.sockets:
            return self.ssh_server.sockets[0].getsockname()[1]
        return self.settings.ssh_port or 0

    @property
    def username(self) -> str:
        """Current OS user for ssh-ng URI generation."""
        return os.environ.get("USER", "root")

    def uri(self) -> str:
        """ssh-ng:// URI for --store."""
        return f"ssh-ng://{self.username}@{self.host}:{self.port}"

    async def __aenter__(self) -> Server:
        await self.start()
        return self

    async def __aexit__(self, _exc_type, _exc_val, _exc_tb) -> None:
        await self.close()

    async def start(self) -> None:
        """Start the server listeners and background tasks."""
        if self._started:
            raise RuntimeError("Server already started")
        self._started = True
        local_store = self.ctx.local_store

        await local_store.start()

        if local_store.version < wire.proto(1, 35):
            raise RuntimeError(
                f"Local store {local_store.store_id} uses protocol {wire.proto_str(local_store.version)}, "
                "but pynixd requires at least 1.35 for the local store. "
                "Please upgrade your Nix daemon.",
            )

        if isinstance(local_store, LocalDBStore):
            self.ctx.db = local_store.db
            log.info(
                "local_store_db_connected",
                db_path=str(self.ctx.db.db_path),
            )
        else:
            self.ctx.db = None
            log.warning("local_store_db_disabled")

        if self.ctx.db:
            self.ctx.db.start()

        # Start non-local stores concurrently — they're already in _stores.
        async with anyio.create_task_group() as tg:
            for store_id, store in list(self.ctx._stores.items()):
                if store_id != StoreId("local"):
                    tg.start_soon(self.add_store, store)

        if self.ctx.scheduler:
            self.background_tasks.append(
                asyncio.create_task(self.ctx.scheduler.start()),
            )

        if self.ctx.db and self.ctx.settings.gc_enabled:
            self.background_tasks.append(asyncio.create_task(self._gc_tick()))

        s = self.settings
        if s.ssh_port is not None:
            self.ssh_server = await start_ssh_server(
                ctx=self.ctx,
                host=s.ssh_host,
                port=s.ssh_port,
                host_key_path=s.ssh_host_key,
                admin_users=s.admin_users,
                schedule_mode=s.schedule_mode,
            )

        self.reverse_acceptor = await start_reverse_acceptor(
            server=self,
            settings=s.reverse_acceptor,
        )

        if s.reverse_initiator.enabled:
            initiator = ReverseInitiator(self.ctx, s.reverse_initiator)
            self.background_tasks.append(asyncio.create_task(initiator.run()))

        if s.unix_path:
            self._ensure_unix_socket_parent(s.unix_path)
            self.unix_server = await start_unix_server(
                ctx=self.ctx,
                socket_path=s.unix_path,
                schedule_mode=s.schedule_mode,
            )

        if s.http_port is not None or s.https_port is not None:
            cache = PynixdHttpServer(
                local_store,
                enable_cache=s.http_enable_cache,
                enable_metrics=s.http_enable_metrics,
                metrics_no_auth=s.http_metrics_no_auth,
                username=s.http_user,
                password=s.http_pass,
                htpasswd_path=s.http_htpasswd,
                priority=s.http_priority,
                upload_dir=s.http_upload_dir,
            )
            if s.http_port is not None:
                runner, port = await cache.start(
                    host=s.http_host,
                    port=s.http_port,
                )
                self.http_server = runner
                self.http_bound_port = port
            if s.https_port is not None:
                runner, port = await cache.start(
                    host=s.http_host,
                    port=s.https_port,
                    ssl_cert=str(s.https_cert) if s.https_cert else None,
                    ssl_key=str(s.https_key) if s.https_key else None,
                )
                self.https_server = runner
                self.https_bound_port = port

        if not (self.reverse_acceptor or self.ssh_server or self.unix_server or self.http_server or self.https_server):
            log.warning("no_servers_started")

    async def wait_finished(self) -> None:
        """Wait for the server to shut down.

        This returns when :meth:`close` has been called and all listeners
        and background tasks have been cleaned up.  Useful for library
        integrations that need to block until pynixd is fully stopped.
        """
        await self._done_event.wait()

    async def close(self) -> None:
        """Gracefully shut down the server."""
        if not self._started:
            return
        self._started = False
        log.info("server_shutting_down")

        if self.http_server:
            await self.http_server.cleanup()
            self.http_server = None
        if self.https_server:
            await self.https_server.cleanup()
            self.https_server = None

        if self.reverse_acceptor:
            self.reverse_acceptor.close()
            await self.reverse_acceptor.wait_closed()
            self.reverse_acceptor = None
        if self.ssh_server:
            self.ssh_server.close()
            await self.ssh_server.wait_closed()
            self.ssh_server = None
        if self.unix_server:
            self.unix_server.close()
            await self.unix_server.wait_closed()
            self.unix_server = None

        if self.ctx.db:
            await self.ctx.db.close()
            self.ctx.db = None

        if self.ctx.scheduler:
            await self.ctx.scheduler.close()

        for task in self.background_tasks:
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
        self.background_tasks.clear()

        for store in self.ctx._stores.values():
            await store.close()
        self.ctx._stores.clear()
        self._done_event.set()
