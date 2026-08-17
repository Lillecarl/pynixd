"""The same workload against `nix-daemon` and against pynixd, byte for byte.

This is the stream mode of `nix/functional-tests/`, without the functional
tests. It runs one small workload twice, with the recorder of
`nix_daemon_protocol.wirelog` between the client and the daemon, and it states
that the two recordings agree.

    client -> outer.sock -> recorder -> inner.sock -> the daemon

The contract of pynixd is that a client cannot tell pynixd from `nix-daemon`,
and that is a statement about the bytes. A script of the functional suite says
"pass" or "fail" for reasons that are not the wire, and it needs Linux and a
builder. This needs neither, so it runs in the dev shell on any host.

It found two defects in its first two runs:

1. `nix store add-file` and then `nix store gc` deleted the file against
   `nix-daemon`, and deleted nothing against pynixd. An idle pooled connection
   kept a worker of the daemon alive, and that worker held the temporary root
   of the path. `tests/unit/test_gc_retires_idle_connections.py` holds the
   rule that corrects it.
2. `QueryPathInfo` answered `sha256:<digest>` where `nix-daemon` answers the
   digest alone. The fast path of pynixd read the `narHash` column of the
   database, which carries the name of the algorithm, and the wire does not.
   No client complained, because `Hash::parseAny` reads both forms.

**The workload builds nothing.** A build needs a builder and takes minutes,
and the functional suite covers that ground already. This covers the
handshake and the operations that a read-only client sends.

Issue #175.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import anyio
import pytest

from nix_daemon_protocol.wirelog import compare, decode
from nix_daemon_protocol.wirelog.diff import report

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

NIX = shutil.which("nix")
PYNIXD = shutil.which("pynixd")

pytestmark = pytest.mark.skipif(
    NIX is None or PYNIXD is None,
    reason="this test needs both `nix` and `pynixd` on the PATH",
)

# Nix refuses a store when a parent of it is a symbolic link, and `/tmp` is
# one on Darwin. A Unix socket path also has to fit in `sun_path`, which is
# 104 bytes, so the root is short and not a `tmp_path` of pytest.
TEMP_ROOT = Path("/private/tmp") if Path("/private/tmp").is_dir() else Path("/tmp")
BASE = TEMP_ROOT / "pynixd-wire-parity"
SOCKET_WAIT = 30.0


def _config(work: Path, root: Path) -> Path:
    """The configuration that `nix/functional-tests/make-shim.sh` writes."""
    config = work / "pynixd.json"
    config.write_text(
        json.dumps(
            {
                "stores": {
                    "local": {
                        "type": "local-socket",
                        "store_dir": str(root / "store"),
                        "state_dir": str(root / "var/nix"),
                        "socket_path": str(work / "up.sock"),
                        "nix_bin": NIX,
                        "use_db": True,
                        "monitor": False,
                        "probe": False,
                    },
                },
                "unix_path": str(work / "inner.sock"),
                "ssh_port": None,
                "http_port": None,
            },
        ),
    )
    return config


def _backend(role: str, work: Path, root: Path) -> tuple[list[str], dict[str, str]]:
    """The daemon that the recorder starts, and the environment it needs."""
    env = dict(os.environ, NIX_STORE_DIR=str(root / "store"), NIX_STATE_DIR=str(root / "var/nix"))
    if role == "control":
        return [str(NIX), "daemon"], env
    return [str(PYNIXD), "daemon"], dict(env, PYNIXD_CONFIG=str(_config(work, root)))


async def _wait_for(path: Path) -> None:
    with anyio.fail_after(SOCKET_WAIT):
        while not await anyio.Path(path).exists():
            await anyio.sleep(0.02)


async def _record(role: str, root: Path) -> Path:
    """Run the workload once, and answer the directory of the recording."""
    work = BASE / role
    out = BASE / f"rec-{role}"
    for path in (work, out, root / "store", root / "var/nix"):
        await anyio.Path(path).mkdir(parents=True, exist_ok=True)
    sample = work / "f.txt"
    await anyio.Path(sample).write_text("hello wirelog\n")
    await anyio.Path(work / "d").mkdir(exist_ok=True)
    await anyio.Path(work / "d" / "inner").write_text("inner\n")

    command, env = _backend(role, work, root)
    recorder = await anyio.open_process(
        [
            sys.executable,
            "-m",
            "nix_daemon_protocol.wirelog",
            "record",
            "--listen",
            str(work / "outer.sock"),
            "--connect",
            str(work / "inner.sock"),
            "--out",
            str(out),
            "--",
            *command,
        ],
        env=env,
    )
    try:
        await _wait_for(work / "outer.sock")
        client = dict(os.environ, NIX_REMOTE=f"unix://{work / 'outer.sock'}", NIX_STORE_DIR=str(root / "store"))
        for words in (
            ["store", "info"],
            ["store", "info", "--json"],
            ["store", "add-file", "--name", "f.txt", str(sample)],
            ["store", "add-path", "--name", "d", str(work / "d")],
            ["store", "ls", "--json", "--recursive", str(root / "store")],
            ["path-info", "--json", str(root / "store")],
            ["store", "verify", "--all"],
            ["store", "optimise"],
            ["store", "dump-path", str(root / "store")],
            ["store", "gc", "--max", "0"],
            ["store", "gc"],
            ["store", "info"],
        ):
            # A command may fail, and a failure is a fine thing to record: the
            # two daemons must fail the same way.
            await anyio.run_process([str(NIX), *words], env=client, check=False)
    finally:
        recorder.terminate()
        with anyio.move_on_after(30):
            await recorder.wait()
    return out


@pytest.fixture
async def clean_base() -> AsyncIterator[None]:
    shutil.rmtree(BASE, ignore_errors=True)
    yield
    shutil.rmtree(BASE, ignore_errors=True)


@pytest.mark.usefixtures("clean_base")
async def test_the_two_daemons_answer_the_same_bytes() -> None:
    """Each connection of the pynixd run agrees with the control run.

    The store directory is one path for both runs, because the hash of a store
    path holds that directory. Two roots would give two hashes, and then every
    answer would differ for a reason that is not pynixd.
    """
    root = BASE / "store-root"
    recordings: dict[str, Path] = {}
    for role in ("control", "pynixd"):
        shutil.rmtree(root, ignore_errors=True)
        recordings[role] = await _record(role, root)

    control = sorted(p.relative_to(recordings["control"]) for p in recordings["control"].rglob("conn-*.wire"))
    candidate = sorted(p.relative_to(recordings["pynixd"]) for p in recordings["pynixd"].rglob("conn-*.wire"))
    assert control, "the control run recorded no connection"
    assert control == candidate, f"the two runs served different connections: {control} and {candidate}"

    for name in control:
        one = await decode(recordings["control"] / name)
        two = await decode(recordings["pynixd"] / name)
        assert one.problem is None, one.problem
        assert two.problem is None, two.problem
        differences = compare(one, two)
        assert differences == [], f"{name}\n{report(differences)}"
