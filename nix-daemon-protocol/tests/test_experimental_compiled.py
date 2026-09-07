"""Equivalence tests for the opt-in generated-code codec experiment."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from nix_daemon_protocol import (
    SUPPORTED_PROTOCOL_VERSIONS,
    AddBuildLogRequest,
    BasicDerivation,
    BuildResult,
    DerivationOutput,
    DrvOutput,
    OptMicroseconds,
    QueryPathInfoResponse,
    Realisation,
    StorePath,
)
from nix_daemon_protocol.context import ReadContext, WriteContext
from nix_daemon_protocol.experimental_compiled import compile_codec
from nix_daemon_protocol.io import BytesReader, BytesWriter
from nix_daemon_protocol.wire_ops import WireRequest

if TYPE_CHECKING:
    from nix_daemon_protocol.wire_message import WireModel


def _values() -> tuple[WireModel, ...]:
    output = StorePath(path="/nix/store/0123456789abcdefghijklmnopqrstuv-output")
    return (
        AddBuildLogRequest(path=output),
        BasicDerivation(
            outputs={"out": DerivationOutput(path=str(output))},
            input_srcs=set(),
            platform="x86_64-linux",
            builder="/nix/store/0123456789abcdefghijklmnopqrstuv-builder",
            args=["--arg", "value"],
            env={"PATH": "/bin"},
        ),
        BuildResult(
            status=0,
            error_msg="",
            times_built=1,
            is_non_deterministic=0,
            start_time=1_700_000_000,
            stop_time=1_700_000_100,
            cpu_user=OptMicroseconds(tag=1, value=1_500),
            cpu_system=OptMicroseconds(tag=1, value=250),
            built_outputs={
                "out": Realisation(
                    id=DrvOutput(drv_hash="sha256:0123456789abcdefghijklmnopqrstuv", output_name="out"),
                    out_path=output,
                    signatures=["cache:signature"],
                    dependent_realisations={},
                ),
            },
        ),
        QueryPathInfoResponse(valid=False),
    )


@pytest.mark.parametrize("version", SUPPORTED_PROTOCOL_VERSIONS)
@pytest.mark.parametrize("value", _values(), ids=lambda value: type(value).__name__)
async def test_compiled_codec_matches_generic_codec(value: WireModel, version: int) -> None:
    """Generated code has identical bytes and decoded model state."""
    generic_writer = BytesWriter()
    await value.to_writer(WriteContext(writer=generic_writer, version=version))

    codec = compile_codec(type(value), version)
    compiled_writer = BytesWriter()
    await codec.write(value, WriteContext(writer=compiled_writer, version=version))
    assert compiled_writer.bytes() == generic_writer.bytes()

    generic_reader = BytesReader(generic_writer.bytes())
    compiled_reader = BytesReader(generic_writer.bytes())
    if isinstance(value, WireRequest):
        await generic_reader.read_uint64()
        await compiled_reader.read_uint64()
    generic_decoded = await type(value).from_reader(ReadContext(reader=generic_reader, version=version))
    compiled_decoded = await codec.read(
        ReadContext(reader=compiled_reader, version=version),
    )
    assert compiled_decoded.model_dump() == generic_decoded.model_dump()


def test_compiled_codec_exposes_inspectable_source() -> None:
    """The experiment remains reviewable rather than opaque generated magic."""
    codec = compile_codec(BuildResult, SUPPORTED_PROTOCOL_VERSIONS[-1])
    assert codec.schema.model is BuildResult
    assert codec.schema.version == SUPPORTED_PROTOCOL_VERSIONS[-1]
    assert "value.status" in codec.write_source
    assert "await ctx.reader.read_uint64()" in codec.read_source
