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

    def __init__(self, raises: BaseException | None = None) -> None:
        self.blocks: list[bytes] = []
        self.raises = raises

    async def send_raw(self, raw: bytes) -> None:
        if self.raises is not None:
            raise self.raises
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
@pytest.mark.parametrize(
    "error",
    [
        # uvloop, for a write that starts after the loop dropped the transport.
        RuntimeError("unable to perform operation on <UnixTransport closed=True>; the handler is closed"),
        # A peer that goes away during the write. Both are subclasses of OSError.
        BrokenPipeError(32, "Broken pipe"),
        ConnectionResetError(104, "Connection reset by peer"),
    ],
    ids=["closed-transport", "broken-pipe", "reset"],
)
async def test_a_client_that_is_gone_drops_out_of_the_fan_out(error: BaseException) -> None:
    """A build outlives its client, so the fan-out meets a closed transport.

    `RuntimeError` was not in the caught set, so it left the task group of
    `post_log_bytes` as an `ExceptionGroup`, then left
    `Scheduler.execute_build` through its own error path. Nothing retrieves
    the exception of that task. Issue #196.
    """
    build = _build()
    good: Any = FakeClient()
    gone: Any = FakeClient(raises=error)
    await build.add_subscriber(cast("ClientConn", good))
    build.subscribers.append(gone)
    build._subscriber_refs[gone] = 1  # noqa: SLF001 -- the fan-out reads this, and the replay is not the subject

    await build.post_log_bytes(b"building\n")

    assert good.blocks == [b"building\n"]
    assert build.subscribers == [good]


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
