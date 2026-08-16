"""Build each corpus case twice, and compare the two stores.

One run goes through `pynixd.goals`. The other goes through the goal system of
Nix, which nanopynix calls in process. The two runs use two separated chroot
stores, and the test compares what each build added to its own store.

Three rules shape the design.

**Instantiate identically, build differently.** Both arms get their `.drv`
from the same `nix-instantiate` invocation shape, against their own store.
Evaluation is deterministic, so the two `.drv` paths must agree, and the test
states that before it builds anything. It keeps the build as the only
difference between the arms.

**Compare the delta, not the store.** One arm is driven by a real
`nix-daemon` and the other by an in-process `LocalStore`. Each seeds its own
store in its own way, and neither way says anything about a goal system.
Comparing what the build *added* removes that from the question.

**Run the arms one at a time.** nanopynix reads both stores directly, and arm
A's store belongs to a running `nix-daemon` while the server is up. Every
direct read of that store happens before the server starts or after it stops.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, cast

import anyio
import pytest
from nanopynix_testing.nix_environment import NixTestEnvironment

from pynixd.goals.engine import GoalEngine
from pynixd.instance import Server
from pynixd.serde import (
    BuildMode,
    BuildPathsWithResultsRequest,
    DerivedPath as SerdeDerivedPath,
)
from pynixd.serde.ids import StoreId
from pynixd.store.local_db import LocalDBStore
from tests._conftest.config import make_test_spec
from tests.differential.corpus import CA_CORPUS, CORPUS, Case
from tests.differential.snapshot import compare, delta, take_snapshot

if TYPE_CHECKING:
    from pathlib import Path

    from tests.differential.snapshot import StoreSnapshot

# The settings both arms instantiate and build under. They are the settings
# that make an unprivileged chroot store work at all: no build users, so Nix
# builds as the calling user, and no substituters, so a missing path is a
# build in both arms rather than a fetch in one of them.
#
# That second one is not a detail. `ultimate` in a snapshot separates a path a
# store built from a path it fetched, and a corpus that allowed substitution
# would make the two arms disagree on it for a reason that is about timing
# rather than about either engine.
_BASE_NIX_CONFIG = {
    "build-users-group": "",
    "substituters": "",
    "require-sigs": "false",
}


def _nix_config_env(case: Case) -> dict[str, str]:
    settings = dict(_BASE_NIX_CONFIG)
    if case.experimental_features:
        settings["extra-experimental-features"] = " ".join(case.experimental_features)
    return {
        **os.environ,
        "NIX_CONFIG": "\n".join(f"{key} = {value}" for key, value in settings.items()),
    }


async def _instantiate(root: Path, case: Case) -> str:
    """Write the `.drv` of *case* into the chroot store at *root*, and name it."""
    process = await anyio.run_process(
        [
            "nix-instantiate",
            "--store",
            f"local://?root={root}",
            "--expr",
            case.expression,
        ],
        env=_nix_config_env(case),
        check=False,
    )
    if process.returncode != 0:
        message = process.stderr.decode(errors="replace")
        raise AssertionError(f"instantiating {case.name} into {root} failed:\n{message}")
    return process.stdout.decode().strip().splitlines()[-1].split("!")[0]


def _nix_environment(root: Path) -> NixTestEnvironment:
    """A nanopynix handle on the chroot store at *root*."""
    return NixTestEnvironment(backend="local", root=root, store_uri=f"local://?root={root}")


async def _read_store(root: Path) -> StoreSnapshot:
    """Snapshot the chroot store at *root*, through nanopynix."""
    environment = _nix_environment(root)
    async with environment.inproc_session() as session, session.store() as store:
        return await take_snapshot(store)


async def _build_with_pynixd(root: Path, drv_path: str, case: Case) -> Any:
    """Realise every output of *drv_path* through pynixd's goal engine."""
    spec = make_test_spec(
        store_id="local",
        store_path=root,
        extra_env=_nix_config_env(case),
    )
    async with Server(
        stores={StoreId("local"): LocalDBStore(spec)},
        ssh_port=0,
        http_port=0,
        unix_path=root.parent / "pynixd.sock",
    ) as server:
        engine = GoalEngine(server.ctx)
        # `!` and not `^`: the request carries the wire spelling of a derived
        # path, and `parse_derived_path_legacy` is what reads it.
        request = BuildPathsWithResultsRequest(
            derived_paths=cast("Any", {SerdeDerivedPath(value=f"{drv_path}!*")}),
            build_mode=BuildMode.NORMAL,
        )
        return await engine.build_paths_with_results(request)


async def _build_with_nix(root: Path, drv_path: str) -> Any:
    """Realise every output of *drv_path* through the goal system of Nix."""
    environment = _nix_environment(root)
    async with environment.inproc_session() as session, session.store() as store:
        return await store.build_paths_with_results([f"{drv_path}^*"])


@pytest.mark.parametrize("case", [*CORPUS, *CA_CORPUS], ids=lambda case: case.name)
async def test_both_engines_leave_the_same_store(case: Case, differential_roots: tuple[Path, Path]) -> None:
    """pynixd's goal engine and Nix's goal system agree on what they built."""
    root_a, root_b = differential_roots

    drv_a = await _instantiate(root_a, case)
    drv_b = await _instantiate(root_b, case)
    assert drv_a == drv_b, (
        f"the two arms instantiated {case.name} to different derivations, so the "
        f"comparison below would say nothing: {drv_a} against {drv_b}"
    )

    before_a = await _read_store(root_a)
    await _build_with_pynixd(root_a, drv_a, case)
    after_a = await _read_store(root_a)

    before_b = await _read_store(root_b)
    await _build_with_nix(root_b, drv_b)
    after_b = await _read_store(root_b)

    difference = compare(delta(before_a, after_a), delta(before_b, after_b))
    assert not difference, (
        f"{case.name}: the two goal systems left different stores.\n"
        f"This case probes: {case.probes}\n\n"
        f"{difference.describe('pynixd', 'nix')}"
    )
