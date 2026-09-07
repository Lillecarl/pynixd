"""A wire field chooses its shape by the negotiated feature set.

**The protocol number stopped at 1.38, so a version alone no longer says what
shape a field has.** `worker-protocol.hh:105` of Nix states the rule, and
`worker-protocol.cc:268` is the shape that follows from it:
`BuildResult.builtOutputs` is a map of `UnkeyedRealisation` when
`realisation-with-path-not-hash` is on, and a map of JSON strings when it is
off. One `min_version` cannot express that, because both shapes live at 1.38.

`WireField` therefore takes `needs_features` and `unless_features`, and
`ReadContext` and `WriteContext` carry the set that the two peers negotiated.
Issue #162.
"""

from __future__ import annotations

import pytest

from nix_daemon_protocol.context import ReadContext, WriteContext
from nix_daemon_protocol.wire_message import WireField, WireModel
from pynixd import wire

FEATURE = "realisation-with-path-not-hash"


class Gated(WireModel):
    """One field for each half of the choice that Nix writes as an if/else."""

    always: int = WireField(default=0)
    new_shape: int = WireField(default=0, needs_features=[FEATURE])
    old_shape: int = WireField(default=0, unless_features=[FEATURE])


async def _round_trip(features: frozenset[str]) -> tuple[bytes, Gated]:
    """Write a `Gated` and read it back under the same feature set."""
    writer = wire.BytesWriter("test")
    await Gated(always=1, new_shape=2, old_shape=3).to_writer(
        WriteContext(writer=writer, version=0, features=features),
    )
    raw = writer.get_bytes()
    read = await Gated.from_reader(
        ReadContext(reader=wire.BytesReader(raw), version=0, features=features),
    )
    return raw, read


@pytest.mark.anyio
async def test_no_feature_keeps_the_old_shape() -> None:
    """The empty set is the ordinary case, and not a fallback.

    Nix 2.34 names no feature at 1.38, and every peer below 1.38 names none
    at all.
    """
    raw, read = await _round_trip(frozenset())

    # Two fields of eight bytes each: `always` and `old_shape`.
    assert len(raw) == 16
    assert read.always == 1
    assert read.old_shape == 3


@pytest.mark.anyio
async def test_the_feature_keeps_the_new_shape() -> None:
    raw, read = await _round_trip(frozenset({FEATURE}))

    assert len(raw) == 16
    assert read.always == 1
    assert read.new_shape == 2


@pytest.mark.anyio
async def test_another_feature_decides_nothing() -> None:
    """A gate names the feature it reads, and no other name reaches it."""
    raw, _ = await _round_trip(frozenset({"delete-dead-specific-referrers"}))

    assert len(raw) == 16


@pytest.mark.anyio
async def test_the_two_sides_disagree_when_the_sets_disagree() -> None:
    """This is the failure that the negotiation exists to stop.

    A writer that names the feature and a reader that does not read different
    fields from the same bytes. The wire does not say which shape it holds,
    so the bytes decode to a wrong value rather than raise. Both sides must
    take the set from `negotiate_features`, and never their own.
    """
    writer = wire.BytesWriter("test")
    await Gated(always=1, new_shape=2, old_shape=3).to_writer(
        WriteContext(writer=writer, version=0, features=frozenset({FEATURE})),
    )
    read = await Gated.from_reader(
        ReadContext(reader=wire.BytesReader(writer.get_bytes()), version=0, features=frozenset()),
    )

    # The reader took the `new_shape` bytes as `old_shape`.
    assert read.old_shape == 2


@pytest.mark.anyio
async def test_a_nested_model_reads_the_same_set() -> None:
    """The set has to reach every level, or a nested field takes the wrong shape.

    `_find_reader` builds a fresh `ReadContext` for a nested `WireModel`, and
    that context carried no feature set until this. `BuildResult` holds
    `builtOutputs`, so the gated field of #162 is a nested one.
    """

    class Outer(WireModel):
        inner: Gated = WireField(default_factory=Gated)

    writer = wire.BytesWriter("test")
    await Outer(inner=Gated(always=1, new_shape=2, old_shape=3)).to_writer(
        WriteContext(writer=writer, version=0, features=frozenset({FEATURE})),
    )
    read = await Outer.from_reader(
        ReadContext(reader=wire.BytesReader(writer.get_bytes()), version=0, features=frozenset({FEATURE})),
    )

    assert read.inner.new_shape == 2
