"""Unit tests for scheduler build queue behavior."""

from __future__ import annotations

import pytest

from pynixd.build_queue import BuildQueue
from pynixd.serde import BasicDerivation, BuildDerivationRequest, BuildMode
from pynixd.serde import StorePath as SerdeStorePath
from pynixd.store_path import StorePath


def _build_request(drv_path: StorePath, *, builder: str = "") -> BuildDerivationRequest:
    return BuildDerivationRequest(
        drv_path=SerdeStorePath(path=str(drv_path)),
        derivation=BasicDerivation(platform="x86_64-linux", builder=builder),
        build_mode=BuildMode.NORMAL,
    )


@pytest.mark.anyio
async def test_build_queue_deduplicates_active_builds_by_drv_path() -> None:
    queue = BuildQueue()
    drv_path = StorePath("/nix/store/00000000000000000000000000000001-test.drv")

    first_id, first_future = await queue.enqueue(_build_request(drv_path, builder="/bin/first"))
    second_id, second_future = await queue.enqueue(_build_request(drv_path, builder="/bin/second"))

    assert second_id == first_id
    assert second_future is first_future
    assert len(queue.queue) == 1
    assert queue.by_path[str(drv_path)].build_id == first_id
