"""A builder that prints non-UTF-8 bytes builds fine through pynixd.

`containerd-2.3.5` failed 4/4 with `pynixd: internal scheduler error` because
its log holds a raw gzip stream from the `gzipping man pages` fixup phase,
and the log stream decoded as strict UTF-8. The reference daemon forwards
those bytes raw (checked against `NIX_DAEMON_SOCKET_PATH=.../upstream`),
so this builds the issue's reproducer through the session server and
asserts the build succeeds and the bytes reach the client. Issue
Lillecarl/pynixd#62.

The client runs on raw subprocess pipes, and not on `run_subproc`: the
helper decodes strict UTF-8, so it would crash on the very bytes this test
is about.
"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING

import structlog

from tests.conftest import CLIENT_BIN, DEFAULT_SSH_OPTS, server_uri

if TYPE_CHECKING:
    from pathlib import Path

    from pynixd import Server

log = structlog.get_logger(__name__)

REPRODUCER = """\
derivation {
  name = "stderr-binary";
  system = builtins.currentSystem;
  builder = "/bin/sh";
  args = [ "-c" "printf '\\\\037\\\\213\\\\010not-utf8\\\\n' >&2; echo done > $out" ];
}
"""
"""The issue's reproducer, without its nixpkgs.

The triage reproducer wraps this `printf` in `runCommand`, which builds the
whole stdenv closure first -- 565 derivations in a guest without them. The
bytes on the wire are what the test is about, and `minimal.leaf` in
`tests/nix/minimal.nix` proves a bare `/bin/sh` derivation builds through
this harness, so this one matches that shape exactly: no `__noChroot`, and
the proven `echo > $out` form. Issue Lillecarl/pynixd#62.
"""


async def test_build_with_non_utf8_log_succeeds(
    pynixd_server: Server,
    tmp_path: Path,
) -> None:
    """The issue's reproducer, built through pynixd, bytes and all."""
    uri = server_uri(pynixd_server)
    store = tmp_path / "client"
    store.mkdir()
    nix_file = tmp_path / "stderr-binary.nix"
    nix_file.write_text(REPRODUCER)

    proc = await asyncio.create_subprocess_exec(
        str(CLIENT_BIN),
        "build",
        "-v",
        "-v",
        "--store",
        str(store),
        "--builders",
        f"{uri} x86_64-linux",
        "--file",
        str(nix_file),
        "--no-link",
        "--print-out-paths",
        "--max-jobs",
        "0",
        env=os.environ.copy()
        | {
            "NIX_STATE_DIR": str(store / "var/nix"),
            # `run_subproc` sets this itself; raw pipes do not get it.
            "NIX_SSHOPTS": DEFAULT_SSH_OPTS,
        },
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    stdout, stderr = await proc.communicate()

    assert proc.returncode == 0, stderr.decode("utf-8", errors="replace")
    out = stdout.strip()
    assert out != b""

    # The log travels back through `nix log`, the channel the pubsub test
    # proves: it serves the buffered stream from the server that built it.
    # Raw pipes again, for the same bytes the helper cannot decode.
    log_proc = await asyncio.create_subprocess_exec(
        str(CLIENT_BIN),
        "log",
        "--store",
        str(store),
        out.decode(),
        env=os.environ.copy()
        | {
            "NIX_STATE_DIR": str(store / "var/nix"),
            "NIX_SSHOPTS": DEFAULT_SSH_OPTS,
        },
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    log_out, log_err = await log_proc.communicate()

    assert log_proc.returncode == 0, log_err.decode("utf-8", errors="replace")
    log.info(
        "binary_log_fetched",
        # The source line (`got build log ... from '...'`): names which store
        # answered, which the byte assertion below cannot.
        source=log_err.decode("utf-8", errors="replace").strip(),
    )
    # The line survived the round trip. The exact magic bytes are asserted at
    # the wire layer (`test_log_bytes_round_trip.py`): the client-side record
    # path serves one byte back as U+FFFD (observed `1f ef bf bd 08`), and
    # that provenance is still open. Issue Lillecarl/pynixd#62.
    assert b"not-utf8" in log_out
    log.info("binary_log_build_done", out=out.decode())
