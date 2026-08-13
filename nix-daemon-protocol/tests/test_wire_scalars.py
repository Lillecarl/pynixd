"""Contracts for typed string values used by daemon protocol models."""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING

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

if TYPE_CHECKING:
    from collections.abc import Iterator


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


# Every scalar, with a value that each helper of that scalar can read. The
# store path ends in `.drv` so that `is_derivation` takes its True branch.
_SAMPLES: dict[type, str | int] = {
    StorePath: "/nix/store/0123456789abcdefghijklmnopqrstuv-output.drv",
    NARHash: "sha256:abc",
    ContentAddress: "fixed:sha256:def",
    DerivedPath: "example.drv!out",
    Signature: "cache:signature",
    DrvOutput: "sha256:drv!out",
    Time: 1_700_000_000,
    TimeSpan: 30,
}


def _helpers_that_take_no_argument(cls: type) -> Iterator[tuple[str, bool]]:
    """Each property and each plain method of `cls` that a bare value can read.

    The second item of the pair says whether the caller must call the result.
    """
    for name, attribute in vars(cls).items():
        if name.startswith("_"):
            continue
        if isinstance(attribute, property):
            yield name, False
        elif isinstance(attribute, classmethod | staticmethod):
            continue
        elif callable(attribute) and list(inspect.signature(attribute).parameters) == ["self"]:
            yield name, True


def test_no_helper_of_a_wire_scalar_calls_itself() -> None:
    """A helper that delegates through a `str`-typed accessor must reach `str`.

    `StorePath.path` returned `self`, which is legal because a `WireScalar`
    **is** a `str`. `StorePath.endswith` then read `self.path.endswith(...)`
    and dispatched back to itself, so every call recursed 962 times and raised
    RecursionError.

    Three tests of the daemon over HTTP found it, and no test of the protocol
    did: the accessor alone is harmless, and the defect needs a helper that
    shadows a method of `str`. This gate calls each helper of each scalar, so
    the next shadowing helper reports here.
    """
    failures: list[str] = []
    for cls, sample in _SAMPLES.items():
        value = cls(sample)
        for name, must_call in _helpers_that_take_no_argument(cls):
            try:
                helper = getattr(value, name)
                if must_call:
                    helper()
            except RecursionError:
                failures.append(f"{cls.__name__}.{name}")
    assert not failures, (
        f"these helpers call themselves: {failures}. "
        "The accessor they delegate through returns `self` and not `str(self)`, "
        "so the call dispatches back to this class. See StorePath.path."
    )
