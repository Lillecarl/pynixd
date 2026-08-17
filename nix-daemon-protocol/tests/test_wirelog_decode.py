"""Read a recording into operations, and compare two of them.

These tests build a recording from the codecs of this package, so they check
the framing and the way the decoder divides the two directions. The proof
against a real daemon is the run of the functional tests, which is a separate
gate. Issue #175.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from nix_daemon_protocol.constants import (
    STDERR_LAST,
    STDERR_NEXT,
    WORKER_MAGIC_1,
    WORKER_MAGIC_2,
    proto,
)
from nix_daemon_protocol.context import WriteContext
from nix_daemon_protocol.io import BytesWriter
from nix_daemon_protocol.is_valid_path import IsValidPathRequest
from nix_daemon_protocol.query_path_info import QueryPathInfoRequest
from nix_daemon_protocol.store_path import StorePath
from nix_daemon_protocol.wirelog import Direction, compare, decode
from nix_daemon_protocol.wirelog.framing import MAGIC, encode_chunk

VERSION = proto(1, 38)
PATH_A = StorePath("/nix/store/00000000000000000000000000000000-a")
PATH_B = StorePath("/nix/store/11111111111111111111111111111111-b")


@pytest.fixture
def workdir():
    path = Path(tempfile.mkdtemp(prefix="/tmp/nixwire-"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


class Tape:
    """Build a recording, one write of one side at a time."""

    def __init__(self) -> None:
        self.raw = bytearray(MAGIC)
        self.clock = 0

    def add(self, direction: Direction, data: bytes) -> None:
        self.clock += 1
        self.raw.extend(encode_chunk(direction, self.clock, data))

    def save(self, path: Path) -> Path:
        path.write_bytes(bytes(self.raw))
        return path


def writer() -> BytesWriter:
    return BytesWriter()


async def encode_request(request) -> bytes:
    """The bytes of one request. `WireRequest.to_writer` writes its own op."""
    out = writer()
    await request.to_writer(WriteContext(writer=out, version=VERSION))
    return out.bytes()


def handshake_client(features: set[str]) -> tuple[bytes, bytes]:
    """The two writes of the client, around the first write of the daemon."""
    first = writer()
    first.write_uint64(WORKER_MAGIC_1)

    second = writer()
    second.write_uint64(VERSION)
    second.write_uint64(len(features))
    for name in sorted(features):
        second.write_string(name)
    second.write_uint64(0)  # sendCpu
    second.write_uint64(0)  # reserveSpace
    return first.bytes(), second.bytes()


def handshake_server(features: set[str], nix_version: str, trusted: int) -> tuple[bytes, bytes]:
    first = writer()
    first.write_uint64(WORKER_MAGIC_2)
    first.write_uint64(VERSION)

    second = writer()
    second.write_uint64(len(features))
    for name in sorted(features):
        second.write_string(name)
    second.write_string(nix_version)
    second.write_uint64(trusted)
    second.write_uint64(STDERR_LAST)
    return first.bytes(), second.bytes()


def response(payload: bytes = b"", logs: tuple[str, ...] = ()) -> bytes:
    out = writer()
    for text in logs:
        out.write_uint64(STDERR_NEXT)
        out.write_string(text)
    out.write_uint64(STDERR_LAST)
    return out.bytes() + payload


def valid(flag: bool) -> bytes:
    out = writer()
    out.write_uint64(int(flag))
    return out.bytes()


async def build_tape(
    path: Path,
    *,
    nix_version: str = "2.34.8",
    trusted: int = 1,
    server_features: set[str] | None = None,
    second_answer: bool = True,
    logs: tuple[str, ...] = (),
) -> Path:
    tape = Tape()
    client_one, client_two = handshake_client({"nix-command"})
    server_one, server_two = handshake_server(
        server_features if server_features is not None else {"nix-command"},
        nix_version,
        trusted,
    )
    tape.add(Direction.CLIENT, client_one)
    tape.add(Direction.SERVER, server_one)
    tape.add(Direction.CLIENT, client_two)
    tape.add(Direction.SERVER, server_two)

    tape.add(Direction.CLIENT, await encode_request(IsValidPathRequest(path=PATH_A)))
    tape.add(Direction.SERVER, response(valid(True), logs))
    tape.add(Direction.CLIENT, await encode_request(IsValidPathRequest(path=PATH_B)))
    tape.add(Direction.SERVER, response(valid(second_answer)))
    return tape.save(path)


@pytest.mark.anyio
async def test_the_handshake_decodes(workdir):
    session = await decode(await build_tape(workdir / "a.wire"))
    assert session.problem is None
    assert session.handshake is not None
    assert session.handshake.negotiated == VERSION
    assert session.handshake.nix_version == "2.34.8"
    assert session.handshake.trusted == 1


@pytest.mark.anyio
async def test_each_operation_gets_its_own_response(workdir):
    session = await decode(await build_tape(workdir / "a.wire"))
    assert [op.name for op in session.operations] == ["IsValidPath", "IsValidPath"]
    assert session.operations[0].response_payload == valid(True)
    assert session.operations[1].response_payload == valid(True)


@pytest.mark.anyio
async def test_a_log_message_is_not_part_of_the_payload(workdir):
    session = await decode(await build_tape(workdir / "a.wire", logs=("building x", "done")))
    first = session.operations[0]
    assert first.logs == ("building x", "done")
    assert first.response_payload == valid(True)


@pytest.mark.anyio
async def test_two_recordings_of_the_same_exchange_agree(workdir):
    control = await decode(await build_tape(workdir / "a.wire"))
    candidate = await decode(await build_tape(workdir / "b.wire"))
    assert compare(control, candidate) == []


@pytest.mark.anyio
async def test_a_different_answer_names_the_operation(workdir):
    control = await decode(await build_tape(workdir / "a.wire"))
    candidate = await decode(await build_tape(workdir / "b.wire", second_answer=False))
    differences = compare(control, candidate)
    assert len(differences) == 1
    assert differences[0].where == "operation 1 IsValidPath response"


@pytest.mark.anyio
async def test_the_name_and_the_version_of_the_daemon_are_exempt(workdir):
    """pynixd states its own name, and that difference is on purpose."""
    control = await decode(await build_tape(workdir / "a.wire"))
    candidate = await decode(await build_tape(workdir / "b.wire", nix_version="pynixd-0.1.0"))
    assert compare(control, candidate) == []


@pytest.mark.anyio
async def test_a_feature_that_pynixd_adds_is_exempt(workdir):
    control = await decode(await build_tape(workdir / "a.wire"))
    candidate = await decode(
        await build_tape(workdir / "b.wire", server_features={"nix-command", "feature_matrix:x86_64-linux"})
    )
    assert compare(control, candidate) == []


@pytest.mark.anyio
async def test_a_response_that_stops_early_names_its_operation(workdir):
    """The watchdog kills a run, and the recording then ends anywhere."""
    whole = (await build_tape(workdir / "a.wire")).read_bytes()
    cut = workdir / "cut.wire"
    cut.write_bytes(whole[: len(whole) - 8])
    session = await decode(cut)
    assert session.problem is not None
    assert "operation 1" in session.problem


@pytest.mark.anyio
async def test_a_request_that_stops_early_names_the_operation(workdir):
    tape = Tape()
    client_one, client_two = handshake_client({"nix-command"})
    server_one, server_two = handshake_server({"nix-command"}, "2.34.8", 1)
    tape.add(Direction.CLIENT, client_one)
    tape.add(Direction.SERVER, server_one)
    tape.add(Direction.CLIENT, client_two)
    tape.add(Direction.SERVER, server_two)
    # Half of a request: the operation number arrived, and its path did not.
    tape.add(Direction.CLIENT, (await encode_request(IsValidPathRequest(path=PATH_A)))[:12])
    session = await decode(tape.save(workdir / "half.wire"))
    assert session.problem is not None
    assert "IsValidPath" in session.problem
    assert session.operations == []


@pytest.mark.anyio
async def test_a_client_that_stopped_early_is_a_difference(workdir):
    control = await decode(await build_tape(workdir / "a.wire"))
    short = Tape()
    client_one, client_two = handshake_client({"nix-command"})
    server_one, server_two = handshake_server({"nix-command"}, "2.34.8", 1)
    short.add(Direction.CLIENT, client_one)
    short.add(Direction.SERVER, server_one)
    short.add(Direction.CLIENT, client_two)
    short.add(Direction.SERVER, server_two)
    short.add(Direction.CLIENT, await encode_request(IsValidPathRequest(path=PATH_A)))
    short.add(Direction.SERVER, response(valid(True)))
    candidate = await decode(short.save(workdir / "b.wire"))

    differences = compare(control, candidate)
    assert len(differences) == 1
    assert "2 operations to the daemon and 1 to pynixd" in differences[0].where


@pytest.mark.anyio
async def test_a_different_request_is_a_difference(workdir):
    """A client asks a different question when an earlier answer differed."""
    control = await decode(await build_tape(workdir / "a.wire"))

    other = Tape()
    client_one, client_two = handshake_client({"nix-command"})
    server_one, server_two = handshake_server({"nix-command"}, "2.34.8", 1)
    other.add(Direction.CLIENT, client_one)
    other.add(Direction.SERVER, server_one)
    other.add(Direction.CLIENT, client_two)
    other.add(Direction.SERVER, server_two)
    other.add(Direction.CLIENT, await encode_request(IsValidPathRequest(path=PATH_A)))
    other.add(Direction.SERVER, response(valid(True)))
    other.add(Direction.CLIENT, await encode_request(QueryPathInfoRequest(path=PATH_B)))
    other.add(Direction.SERVER, response(b""))
    candidate = await decode(other.save(workdir / "b.wire"))

    differences = compare(control, candidate)
    assert differences[0].where == "operation 1"
    assert "IsValidPath" in differences[0].control
    assert "QueryPathInfo" in differences[0].candidate
