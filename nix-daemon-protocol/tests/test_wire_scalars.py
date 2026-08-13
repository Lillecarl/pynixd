"""Contracts for typed string values used by daemon protocol models."""

from __future__ import annotations

from nix_daemon_protocol import (
    ContentAddress,
    DerivedPath,
    DrvOutput,
    NARHash,
    Signature,
    StorePath,
    Time,
    TimeSpan,
    WireModel,
)
from nix_daemon_protocol.context import ReadContext, WriteContext
from nix_daemon_protocol.io import BytesReader, BytesWriter


class ScalarEnvelope(WireModel):
    """A message proving scalar Pydantic and generic-wire integration."""

    path: StorePath
    nar_hash: NARHash
    content_address: ContentAddress
    derived_path: DerivedPath
    signature: Signature
    drv_output: DrvOutput
    time: Time
    span: TimeSpan


async def test_wire_scalars_are_native_strings_on_the_generic_wire() -> None:
    value = ScalarEnvelope(
        path="/nix/store/0123456789abcdefghijklmnopqrstuv-output",
        nar_hash="sha256:abc",
        content_address="fixed:sha256:def",
        derived_path="example.drv!out",
        signature="cache:signature",
        drv_output="sha256:drv!out",
        time=1_700_000_000,
        span=30,
    )
    assert isinstance(value.path, str)
    assert value.path.path == str(value.path)
    assert value.signature.name == "cache"
    assert value.signature.signature == "signature"
    assert value.drv_output.drv_hash == "sha256:drv"
    assert value.drv_output.output_name == "out"
    assert value.time.ts == 1_700_000_000
    assert value.span.seconds == 30

    writer = BytesWriter()
    await value.to_writer(WriteContext(writer=writer, version=0))
    decoded = await ScalarEnvelope.from_reader(ReadContext(reader=BytesReader(writer.bytes()), version=0))
    assert decoded == value
    assert decoded.model_dump() == {
        "path": str(value.path),
        "nar_hash": str(value.nar_hash),
        "content_address": str(value.content_address),
        "derived_path": str(value.derived_path),
        "signature": str(value.signature),
        "drv_output": str(value.drv_output),
        "time": 1_700_000_000,
        "span": 30,
    }


def test_wire_scalar_keyword_constructors_remain_compatible() -> None:
    assert StorePath(path="/nix/store/example").path == "/nix/store/example"
    assert NARHash(hash="sha256:example").hash == "sha256:example"
    assert ContentAddress(value="text:sha256:example").value == "text:sha256:example"
    assert DerivedPath(value="example.drv!out").value == "example.drv!out"
    assert Signature(name="cache", signature="signed") == "cache:signed"
    assert DrvOutput(drv_hash="sha256:drv", output_name="out") == "sha256:drv!out"
    assert Time(ts=1).datetime.timestamp() == 1
    assert TimeSpan(seconds=30).timedelta.total_seconds() == 30
