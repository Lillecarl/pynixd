"""CPU and memory profile of the server-side NAR forward path.

This drives `AddMultipleToStore._forward_stream`, the handler a client reaches
with `nix copy --to ssh-ng://...`. That is the path a nixkube node uses, and it
is the one that pegged a cluster at 999 millicores and 1850 MB.

`test_bench_nar.py` does not cover it. Every test there drives
`stream_paths_store_to_store`, which is pynixd acting as a *client* against
another store -- a different loop, with a different drain pattern.

Two shapes, because they fail differently. Many small paths put the cost in
per-path Python work, so read `cpu_ms_per_path`. Few large paths put it in the
byte loop, so read `peak_mib` against `sent_mib`: a streaming forward holds
roughly one chunk, and a buffering one holds the whole payload.

The metrics are reported, not asserted. Bounds belong here once the numbers are
known -- a guessed threshold either passes forever or fails on a slower runner.
What is asserted is that the copy succeeded and the paths arrived, so the
measurement is of a transfer that really happened.

`tracemalloc` inflates CPU somewhat, so compare `cpu_ms_per_mib` between shapes
in one run rather than against a production figure. `peak_mib` is the number
that carries the diagnosis and it is unaffected.

Every test here is also sampled by the autouse `profiler` fixture, which writes
`pyinstrument.txt` beside the test's log directory. That is where the attributed
hotspot is.
"""

from __future__ import annotations

import os
import resource
import time
import tracemalloc
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import structlog

from tests.conftest import (
    CLIENT_BIN,
    STORE_PREFIX,
    rmtree_robust,
    run_subproc,
    ssh_admin_uri,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from pynixd import Server

log = structlog.get_logger(__name__)

# Fixed sizes, never derived from the host, so two runs are comparable and a
# number can be quoted in an issue.
_MANY_SMALL = (200, 64)
_FEW_LARGE = (2, 64 * 1024)

_SRC_STORE = STORE_PREFIX / "nar-profile-src"
_CONTENT_DIR = STORE_PREFIX / "nar-profile-content"

_MIB = 1024 * 1024


@pytest.fixture
def src_store() -> Iterator[Path]:
    """A source store of this repository's own, per the /tmp/pynixd-stores rule."""
    rmtree_robust(_SRC_STORE)
    rmtree_robust(_CONTENT_DIR)
    _SRC_STORE.mkdir(parents=True, exist_ok=True)
    _CONTENT_DIR.mkdir(parents=True, exist_ok=True)
    yield _SRC_STORE
    rmtree_robust(_SRC_STORE)
    rmtree_robust(_CONTENT_DIR)


async def _add_paths(src: Path, count: int, size_kib: int) -> list[str]:
    """Add `count` paths of `size_kib` each to `src`, and return them.

    The content is random so that no two paths deduplicate into one, which
    would make the shape smaller than it claims to be.
    """
    paths: list[str] = []
    for i in range(count):
        blob = _CONTENT_DIR / f"blob-{size_kib}k-{i}"
        blob.write_bytes(os.urandom(size_kib * 1024))
        rc, stdout, stderr, _ = await run_subproc(
            [str(CLIENT_BIN), "store", "add-path", "--store", str(src), str(blob)],
        )
        assert rc == 0, f"add-path failed:\n{stderr}"
        paths.append(stdout.strip())
        blob.unlink()
    return paths


@contextmanager
def _measure(label: str, *, count: int, sent_bytes: int) -> Iterator[None]:
    """Report wall time, CPU and peak allocation for the block.

    CPU is split. `RUSAGE_SELF` is pynixd, because the server runs in this
    process; `RUSAGE_CHILDREN` is the `nix` client, which is a subprocess. A
    single total would hide which side burns the core.
    """
    tracemalloc.start()
    tracemalloc.reset_peak()
    self0 = resource.getrusage(resource.RUSAGE_SELF)
    kids0 = resource.getrusage(resource.RUSAGE_CHILDREN)
    wall0 = time.perf_counter()

    yield

    wall = time.perf_counter() - wall0
    self1 = resource.getrusage(resource.RUSAGE_SELF)
    kids1 = resource.getrusage(resource.RUSAGE_CHILDREN)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    server_cpu = (self1.ru_utime - self0.ru_utime) + (self1.ru_stime - self0.ru_stime)
    client_cpu = (kids1.ru_utime - kids0.ru_utime) + (kids1.ru_stime - kids0.ru_stime)
    sent_mib = sent_bytes / _MIB

    log.info(
        "bench_nar_forward_profile",
        shape=label,
        paths=count,
        sent_mib=round(sent_mib, 2),
        wall_s=round(wall, 3),
        server_cpu_s=round(server_cpu, 3),
        client_cpu_s=round(client_cpu, 3),
        # The two that locate the fault.
        cpu_ms_per_path=round(server_cpu * 1000 / count, 3),
        cpu_ms_per_mib=round(server_cpu * 1000 / sent_mib, 3) if sent_mib else None,
        peak_mib=round(peak / _MIB, 2),
        # Streaming keeps this near zero; buffering drives it towards 1.
        peak_over_sent=round(peak / sent_bytes, 3) if sent_bytes else None,
    )


async def _copy_and_measure(
    server: Server,
    src: Path,
    label: str,
    count: int,
    size_kib: int,
) -> None:
    paths = await _add_paths(src, count, size_kib)
    sent_bytes = count * size_kib * 1024

    cmd = [
        str(CLIENT_BIN),
        "copy",
        "--no-check-sigs",
        "--from",
        str(src),
        "--to",
        ssh_admin_uri(server),
        *paths,
    ]

    with _measure(label, count=count, sent_bytes=sent_bytes):
        rc, _, stderr, _ = await run_subproc(cmd)

    assert rc == 0, f"nix copy failed:\n{stderr}"

    # A copy that silently moved nothing would report a flattering profile.
    rc_check, stdout_check, stderr_check, _ = await run_subproc(
        [str(CLIENT_BIN), "path-info", "--store", ssh_admin_uri(server), *paths],
    )
    assert rc_check == 0, f"path-info failed:\n{stderr_check}"
    assert len(stdout_check.split()) >= count


@pytest.mark.benchmark
async def test_profile_nar_forward_many_small(pynixd_server: Server, src_store: Path) -> None:
    """Many small paths: the cost per path, not per byte.

    200 paths of 64 KiB. Read `cpu_ms_per_path`.
    """
    count, size_kib = _MANY_SMALL
    await _copy_and_measure(pynixd_server, src_store, "many-small", count, size_kib)


@pytest.mark.benchmark
async def test_profile_nar_forward_few_large(pynixd_server: Server, src_store: Path) -> None:
    """Few large paths: the cost per byte, and whether the forward streams.

    2 paths of 64 MiB. Read `peak_over_sent`: a forward that streams holds about
    one chunk whatever the payload, and one that buffers holds all of it.
    """
    count, size_kib = _FEW_LARGE
    await _copy_and_measure(pynixd_server, src_store, "few-large", count, size_kib)
