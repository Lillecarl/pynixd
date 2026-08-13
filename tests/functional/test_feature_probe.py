"""Feature probe: send BuildDerivationRequest with requiredSystemFeatures
directly to a store, constructing derivations entirely in-memory.

No .drv files on disk needed — the daemon uses the wire-provided BasicDerivation.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import structlog
from pynixd.serde.ids import StoreId

from pynixd.config import SSHSubprocessStoreSpec
from pynixd.store import LocalSocketStore, SSHSubprocessStore
from tests._conftest.nix_config import for_ca_derivations
from tests.conftest import (
    STORE_PREFIX,
    make_test_spec,
    rmtree_robust,
)
from tests.test_features import TestFeatures as F

log = structlog.get_logger(__name__)

FEATURE_NIX_CONFIG = for_ca_derivations(
    substituters=(
        "https://nixkube.cachix.org/",
        "unix:///nix/var/nix/daemon-socket/socket?root=/",
    ),
    trusted_public_keys=("nixkube.cachix.org-1:H8UE0jlI9pxHexK/NhDmEoLDarJXp1WTymQrsajlh7M=",),
)


@pytest.mark.covers(F.PROBE_FEATURES | F.PROBE_SYSTEMS | F.BUILD_DERIVATION | F.STORE_LOCAL)
async def test_feature_probe_in_memory() -> None:
    store_path = STORE_PREFIX / "feature-probe"
    rmtree_robust(store_path)
    store: LocalSocketStore | SSHSubprocessStore = LocalSocketStore(
        make_test_spec(
            store_id="feature-probe",
            store_path=store_path,
            nix_config=FEATURE_NIX_CONFIG,
        ),
    )
    await store.ensure_daemon()

    await store.probe()

    fm = store.feature_matrix
    assert fm, "feature_matrix should not be empty after probe"
    assert "x86_64-linux" in fm
    assert "big-parallel" in fm["x86_64-linux"]
    assert "ca-derivations" in fm["x86_64-linux"]
    assert "nixos-test" in fm["x86_64-linux"]
    assert "apple-virt" not in fm.get("x86_64-linux", set())
    assert "uid-range" not in fm.get("x86_64-linux", set())


@pytest.mark.nixbuild
@pytest.mark.skip(reason="requires nixbuild.net; pass -m nixbuild to run")
async def test_feature_probe_nixbuild_net() -> None:
    store = SSHSubprocessStore(
        SSHSubprocessStoreSpec(
            host="eu.nixbuild.net",
            store_id=StoreId("nixbuild-net"),
            username="lillecarl",
            client_keys=[Path("~/.ssh/id_ed25519")],
        ),
    )

    await store.probe()

    fm = store.feature_matrix
    assert fm, "feature_matrix should not be empty after probe"
    assert "x86_64-linux" in fm
    assert "benchmark" in fm["x86_64-linux"]
    assert "big-parallel" in fm["x86_64-linux"]
    assert "nixos-test" in fm["x86_64-linux"]
