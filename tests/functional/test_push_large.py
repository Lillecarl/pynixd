"""A first-time push of a large path completes instead of wedging the store.

Two deploys stalled at the start of a ~250 MiB cache push: the pool created
one connection for the push, no operation ever completed, and afterwards
even `nix store info` timed out while the pod stayed Ready. Issue #53.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import anyio

from tests.conftest import CLIENT_BIN, DEFAULT_SSH_OPTS, run_subproc, server_uri

if TYPE_CHECKING:
    from pathlib import Path

    from pynixd import Server

SIZE_MB = 256
"""The incident pushed about 250 MiB of new content. Zeros: the NAR carries
them uncompressed, so size is what this is about, not entropy."""


async def test_large_first_push_completes(
    pynixd_server: Server,
    tmp_path: Path,
) -> None:
    """`nix copy --to pynixd` of a 256 MiB path answers, and the path lands."""
    uri = server_uri(pynixd_server)
    store = tmp_path / "client"
    store.mkdir()
    blob = tmp_path / "big.bin"
    with blob.open("wb") as handle:
        chunk = b"\0" * (1024 * 1024)
        for _ in range(SIZE_MB):
            handle.write(chunk)
    env = os.environ.copy() | {
        "NIX_STATE_DIR": str(store / "var/nix"),
        "NIX_SSHOPTS": DEFAULT_SSH_OPTS,
    }

    rc, out, _, _ = await run_subproc(
        [str(CLIENT_BIN), "--store", str(store), "store", "add-file", str(blob)],
        env=env,
    )
    assert rc == 0
    path = out.strip().split()[-1]

    with anyio.fail_after(300):
        rc, _, err, _ = await run_subproc(
            [str(CLIENT_BIN), "--store", str(store), "copy", "--to", uri, path],
            env=env,
        )
    assert rc == 0, err

    rc, out, _, _ = await run_subproc(
        [str(CLIENT_BIN), "--store", uri, "path-info", path],
        env=env,
    )
    assert rc == 0, out
