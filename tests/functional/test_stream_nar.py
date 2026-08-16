"""Tests for NAR streaming between stores."""

from __future__ import annotations

from pathlib import Path

import pytest
import structlog

from pynixd.serde import IsValidPathRequest, StorePath as SerdeStorePath
from pynixd.store import LocalSocketStore
from pynixd.store.transfer import stream_paths_store_to_store
from pynixd.store_path import StorePath
from tests.conftest import (
    CLIENT_BIN,
    STORE_PREFIX,
    make_test_spec,
    rmtree_robust,
    run_subproc,
)
from tests.test_features import TestFeatures as F

log = structlog.get_logger(__name__)


async def get_hello_path() -> StorePath:
    """Build nixpkgs#hello and return its store path."""
    rc, stdout, stderr, _ = await run_subproc(
        [str(CLIENT_BIN), "build", "nixpkgs#hello", "--no-link", "--print-out-paths"],
    )
    return StorePath(stdout.strip())


@pytest.mark.covers(F.NAR_FROM_PATH | F.NAR_STREAM | F.NAR_PARSE | F.STORE_LOCAL)
async def test_stream_nar() -> None:
    """
    Test streaming a NAR from the system store to a temporary store.
    This doesn't start pynixd, it just uses two LocalSocketStore instances.

    Store operations triggered:
    - NarFromPath: Gets NAR from path for streaming
    """
    # 1. Build hello in the system store
    store_path = await get_hello_path()
    log.info("streaming_path", path=store_path)

    # Source store is the system store (/)
    src_store = LocalSocketStore(
        make_test_spec(store_id="system", store_path=Path("/"), no_probe=True),
    )

    # Destination store is a temporary store
    dst_path = STORE_PREFIX / "test-stream-nar"
    rmtree_robust(dst_path)
    dst_store = LocalSocketStore(
        make_test_spec(store_id="test-stream-nar", store_path=dst_path, no_probe=True),
    )

    try:
        # 2. Stream the path from src to dst
        # Check if path exists in src
        is_valid_src = await src_store.execute(IsValidPathRequest(path=SerdeStorePath(path=str(store_path))))
        assert is_valid_src.valid, f"Path {store_path} not valid in system store"

        # Use stream_paths_store_to_store which handles the NAR piping
        await stream_paths_store_to_store(src_store, dst_store, [store_path])

        # Verify it now exists in dst
        is_valid_dst_after = await dst_store.execute(
            IsValidPathRequest(path=SerdeStorePath(path=str(store_path))),
        )
        assert is_valid_dst_after.valid
    finally:
        await src_store.close()
        await dst_store.close()
