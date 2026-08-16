"""Gates and fixtures for the differential suite.

Three things have to be true before a differential test can say anything, and
each one is a skip rather than a failure:

1. The host is Linux. A chroot store cannot *build* anywhere else -- Nix says
   `building using a diverted store is not supported on this platform`. The
   whole design of this suite rests on two separated stores, so on darwin
   there is nothing to run. Use `vzrun` there.
2. nanopynix is installed. It is the oracle, and it is a dependency of this
   suite alone. `pynixd` itself does not depend on it and must not: the
   shipped proxy is pure Python and links no C++.
3. The case's experimental features are enabled in the linked Nix.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from typing import TYPE_CHECKING

import pytest

from tests._conftest.constants import STORE_PREFIX
from tests._conftest.helpers import rmtree_robust

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path

# The root every differential store lives under. Separate from the rest of the
# suite's prefix, so a teardown here cannot remove a store the session server
# is using.
DIFFERENTIAL_PREFIX = STORE_PREFIX.parent / "pynixd-differential"

_NANOPYNIX_PRESENT = importlib.util.find_spec("nanopynix") is not None


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark every test in this directory, so the shared server stays out.

    The differential tests each own their stores. The autouse session server
    of the outer suite would give them a second one they do not use, and its
    stores would then appear in neither snapshot for a reason unrelated to
    either engine.
    """
    for item in items:
        if "differential" in str(item.path.parent):
            item.add_marker(pytest.mark.no_pynixd)
            item.add_marker(pytest.mark.differential)


@pytest.fixture(scope="session", autouse=True)
def _require_a_differential_host() -> None:
    """Skip the whole directory when the host cannot answer the question."""
    if sys.platform != "linux":
        pytest.skip(
            "a chroot store cannot build off Linux "
            "(`building using a diverted store is not supported on this platform`); "
            "run this suite under vzrun",
            allow_module_level=True,
        )
    if not _NANOPYNIX_PRESENT:
        pytest.skip(
            "nanopynix is the oracle of this suite and is not installed; "
            "it is a test dependency only, and pynixd itself does not depend on it",
            allow_module_level=True,
        )
    if shutil.which("nix-instantiate") is None:
        pytest.skip("nix-instantiate is not on PATH, and both arms instantiate with it")


@pytest.fixture
async def differential_roots(request: pytest.FixtureRequest) -> AsyncGenerator[tuple[Path, Path]]:
    """Two empty store roots for one case: the pynixd arm and the Nix arm.

    Named after the test, so a failed run leaves two directories a person can
    open and compare by hand.
    """
    stem = request.node.name.replace("/", "_").replace("[", "-").replace("]", "")
    root_a = DIFFERENTIAL_PREFIX / stem / "pynixd"
    root_b = DIFFERENTIAL_PREFIX / stem / "nix"
    for root in (root_a, root_b):
        rmtree_robust(root)
        root.mkdir(parents=True, exist_ok=True)
    yield root_a, root_b
