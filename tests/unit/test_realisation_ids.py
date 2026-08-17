"""The id of a realisation, after pynixd resolves the derivation it sends.

pynixd flattens `inputDrvs` into `inputSrcs` before it sends a derivation, so
the daemon reads a different ATerm and answers with a different `DrvOutput`.
The client holds the original derivation and asks for the original id. These
tests state the difference, and then state that the goal corrects it.

`test_drv_hash.py` holds the same two derivations, and it states why they are
the oracle for the hash itself. Issue #182.
"""

from __future__ import annotations

import asyncio
import dataclasses
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest
from test_drv_hash import BASE_PATH, BASE_TEXT, TOP_TEXT

from pynixd.drv_hash import output_hashes
from pynixd.drv_parser import parse_drv
from pynixd.goals.build_derivation import BuildDerivationGoal
from pynixd.serde import (
    BasicDerivation,
    BuildDerivationRequest,
    BuildDerivationResponse,
    BuildMode,
    BuildResult,
    BuildResultStatus,
    DerivationOutput,
    Realisation,
    RegisterDrvOutputRequest,
    StorePath as SerdeStorePath,
)
from pynixd.serde.ids import BuildId

if TYPE_CHECKING:
    from pynixd.context import PynixdContext
    from pynixd.goals.engine import GoalEngine

TOP_PATH = "/nix/store/00000000000000000000000000000002-drv-hash-top.drv"
BASE_OUT = "/nix/store/d69pwdv3lma9vw5hgd09hlcnv484j307-drv-hash-base"
TOP_OUT = "/nix/store/331zkap2q342l1x5g9sd6i5cxqbwmw4a-drv-hash-top"


async def _read_drv(drv_path: str):
    if drv_path == BASE_PATH:
        return parse_drv(BASE_TEXT)
    if drv_path == TOP_PATH:
        return parse_drv(TOP_TEXT)
    return None


def _flattened_hash() -> str:
    """The hash the daemon computes for the derivation that pynixd sends.

    `to_basic_derivation` moves the output of each input derivation into
    `inputSrcs` and leaves `inputDrvs` empty. This makes the same derivation.
    """
    resolved = dataclasses.replace(
        parse_drv(TOP_TEXT),
        input_drvs={},
        input_srcs={BASE_OUT},  # pyright: ignore[reportArgumentType] -- StorePath is a str
    )
    return resolved.hash_derivation_modulo(mask_outputs=True)["out"]


class FakeScheduler:
    def __init__(self, response: BuildDerivationResponse) -> None:
        self.response = response

    async def build_derivation(
        self,
        request: BuildDerivationRequest,
        *,
        from_goal_path: bool = False,
    ) -> tuple[BuildId, asyncio.Future[BuildDerivationResponse]]:
        del request, from_goal_path
        future: asyncio.Future[BuildDerivationResponse] = asyncio.get_running_loop().create_future()
        future.set_result(self.response)
        return BuildId(1), future


class FakeLocalStore:
    def __init__(self) -> None:
        self.registered: list[Realisation] = []
        self.read_derivation = _read_drv

    async def execute(self, request):
        if isinstance(request, RegisterDrvOutputRequest):
            self.registered.append(request.realisation)
            return SimpleNamespace()
        return SimpleNamespace(valid=True)


class FakeEngine:
    def __init__(self, response: BuildDerivationResponse) -> None:
        self.local_store = FakeLocalStore()
        self.ctx = cast(
            "PynixdContext",
            SimpleNamespace(scheduler=FakeScheduler(response), local_store=self.local_store),
        )

    async def subscribe_build(self, build_id: BuildId, client) -> bool:
        del build_id, client
        return True

    async def unsubscribe_build(self, build_id: BuildId, client) -> None:
        del build_id, client


def _response(key: str) -> BuildDerivationResponse:
    return BuildDerivationResponse(
        result=BuildResult(
            status=BuildResultStatus.BUILT,
            built_outputs={key: Realisation(id=key, out_path=SerdeStorePath(path=TOP_OUT))},
        )
    )


def _request(*, floating: bool) -> BuildDerivationRequest:
    output = (
        DerivationOutput(path="", method="r:sha256", hash_digest="")
        if floating
        else DerivationOutput(path=TOP_OUT, method="", hash_digest="")
    )
    return BuildDerivationRequest(
        drv_path=SerdeStorePath(path=TOP_PATH),
        derivation=BasicDerivation(
            outputs={"out": output},
            input_srcs={SerdeStorePath(path=BASE_OUT)},  # pyright: ignore[reportUnhashable]
            platform="x86_64-linux",
            builder="/bin/sh",
        ),
        build_mode=BuildMode.NORMAL,
    )


@pytest.mark.anyio
async def test_the_flattened_derivation_hashes_to_something_else() -> None:
    """The premise. Without this difference the correction has no subject."""
    original = await output_hashes(parse_drv(TOP_TEXT), _read_drv, cache={})

    assert original is not None
    assert original["out"] != _flattened_hash()


@pytest.mark.anyio
async def test_the_answer_carries_the_id_of_the_original_derivation() -> None:
    original = await output_hashes(parse_drv(TOP_TEXT), _read_drv, cache={})
    if original is None:
        raise AssertionError("the walk read both derivations, so it has an answer")
    sent = f"sha256:{_flattened_hash()}!out"
    wanted = f"sha256:{original['out']}!out"

    engine = FakeEngine(_response(sent))
    goal = BuildDerivationGoal(cast("GoalEngine", engine), _request(floating=False))
    result = await goal.result()

    assert list(result.result.built_outputs) == [wanted]
    assert str(result.result.built_outputs[wanted].out_path) == TOP_OUT
    assert result.result.built_outputs[wanted].id.output_name == "out"


@pytest.mark.anyio
async def test_pynixd_registers_the_id_that_the_client_asks_for() -> None:
    """`queryPartialDerivationOutputMap` of the client uses the original hash.

    `store-api.cc:406` reads `staticOutputHashes` of the derivation in the
    store of the client, so a realisation under any other id answers nothing.
    """
    original = await output_hashes(parse_drv(TOP_TEXT), _read_drv, cache={})
    if original is None:
        raise AssertionError("the walk read both derivations, so it has an answer")
    wanted = f"sha256:{original['out']}!out"

    engine = FakeEngine(_response(f"sha256:{_flattened_hash()}!out"))
    goal = BuildDerivationGoal(cast("GoalEngine", engine), _request(floating=True))
    await goal.result()

    assert [str(item.id) for item in engine.local_store.registered] == [wanted]


@pytest.mark.anyio
async def test_a_signature_does_not_survive_the_new_id() -> None:
    """A signature covers the id, so it means nothing under a different one.

    `derivation-goal.cc:234` of Nix clears them and signs again.
    """
    response = _response(f"sha256:{_flattened_hash()}!out")
    for realisation in response.result.built_outputs.values():
        realisation.signatures = ["key1:notarealsignature"]

    engine = FakeEngine(response)
    goal = BuildDerivationGoal(cast("GoalEngine", engine), _request(floating=True))
    result = await goal.result()

    assert [item.signatures for item in result.result.built_outputs.values()] == [[]]


@pytest.mark.anyio
async def test_an_id_that_already_agrees_stays_as_it_is() -> None:
    original = await output_hashes(parse_drv(TOP_TEXT), _read_drv, cache={})
    if original is None:
        raise AssertionError("the walk read both derivations, so it has an answer")
    wanted = f"sha256:{original['out']}!out"

    engine = FakeEngine(_response(wanted))
    goal = BuildDerivationGoal(cast("GoalEngine", engine), _request(floating=True))
    result = await goal.result()

    assert list(result.result.built_outputs) == [wanted]
    assert [str(item.id) for item in engine.local_store.registered] == [wanted]
