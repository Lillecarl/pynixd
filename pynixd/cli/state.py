"""pynixd state — daemon state as JSON."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import anyio
import structlog

from ..daemon_extensions.pynixd_state import PynixdStateRequest
from ..state import KNOWN_SECTIONS
from .base import add_socket_argument, load_settings, open_pynixd, setup_logging

if TYPE_CHECKING:
    import argparse


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
    add_socket_argument(parser)
    parser.set_defaults(func=state_main)


def state_main(args: argparse.Namespace) -> None:
    """Entry point for ``pynixd state`` — connect to daemon and ask."""
    anyio.run(_state_main, args)


async def _state_main(args: argparse.Namespace) -> None:
    settings = load_settings()
    setup_logging(settings)
    log = structlog.get_logger(__name__)

    store = await open_pynixd(settings, args.socket, PynixdStateRequest.name)

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
