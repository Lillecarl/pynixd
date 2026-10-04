"""pynixd roots — full and exclusive storage per root."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import anyio
import structlog

from nix_daemon_protocol.ids import StoreId

from ..config import LocalSocketStoreSpec
from ..daemon_extensions.pynixd_roots_report import PynixdRootsReportRequest
from ..store import LocalStore as LocalSocketStore
from .base import load_settings, setup_logging

if TYPE_CHECKING:
    import argparse

DEFAULT_SOCKET = Path("/run/pynixd/pynixd.sock")


def _human(n: int) -> str:
    """Bytes as the hog-finder reads them: `96.9G`, not twelve digits."""
    value = float(n)
    for unit in ("B", "K", "M", "G", "T"):
        if value < 1024 or unit == "T":
            text = f"{value:.1f}".removesuffix(".0")
            return f"{text}{unit}"
        value /= 1024
    return f"{value:.1f}T"  # unreachable: the loop returns at "T"


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``roots`` subcommand on the root argument parser."""
    parser = subparsers.add_parser("roots", help="Full and exclusive storage per root")
    parser.add_argument(
        "--top",
        type=int,
        default=20,
        help="Show this many roots, by exclusive bytes (default: 20)",
    )
    parser.set_defaults(func=roots_main)


def roots_main(args: argparse.Namespace) -> None:
    """Entry point for ``pynixd roots`` — connect to daemon and issue a report request."""
    if args.top < 1:
        raise SystemExit(f"pynixd roots: error: --top takes a count, and {args.top} is not one")
    anyio.run(_roots_main, args)


async def _roots_main(args: argparse.Namespace) -> None:
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
        resp = await store.execute(PynixdRootsReportRequest())
    except Exception:
        log.exception("roots_failed")
        raise
    finally:
        await store.close()

    rows = sorted(resp.rows, key=lambda row: row.exclusive_bytes, reverse=True)[: args.top]
    print(f"{'LABEL':<44}{'PATHS':>8}{'FULL':>10}{'EXCLUSIVE':>10}{'EXCL_PATHS':>11}")  # noqa: T201
    for row in rows:
        print(  # noqa: T201
            f"{row.label:<44}{row.full_paths:>8}{_human(row.full_bytes):>10}"
            f"{_human(row.exclusive_bytes):>10}{row.exclusive_paths:>11}"
        )
