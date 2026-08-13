"""pynixd gc — trigger garbage collection on stores."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import anyio
import structlog

from ..config import LocalSocketStoreSpec
from ..serde.ids import StoreId
from ..serde.protocol import PynixdGCAction
from ..serde.pynixd_collect_garbage import PynixdCollectGarbageRequest
from ..store import LocalStore as LocalSocketStore
from .base import load_settings, setup_logging

if TYPE_CHECKING:
    import argparse

DEFAULT_SOCKET = Path("/run/pynixd/pynixd.sock")


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``gc`` subcommand on the root argument parser."""
    parser = subparsers.add_parser("gc", help="Trigger garbage collection on stores")
    parser.add_argument(
        "--store",
        help="Only run GC on this store (default: all stores)",
        default=None,
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually run GC (default is dry-run)",
    )
    parser.set_defaults(func=gc_main)


def gc_main(args: argparse.Namespace) -> None:
    """Entry point for ``pynixd gc`` — connect to daemon and issue a GC request."""
    anyio.run(_gc_main, args)


async def _gc_main(args: argparse.Namespace) -> None:
    settings = load_settings()
    setup_logging(settings)
    log = structlog.get_logger(__name__)

    socket_path = settings.unix_path or DEFAULT_SOCKET
    action = PynixdGCAction.EXECUTE if args.execute else PynixdGCAction.DRY_RUN

    store = LocalSocketStore(
        LocalSocketStoreSpec(
            store_id=StoreId("cli"),
            socket_path=socket_path,
            probe=False,
            monitor=False,
        ),
    )

    await store.start(sync_paths=False)

    try:
        resp = await store.execute(PynixdCollectGarbageRequest(action=action))
    except Exception:
        log.exception("gc_failed")
        raise
    finally:
        await store.close()

    for msg in resp.logs.messages:
        text = getattr(msg, "text", None) or getattr(msg, "msg", None)
        if text:
            print(text)  # noqa: T201

    label = "dry-run" if action == PynixdGCAction.DRY_RUN else "gc"
    if resp.store_paths:
        print(f"{label}: {len(resp.store_paths)} paths, {resp.bytes} bytes freed")  # noqa: T201
    else:
        print(f"{label}: no paths eligible")  # noqa: T201
