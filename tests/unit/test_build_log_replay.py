"""One client subscribes many times to one build, and the replay runs once.

A build is shared. A client asks for it as a root goal, and it asks for it
again as the input derivation of another goal. `QueuedBuild.add_subscriber`
replayed the whole log each time, so the client printed the error of one
build two or three times.

`build.sh:167` of the Nix functional suite counts the `error:` lines of
`nix build -f fod-failing.nix -j1 -L`. It expects one. Issue #196.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, cast

import pytest

from pynixd.build_queue import QueuedBuild
from pynixd.serde.ids import BuildId

if TYPE_CHECKING:
    from pynixd.connection import ClientConn
    from pynixd.serde import BuildDerivationRequest


class FakeClient:
    """A client that keeps every block of bytes that the build sends it."""

    def __init__(self) -> None:
        self.blocks: list[bytes] = []

    async def send_raw(self, raw: bytes) -> None:
        self.blocks.append(raw)


def _build() -> QueuedBuild:
    return QueuedBuild(
        build_id=BuildId("build-1"),
        request=cast("BuildDerivationRequest", object()),
        future=asyncio.get_running_loop().create_future(),
    )


def _log(build: QueuedBuild, text: bytes) -> None:
    """Put bytes in the log of the build, the way a build line arrives."""
    build._log_writer.write(text)  # noqa: SLF001 -- the replay of that buffer is the unit under test


@pytest.mark.anyio
async def test_the_second_subscription_replays_nothing() -> None:
    build = _build()
    _log(build, b"error: hash mismatch\n")
    client: Any = FakeClient()

    await build.add_subscriber(cast("ClientConn", client))
    await build.add_subscriber(cast("ClientConn", client))

    assert client.blocks == [b"error: hash mismatch\n"]


@pytest.mark.anyio
async def test_the_second_subscription_still_counts() -> None:
    """The build must stay subscribed until each goal lets it go."""
    build = _build()
    client: Any = FakeClient()

    await build.add_subscriber(cast("ClientConn", client))
    await build.add_subscriber(cast("ClientConn", client))

    assert await build.remove_subscriber(cast("ClientConn", client)) is True
    assert build.subscribers == [client]
    assert await build.remove_subscriber(cast("ClientConn", client)) is True
    assert build.subscribers == []


@pytest.mark.anyio
async def test_the_client_appears_once_in_the_fan_out_list() -> None:
    build = _build()
    client: Any = FakeClient()

    await build.add_subscriber(cast("ClientConn", client))
    await build.add_subscriber(cast("ClientConn", client))

    assert build.subscribers == [client]


@pytest.mark.anyio
async def test_a_second_client_gets_its_own_replay() -> None:
    build = _build()
    _log(build, b"building\n")
    first: Any = FakeClient()
    second: Any = FakeClient()

    await build.add_subscriber(cast("ClientConn", first))
    await build.add_subscriber(cast("ClientConn", second))

    assert first.blocks == [b"building\n"]
    assert second.blocks == [b"building\n"]
