"""Run the recorder, or compare two directories of recordings.

    python -m nix_daemon_protocol.wirelog record --listen L --connect C \
        --out DIR -- nix daemon
    python -m nix_daemon_protocol.wirelog compare CONTROL_DIR CANDIDATE_DIR

`record` starts the backend as its own child, waits for the socket of that
backend, and then serves on `--listen`. So one process owns both, and the
`startDaemon` of the functional tests kills one pid and both go away.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
from pathlib import Path

import anyio

from .decode import decode
from .diff import compare, report
from .recorder import Recorder

# How long the backend has to make its socket. A daemon of Nix takes well
# under a second, and a run that waits longer than this has a real fault.
BACKEND_TIMEOUT = 30.0


async def _wait_for_socket(path: Path) -> None:
    with anyio.fail_after(BACKEND_TIMEOUT):
        while not path.exists():
            await anyio.sleep(0.02)


async def _run_record(args: argparse.Namespace) -> int:
    listen = Path(args.listen)
    connect = Path(args.connect)
    out = Path(args.out)

    async with anyio.create_task_group() as group:
        backend = None
        if args.command:
            env = dict(os.environ, NIX_DAEMON_SOCKET_PATH=str(connect))
            connect.unlink(missing_ok=True)
            backend = await anyio.open_process(args.command, env=env, stdin=None, stdout=None, stderr=None)
            print(f"wirelog: backend {args.command[0]} is pid {backend.pid}", file=sys.stderr, flush=True)

        try:
            await _wait_for_socket(connect)
        except TimeoutError:
            print(f"wirelog: the backend made no socket at {connect}", file=sys.stderr)
            if backend is not None:
                backend.send_signal(signal.SIGTERM)
            return 1

        recorder = Recorder(listen=listen, connect=connect, out_dir=out)
        group.start_soon(recorder.serve)
        await recorder.wait_started()
        print(f"wirelog: recording {listen} -> {connect} into {out}", file=sys.stderr, flush=True)

        if backend is not None:
            # The backend outlives every client, so this waits until somebody
            # kills this process, and the `finally` then takes the backend.
            try:
                await backend.wait()
            finally:
                with anyio.CancelScope(shield=True):
                    backend.send_signal(signal.SIGTERM)
                group.cancel_scope.cancel()
    return 0


async def _run_compare(args: argparse.Namespace) -> int:
    control_dir = Path(args.control)
    candidate_dir = Path(args.candidate)

    control_files = sorted(control_dir.glob("conn-*.wire"))
    candidate_files = sorted(candidate_dir.glob("conn-*.wire"))

    if not control_files:
        print(f"compare: no recording in {control_dir}", file=sys.stderr)
        return 2

    total = 0
    if len(control_files) != len(candidate_files):
        print(
            f"the daemon served {len(control_files)} connections and pynixd served {len(candidate_files)}",
        )
        total += 1

    for control_path, candidate_path in zip(control_files, candidate_files, strict=False):
        differences = compare(await decode(control_path), await decode(candidate_path))
        if differences:
            print(f"--- {control_path.name} ---")
            print(report(differences))
            total += len(differences)

    if not total:
        print(f"the two runs agree over {len(control_files)} connections")
        return 0
    print(f"\n{total} differences over {len(control_files)} connections")
    return 1


def main(argv: list[str] | None = None) -> int:
    """Read the arguments, and run the subcommand that they name."""
    parser = argparse.ArgumentParser(prog="wirelog", description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)

    record = sub.add_parser("record", help="copy a Unix socket to another, and write what passes")
    record.add_argument("--listen", required=True, help="the socket that the client reaches")
    record.add_argument("--connect", required=True, help="the socket of the daemon")
    record.add_argument("--out", required=True, help="the directory for the recordings")
    record.add_argument("command", nargs=argparse.REMAINDER, help="-- the backend to start")

    diff = sub.add_parser("compare", help="compare two directories of recordings")
    diff.add_argument("control", help="the run against a plain nix-daemon")
    diff.add_argument("candidate", help="the run against pynixd")

    args = parser.parse_args(argv)
    if args.mode == "record":
        # argparse.REMAINDER keeps the `--` that divides the two.
        if args.command and args.command[0] == "--":
            args.command = args.command[1:]
        return anyio.run(_run_record, args)
    return anyio.run(_run_compare, args)


if __name__ == "__main__":
    raise SystemExit(main())
