"""Roundtrip test: WireModel ↔ existing dataclass."""

from __future__ import annotations

from pynixd.serde import (
    BuildResult,
    OptMicroseconds,
    Realisation,
    StorePath as SerdeStorePath,
    WireModel,
)


async def test_wire_build_result_json_roundtrip():
    """BuildResult JSON roundtrip (exercises wire_conditional + dict + primitives)."""
    br = BuildResult(
        status=0,
        error_msg="",
        times_built=1,
        is_non_deterministic=0,
        start_time=1000000,
        stop_time=1000500,
        built_outputs={"sha256:abc!out": Realisation(out_path=SerdeStorePath(path="/nix/store/xxx-foo"))},
    )
    br.cpu_user = OptMicroseconds(tag=1, value=50000)
    br.cpu_system = OptMicroseconds(tag=0, value=None)

    # to_json
    data = br.to_json()
    assert '"status":0' in data
    assert '"error_msg":""' in data
    assert '"times_built":1' in data
    assert '"start_time":1000000' in data
    assert '"built_outputs":' in data
    assert '"cpu_user":{"tag":1,"value":50000}' in data
    assert '"cpu_system":{"tag":0,"value":null}' in data

    # from_json
    br2 = BuildResult.from_json(data)
    assert isinstance(br2, BuildResult)
    assert br2.status == 0
    assert br2.times_built == 1
    assert br2.start_time == 1000000
    assert br2.built_outputs == {"sha256:abc!out": Realisation(out_path=SerdeStorePath(path="/nix/store/xxx-foo"))}
    assert br2.cpu_user.tag == 1
    assert br2.cpu_user.value == 50000
    assert br2.cpu_system.tag == 0


async def test_wire_store_path_json():
    """SerdeStorePath serdes as plain string in JSON."""
    sp = SerdeStorePath(path="/nix/store/abc-test")

    class Req(WireModel):
        path: SerdeStorePath

    req = Req(path=sp)

    data = req.to_json()
    # SerdeStorePath serializes as plain string
    assert data == '{"path":"/nix/store/abc-test"}'

    # from_json back to Req
    req2 = Req.from_json(data)
    assert isinstance(req2.path, SerdeStorePath)  # pyright: ignore[reportAttributeAccessIssue]
    assert str(req2.path) == "/nix/store/abc-test"  # pyright: ignore[reportAttributeAccessIssue]
    assert req2.path == sp  # pyright: ignore[reportAttributeAccessIssue]


async def test_wire_build_result_json_null_conditional():
    """OptMicroseconds not present → null in JSON."""
    br = BuildResult(status=0, error_msg="")
    # cpu_user is OptMicroseconds(tag=0) by default
    assert br.cpu_user.tag == 0

    json_str = br.to_json()
    assert '"cpu_user":{"tag":0,"value":null}' in json_str
    assert '"cpu_system":{"tag":0,"value":null}' in json_str

    wm = BuildResult.from_json(json_str)
    assert wm.cpu_user.tag == 0  # pyright: ignore[reportAttributeAccessIssue]
    assert wm.cpu_system.tag == 0  # pyright: ignore[reportAttributeAccessIssue]
