"""A first-time push of a closure-shaped payload completes.

Companion to `test_push_large.py`: that one pushes one 256 MiB path. The
#53 incidents pushed a ~250 MiB render closure -- many paths of mixed
sizes -- so this pushes 150 paths of ~1.3 MiB each through one
AddMultipleToStore instead. Issue #53.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import anyio

from tests.conftest import CLIENT_BIN, DEFAULT_SSH_OPTS, run_subproc, server_uri

if TYPE_CHECKING:
    from pathlib import Path

    from pynixd import Server

DIR_COUNT = 150
FILES_PER_DIR = 20
FILE_SIZE = 64 * 1024
"""150 paths, ~200 MiB total. Small enough for the suite, shaped like the
incident: one copy, many mixed paths, all new to the store."""


async def test_many_path_first_push_completes(
    pynixd_server: Server,
    tmp_path: Path,
) -> None:
    """`nix copy --to pynixd` of 150 new paths answers, and they all land."""
    uri = server_uri(pynixd_server)
    store = tmp_path / "client"
    store.mkdir()
    env = os.environ.copy() | {
        "NIX_STATE_DIR": str(store / "var/nix"),
        "NIX_SSHOPTS": DEFAULT_SSH_OPTS,
    }

    payload = tmp_path / "payload"
    payload.mkdir()
    blob = b"\0" * FILE_SIZE
    for i in range(DIR_COUNT):
        sub = payload / f"dir-{i:03d}"
        sub.mkdir()
        for j in range(FILES_PER_DIR):
            (sub / f"file-{j:02d}.bin").write_bytes(blob)

    paths: list[str] = []
    for i in range(DIR_COUNT):
        rc, out, _, _ = await run_subproc(
            [
                str(CLIENT_BIN),
                "--store",
                str(store),
                "store",
                "add-path",
                str(payload / f"dir-{i:03d}"),
            ],
            env=env,
        )
        assert rc == 0
        paths.append(out.strip().split()[-1])
    assert len(paths) == DIR_COUNT

    with anyio.fail_after(600):
        rc, _, err, _ = await run_subproc(
            [str(CLIENT_BIN), "--store", str(store), "copy", "--to", uri, *paths],
            env=env,
        )
    assert rc == 0, err

    rc, out, _, _ = await run_subproc(
        [str(CLIENT_BIN), "--store", uri, "path-info", *paths],
        env=env,
    )
    assert rc == 0, out
    for path in paths:
        assert path in out
