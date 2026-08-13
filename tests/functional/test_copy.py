"""Tests for copying store paths between stores."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.conftest import (
    CLIENT_BIN,
    run_subproc,
    server_uri,
)

if TYPE_CHECKING:
    from pynixd import Server

from tests.test_features import TestFeatures as F


@pytest.mark.covers(
    F.COPY_MULTIPLE
    | F.QUERY_VALID_PATHS
    | F.ADD_MULTIPLE_TO_STORE
    | F.NAR_FROM_PATH
    | F.QUERY_PATH_INFO
    | F.QUERY_CLOSURE
    | F.STORE_HTTP_BINARY_CACHE
    | F.STORE_POOL
)
async def test_copy(pynixd_server: Server):
    """Copy paths between two stores via UDS.

    Store operations triggered:
    - AddMultipleToStore: Adds multiple paths to store
    - QueryValidPaths: Queries valid paths
    - RegisterDrvOutput: Registers derivation output
    """

    await run_subproc([CLIENT_BIN, "build", "nixpkgs#hello", "--no-link"])
    await run_subproc(
        [
            CLIENT_BIN,
            "copy",
            "--from",
            "daemon",
            "--to",
            server_uri(pynixd_server),
            "nixpkgs#hello",
        ],
    )
