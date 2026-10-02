"""Raw daemon-protocol throughput, through each daemon.

The client is pynixd's own `LocalSocketStore`, so its Python cost is present in
both runs and cancels in the ratio. That is the whole point of the comparison:
the only thing that differs is the daemon on the far end of the socket.

`sys.argv`: the count, then the two sockets -- nix-daemon first, pynixd second.
The pump runs `REPEATS` times and reports the best rate: the container shares
the host's CPU, so a single pass carries whatever else the host was doing.
"""

from __future__ import annotations

import asyncio
import random
import sys
import time
from pathlib import Path

from nix_daemon_protocol.ids import LOCAL_STORE_ID
from pynixd.config import LocalSocketStoreSpec
from pynixd.serde import AddTempRootRequest, IsValidPathRequest, StorePath
from pynixd.store import LocalSocketStore

ALPHABET = "0123456789abcdfghijklmnpqrsvwxyz"

REPEATS = 3
"""How many times each pump runs. The best rate is the one reported."""


def paths(n: int) -> list[StorePath]:
    rng = random.Random(0)
    made = []
    for i in range(n):
        digest = "".join(rng.choice(ALPHABET) for _ in range(32))
        made.append(StorePath(path=f"/nix/store/{digest}-rawbench-{i}"))
    return made


def spec(socket_path: Path) -> LocalSocketStoreSpec:
    return LocalSocketStoreSpec(
        store_id=LOCAL_STORE_ID,
        store_path=Path("/"),
        socket_path=socket_path,
        managed=False,
        probe=False,
        monitor=False,
    )


async def one_pass(socket_path: Path, sample: list[StorePath]) -> tuple[float, float]:
    """One IsValidPath and one AddTempRoot pass. Returns (isvalid, addroot) rates."""
    client = LocalSocketStore(spec(socket_path))
    await client.start()
    try:
        await client.execute(IsValidPathRequest(path=sample[0]))
        await client.execute(AddTempRootRequest(path=sample[0]))

        start = time.perf_counter()
        for p in sample:
            await client.execute(IsValidPathRequest(path=p))
        middle = time.perf_counter()
        for p in sample:
            await client.execute(AddTempRootRequest(path=p))
        end = time.perf_counter()

        n = len(sample)
        return n / (middle - start), n / (end - middle)
    finally:
        await client.close()


async def measure(socket_path: Path, sample: list[StorePath], label: str) -> None:
    best = (0.0, 0.0)
    for _ in range(REPEATS):
        isvalid, addroot = await one_pass(socket_path, sample)
        best = (max(best[0], isvalid), max(best[1], addroot))

    n = len(sample)
    print(
        f"{label:10} n={n}  isvalidpath {best[0]:>8.0f} op/s   addtemproot {best[1]:>8.0f} op/s",
        flush=True,
    )


async def main() -> None:
    sample = paths(int(sys.argv[1]))
    await measure(Path(sys.argv[2]), sample, "nix-daemon")
    await measure(Path(sys.argv[3]), sample, "pynixd")


if __name__ == "__main__":
    asyncio.run(main())
