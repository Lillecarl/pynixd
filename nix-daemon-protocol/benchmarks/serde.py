"""Profile representative daemon-codec serialization and deserialization.

Run with ``python nix-daemon-protocol/benchmarks/serde.py --iterations 5000``.
Install the optional ``benchmark`` dependency when pyinstrument is not already
available.
"""

from __future__ import annotations

import argparse
import asyncio
import time
from typing import TYPE_CHECKING

from pyinstrument import Profiler

from nix_daemon_protocol import (
    PROTOCOL_VERSION,
    BasicDerivation,
    BuildDerivationRequest,
    BuildPathsWithResultsResponse,
    BuildResult,
    ContentAddress,
    DerivationOutput,
    DerivedPath,
    DrvOutput,
    KeyedBuildResult,
    NARHash,
    OptMicroseconds,
    QueryPathInfoResponse,
    Realisation,
    Signature,
    StorePath,
    Time,
    UnkeyedValidPathInfo,
    WireModel,
    WireRequest,
)
from nix_daemon_protocol.context import ReadContext, WriteContext
from nix_daemon_protocol.experimental_compiled import compile_codec
from nix_daemon_protocol.io import BytesReader, BytesWriter

if TYPE_CHECKING:
    from collections.abc import Sequence


def _workload() -> tuple[WireModel, ...]:
    """Build representative nested request and response payloads."""
    output_path = StorePath(path="/nix/store/0123456789abcdefghijklmnopqrstuv-output")
    references: set[StorePath] = set()
    references.add(output_path)
    signatures: set[Signature] = set()
    signatures.add(Signature(name="cache", signature="signature"))
    input_srcs: set[StorePath] = set()
    input_srcs.add(StorePath(path="/nix/store/0123456789abcdefghijklmnopqrstuv-input"))
    info = UnkeyedValidPathInfo(
        deriver=StorePath(path="/nix/store/0123456789abcdefghijklmnopqrstuv-source.drv"),
        nar_hash=NARHash(hash="sha256:0123456789abcdefghijklmnopqrstuv"),
        references=references,
        registration_time=Time(ts=1_700_000_000),
        nar_size=1_048_576,
        ultimate=True,
        sigs=signatures,
        ca=ContentAddress(value="fixed:sha256:0123456789abcdefghijklmnopqrstuv"),
    )
    realisation = Realisation(
        id=DrvOutput(drv_hash="sha256:0123456789abcdefghijklmnopqrstuv", output_name="out"),
        out_path=output_path,
        signatures=["cache:signature"],
        dependent_realisations={"input": "sha256:abcdefghijklmnopqrstuv0123456789!out"},
    )
    result = BuildResult(
        status=0,
        error_msg="",
        times_built=1,
        is_non_deterministic=0,
        start_time=1_700_000_000,
        stop_time=1_700_000_100,
        cpu_user=OptMicroseconds(tag=1, value=1_500),
        cpu_system=OptMicroseconds(tag=1, value=250),
        built_outputs={"out": realisation},
    )
    return (
        BuildDerivationRequest(
            drv_path=StorePath(path="/nix/store/0123456789abcdefghijklmnopqrstuv-example.drv"),
            derivation=BasicDerivation(
                outputs={"out": DerivationOutput(path=str(output_path))},
                input_srcs=input_srcs,
                platform="x86_64-linux",
                builder="/nix/store/0123456789abcdefghijklmnopqrstuv-builder",
                args=["--arg", "value"],
                env={"PATH": "/bin", "system": "x86_64-linux"},
            ),
            build_mode=0,
        ),
        QueryPathInfoResponse(valid=True, info=info),
        BuildPathsWithResultsResponse(
            results=[KeyedBuildResult(path=DerivedPath(value="example.drv!out"), result=result)],
        ),
    )


async def _roundtrip(value: WireModel) -> None:
    writer = BytesWriter()
    write_context = WriteContext(writer=writer, version=PROTOCOL_VERSION)
    await value.to_writer(write_context)

    reader = BytesReader(writer.bytes())
    if isinstance(value, WireRequest):
        await reader.read_uint64()
    await type(value).from_reader(ReadContext(reader=reader, version=PROTOCOL_VERSION))


async def _profile(values: Sequence[WireModel], iterations: int) -> None:
    for _ in range(iterations):
        for value in values:
            await _roundtrip(value)


async def _compiled_roundtrip(value: WireModel) -> None:
    """Roundtrip through generated codecs where the outer model is eligible."""
    try:
        codec = compile_codec(type(value), PROTOCOL_VERSION)
    except TypeError:
        await _roundtrip(value)
        return

    writer = BytesWriter()
    write_context = WriteContext(writer=writer, version=PROTOCOL_VERSION)
    await codec.write(value, write_context)
    reader = BytesReader(writer.bytes())
    if isinstance(value, WireRequest):
        await reader.read_uint64()
    await codec.read(ReadContext(reader=reader, version=PROTOCOL_VERSION))


async def _profile_compiled(values: Sequence[WireModel], iterations: int) -> None:
    for _ in range(iterations):
        for value in values:
            await _compiled_roundtrip(value)


async def main(iterations: int, compiled: bool) -> None:
    """Run the benchmark and print a Pyinstrument report."""
    values = _workload()
    profiler = Profiler(async_mode="enabled")
    started_at = time.perf_counter()
    profiler.start()
    if compiled:
        await _profile_compiled(values, iterations)
    else:
        await _profile(values, iterations)
    profiler.stop()
    elapsed = time.perf_counter() - started_at
    operations = iterations * len(values)

    label = "compiled eligible models" if compiled else "generic"
    print(f"{label}: {operations:,} encode/decode roundtrips in {elapsed:.3f}s ({operations / elapsed:,.0f}/s)")
    print(profiler.output_text(unicode=True, color=False, show_all=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=5_000)
    parser.add_argument("--compiled", action="store_true", help="Use the experimental generated codecs where eligible.")
    args = parser.parse_args()
    asyncio.run(main(args.iterations, args.compiled))
