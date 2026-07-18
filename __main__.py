"""Entry point: python -m pynixd

Subcommands:
  daemon    Start the pynixd daemon server
  gc        Trigger garbage collection on stores
"""

from __future__ import annotations

import argparse
import signal

import anyio
import structlog
import uvloop

from .cli.base import load_settings, setup_logging
from .cli.gc import register as register_gc
from .instance import Server

log = structlog.get_logger(__name__)


async def async_daemon_main() -> None:
    """Async entry point: load settings, create server, and run until shutdown."""
    settings = load_settings()
    stores = settings.to_stores()

    server = Server(stores=stores, settings=settings)
    shutdown_event = anyio.Event()

    async def _handle_signals() -> None:
        with anyio.open_signal_receiver(signal.SIGINT, signal.SIGTERM) as signals:
            async for _sig in signals:
                if shutdown_event.is_set():
                    log.info("forced_shutdown")
                    raise SystemExit(1)
                log.info("shutdown_signal_received")
                shutdown_event.set()

    async with anyio.create_task_group() as tg:
        tg.start_soon(_handle_signals)
        await server.start()
        await shutdown_event.wait()
        tg.cancel_scope.cancel()

    await server.close()


def daemon_main(_args: argparse.Namespace) -> None:
    """CLI entry point for ``pynixd daemon`` — sets up logging and runs the async loop."""
    settings = load_settings()
    setup_logging(settings)

    anyio.run(async_daemon_main, backend="asyncio", backend_options={"loop_factory": uvloop.new_event_loop})


def main() -> None:
    """Top-level CLI entry point: parse args and dispatch to subcommand handlers."""
    parser = argparse.ArgumentParser(description="pynixd — Nix daemon protocol proxy")
    root_sub = parser.add_subparsers(dest="subcommand", required=True)

    daemon_parser = root_sub.add_parser("daemon", help="Start the pynixd daemon server")
    daemon_parser.set_defaults(func=daemon_main)

    register_gc(root_sub)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
