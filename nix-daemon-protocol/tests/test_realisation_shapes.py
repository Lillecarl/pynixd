"""The build trace has two wire shapes, and a feature name picks between them.

**Nix 2.34 carries a whole `Realisation` as one JSON string.** `realisation.py`
holds that shape, and every peer at the floor of this repository speaks it.

**The master branch carries a struct, and refuses the JSON.**
`WorkerProto::Serialise<UnkeyedRealisation>` at `worker-protocol.cc:485`
raises when the negotiated set does not hold `realisation-with-path-not-hash`,
and writes an output path and a set of signatures when it does.
`WorkerProto::Serialise<DrvOutput>` at `worker-protocol.cc:544` does the same
with a derivation path and an output name.

These tests pin the second shape, byte for byte. Nothing writes it yet:
`SUPPORTED_STANDARD_FEATURES` is empty, so pynixd never claims the feature and
never meets a peer that sends it. The codec lands first and the claim lands
after, because a claim with no codec drops the connection. Issue #162.
"""

from __future__ import annotations

import pytest

from nix_daemon_protocol import (
    FEATURE_REALISATION_WITH_PATH,
    PROTOCOL_VERSION,
    BuildResult,
    KeyedDrvOutput,
    OptMicroseconds,
    Realisation,
    Signature,
    StorePath,
    UnkeyedRealisation,
)
from nix_daemon_protocol.context import ReadContext, WriteContext
from nix_daemon_protocol.io import BytesReader, BytesWriter

OUT_PATH = "/nix/store/00000000000000000000000000000000-x"
DRV_PATH = "/nix/store/11111111111111111111111111111111-x.drv"


def _a_result(**maps) -> BuildResult:
    """A `BuildResult` with every version-gated field filled in.

    The 1.29 and 1.37 fields default to `None`, and `PROTOCOL_VERSION` puts
    each of them on the wire, so a writer that meets `None` where an integer
    belongs raises. The maps are the subject here; the rest is scaffolding.
    """
    return BuildResult(
        status=0,
        error_msg="",
        times_built=1,
        is_non_deterministic=0,
        start_time=1_700_000_000,
        stop_time=1_700_000_100,
        cpu_user=OptMicroseconds(tag=0, value=0),
        cpu_system=OptMicroseconds(tag=0, value=0),
        **maps,
    )


async def _round_trip(value, model):
    """Write *value* and read it back as *model*, and answer both."""
    writer = BytesWriter("test")
    await value.to_writer(WriteContext(writer=writer, version=PROTOCOL_VERSION))
    raw = writer.get_bytes()
    read = await model.from_reader(ReadContext(reader=BytesReader(raw), version=PROTOCOL_VERSION))
    return raw, read


@pytest.mark.anyio
async def test_an_unkeyed_realisation_is_a_path_and_the_signatures() -> None:
    value = UnkeyedRealisation(out_path=StorePath(OUT_PATH), signatures={Signature("cache.example.org-1:abc")})

    _, read = await _round_trip(value, UnkeyedRealisation)

    assert read.out_path == OUT_PATH
    assert read.signatures == {"cache.example.org-1:abc"}


@pytest.mark.anyio
async def test_an_unkeyed_realisation_carries_no_dependent_realisations() -> None:
    """The field of the JSON shape is gone, and it is not empty but absent.

    `UnkeyedRealisation` of the master branch has no such member. A codec that
    wrote an empty map would put eight bytes on the wire that the peer reads
    as the next field.
    """
    assert "dependent_realisations" not in UnkeyedRealisation.model_fields
    assert "dependentRealisations" not in Realisation.model_fields  # it is there under its own name

    writer = BytesWriter("test")
    await UnkeyedRealisation(out_path=StorePath(OUT_PATH)).to_writer(
        WriteContext(writer=writer, version=PROTOCOL_VERSION),
    )

    # One string for the path, and one count of zero for the signature set,
    # and nothing after them. A Nix string is a length of eight bytes and the
    # bytes padded up to a multiple of eight.
    padded = (len(OUT_PATH) + 7) // 8 * 8
    assert len(writer.get_bytes()) == 8 + padded + 8


@pytest.mark.anyio
async def test_a_keyed_drv_output_names_a_derivation_and_not_a_hash() -> None:
    """The report on #162 saw `sha256:0000…0000!out`, which this shape cannot say."""
    value = KeyedDrvOutput(drv_path=StorePath(DRV_PATH), output_name="out")

    _, read = await _round_trip(value, KeyedDrvOutput)

    assert read.drv_path == DRV_PATH
    assert read.output_name == "out"


@pytest.mark.anyio
async def test_the_two_shapes_of_a_realisation_are_not_the_same_bytes() -> None:
    """So a peer that reads one from the other decodes nothing useful.

    This is the reason the feature has to be negotiated and not assumed. The
    wire holds no marker that says which shape it carries.
    """
    old = BytesWriter("test")
    await Realisation(out_path=StorePath(OUT_PATH)).to_writer(
        WriteContext(writer=old, version=PROTOCOL_VERSION),
    )
    new = BytesWriter("test")
    await UnkeyedRealisation(out_path=StorePath(OUT_PATH)).to_writer(
        WriteContext(writer=new, version=PROTOCOL_VERSION),
    )

    assert old.get_bytes() != new.get_bytes()
    # The old shape is one JSON string, so it holds the field names.
    assert b"outPath" in old.get_bytes()
    assert b"outPath" not in new.get_bytes()


@pytest.mark.anyio
async def test_a_build_result_carries_one_built_outputs_map_and_not_two() -> None:
    """`worker-protocol.cc:268` writes it as an if/else, and so does this.

    Two fields hold the two shapes, and exactly one of them reaches the wire.
    A model that wrote both would put a second map on the wire that the peer
    reads as whatever comes after `BuildResult`.
    """
    result = _a_result(
        built_outputs={"h!out": Realisation(out_path=StorePath(OUT_PATH))},
        built_outputs_by_name={"out": UnkeyedRealisation(out_path=StorePath(OUT_PATH))},
    )

    without = BytesWriter("test")
    await result.to_writer(WriteContext(writer=without, version=PROTOCOL_VERSION))
    with_it = BytesWriter("test")
    await result.to_writer(
        WriteContext(writer=with_it, version=PROTOCOL_VERSION, features=frozenset({FEATURE_REALISATION_WITH_PATH})),
    )

    # The JSON of the old shape names its fields; the new shape names none.
    assert b"outPath" in without.get_bytes()
    assert b"outPath" not in with_it.get_bytes()
    assert without.get_bytes() != with_it.get_bytes()


@pytest.mark.anyio
async def test_a_build_result_reads_back_the_shape_it_wrote() -> None:
    """Under the feature, the map is keyed by the output name alone."""
    result = _a_result(built_outputs_by_name={"out": UnkeyedRealisation(out_path=StorePath(OUT_PATH))})
    features = frozenset({FEATURE_REALISATION_WITH_PATH})

    writer = BytesWriter("test")
    await result.to_writer(WriteContext(writer=writer, version=PROTOCOL_VERSION, features=features))
    read = await BuildResult.from_reader(
        ReadContext(reader=BytesReader(writer.get_bytes()), version=PROTOCOL_VERSION, features=features),
    )

    assert read.built_outputs is None
    assert read.built_outputs_by_name is not None
    assert read.built_outputs_by_name["out"].out_path == OUT_PATH
