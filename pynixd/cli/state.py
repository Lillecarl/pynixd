"""pynixd state — daemon state as JSON."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import anyio
import structlog

from nix_daemon_protocol.ids import StoreId

from ..config import LocalSocketStoreSpec
from ..daemon_extensions.pynixd_state import PynixdStateRequest
from ..state import KNOWN_SECTIONS
from ..store import LocalStore as LocalSocketStore
from .base import load_settings, setup_logging

if TYPE_CHECKING:
    import argparse

DEFAULT_SOCKET = Path("/run/pynixd/pynixd.sock")


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``state`` subcommand on the root argument parser."""
    parser = subparsers.add_parser("state", help="Daemon state as JSON")
    parser.add_argument(
        "--section",
        action="append",
        default=[],
        choices=KNOWN_SECTIONS,
        help="Show only this section (repeatable; default: all)",
    )
    parser.add_argument(
        "--federated",
        action="store_true",
        help="Ask each configured store for its own state too",
    )
    parser.set_defaults(func=state_main)


def state_main(args: argparse.Namespace) -> None:
    """Entry point for ``pynixd state`` — connect to daemon and ask."""
    anyio.run(_state_main, args)


async def _state_main(args: argparse.Namespace) -> None:
    settings = load_settings()
    setup_logging(settings)
    log = structlog.get_logger(__name__)

    socket_path = settings.unix_path or DEFAULT_SOCKET

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
        resp = await store.execute(
            PynixdStateRequest(wants=args.section, federated=args.federated),
        )
    except Exception:
        log.exception("state_failed")
        raise
    finally:
        await store.close()

    print(json.dumps(json.loads(resp.payload), indent=2))  # noqa: T201
