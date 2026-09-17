"""A NAR size of zero means unknown, and a transfer cannot guess it.

Nix leaves `ValidPathInfo::narSize` at 0 where it has no answer, and the
shortest NAR is 96 bytes: the magic string and the shape around it. pynixd
read zero as empty, streamed no bytes for such a path, and the destination
daemon answered

    reached end of FramedSource

while reading `AddMultipleToStoreResponse`. That message names the frame and
neither the path nor the store that described it, so the cause took a full
log of one run to find. Issue #48.

It reached a real suite through a test double: `StatsTestStore` answered
`QueryClosureWithInfo` with one info per path and `nar_size=0`. It looked
like a flake, because the destination usually held the path already from the
state an earlier run left under `STORE_PREFIX` -- a fresh store transferred
and a reused one did not.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from nix_daemon_protocol.valid_path_info import ValidPathInfo
from pynixd.daemon_extensions.query_closure_with_info import QueryClosureWithInfoResponse
from pynixd.exceptions import BackendError
from pynixd.serde import (
    ContentAddress,
    NARHash,
    QueryValidPathsResponse,
    StorePath,
    Time,
    UnkeyedValidPathInfo,
)
from pynixd.store.transfer import stream_paths_store_to_store

_ZERO_HASH = "0" * 64


def _info(name: str, nar_size: int) -> ValidPathInfo:
    return ValidPathInfo(
        path=StorePath(path=f"/nix/store/{'0' * 31}{name}"),
        info=UnkeyedValidPathInfo(
            deriver=None,
            nar_hash=NARHash(hash=_ZERO_HASH),
            references=set(),
            registration_time=Time(ts=1),
            nar_size=nar_size,
            ultimate=True,
            sigs=set(),
            ca=ContentAddress(value=""),
        ),
    )


class _Source:
    """Answers the closure query, and nothing else."""

    store_id = "source"

    def __init__(self, infos: list[ValidPathInfo]) -> None:
        self._infos = infos

    async def execute(self, _request: object, client: object = None) -> QueryClosureWithInfoResponse:  # noqa: ARG002
        return QueryClosureWithInfoResponse(infos=self._infos)

    def transfer_conn(self) -> None:
        raise AssertionError("a refused transfer must not open a connection")


class _Destination:
    """Holds nothing, so every path of the closure is a path to transfer."""

    store_id = "destination"

    async def execute(self, _request: object) -> QueryValidPathsResponse:
        return QueryValidPathsResponse(paths=set())

    def transfer_conn(self) -> None:
        raise AssertionError("a refused transfer must not open a connection")

    def add_path_infos(self, _infos: object) -> None:
        raise AssertionError("a refused transfer registers nothing")


async def _transfer(infos: list[ValidPathInfo]) -> None:
    await stream_paths_store_to_store(
        cast("Any", _Source(infos)),
        cast("Any", _Destination()),
        [info.path for info in infos],
    )


async def test_an_unsized_path_is_refused_before_a_connection_opens() -> None:
    """The refusal is the point, and so is refusing it early.

    A check inside the framed writer would leave half a frame on the wire,
    which is the shape that produced the unreadable message in the first
    place.
    """
    with pytest.raises(BackendError) as caught:
        await _transfer([_info("a", nar_size=0)])
    assert "source" in str(caught.value), "the store that gave the size"
    assert "destination" in str(caught.value)
    assert "unknown" in str(caught.value), "zero is not empty"


async def test_the_path_is_named() -> None:
    """`reached end of FramedSource` named neither the path nor the store."""
    with pytest.raises(BackendError, match="0000000000000000000000000000000a"):
        await _transfer([_info("a", nar_size=0)])


async def test_one_unsized_path_refuses_the_whole_closure() -> None:
    """The frame stream announces its count first, so a partial send is worse."""
    with pytest.raises(BackendError):
        await _transfer([_info("a", nar_size=96), _info("b", nar_size=0)])


async def test_a_sized_closure_is_not_refused_here() -> None:
    """It gets as far as the connection, which this double refuses to give."""
    with pytest.raises(AssertionError, match="must not open a connection"):
        await _transfer([_info("a", nar_size=96)])
