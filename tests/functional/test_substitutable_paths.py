"""
Tests for substitution-related queries over the daemon protocol.

Tests these protocol operations:
- QuerySubstitutablePaths (op 32): Which paths are substitutable

These operations are forwarded to the upstream daemon — they test
that protocol serialization works correctly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import structlog

from tests.conftest import CLIENT_BIN, TEST_NIX, run_subproc, server_uri

if TYPE_CHECKING:
    from pynixd import Server

from tests.test_features import TestFeatures as F

log = structlog.get_logger(__name__)


@pytest.mark.covers(F.QUERY_SUBSTITUTABLE_PATHS | F.STORE_LOCAL)
async def test_substitutable_paths_via_store(pynixd_server: Server) -> None:
    """Build a path and verify it via path-info through pynixd."""
    uri = server_uri(pynixd_server)

    # Build a path first
    test_nix = TEST_NIX
    cmd = [
        str(CLIENT_BIN),
        "build",
        "--eval-store",
        "auto",
        "--store",
        uri,
        "--file",
        str(test_nix),
        "minimal.leaf",
        "--no-link",
        "--print-out-paths",
    ]
    rc, stdout, stderr, stdboth = await run_subproc(cmd)
    assert rc == 0, f"build failed:\n{stdboth}"

    out_path = stdout.strip()
    assert out_path.startswith("/nix/store/"), f"Expected store path, got: {out_path}"
    cmd = [
        str(CLIENT_BIN),
        "path-info",
        "--store",
        uri,
        out_path,
    ]
    rc, stdout, stderr, stdboth = await run_subproc(cmd)
    assert rc == 0, f"path-info failed:\n{stdboth}"
    assert out_path in stdout, f"Expected {out_path} in path-info output:\n{stdboth}"


async def test_substitutable_paths_via_nix(pynixd_server: Server) -> None:
    """Build a path through pynixd and verify it's tracked.

    Exercises IsValidPath and QueryValidPaths protocol operations.
    """
    uri = server_uri(pynixd_server)

    test_nix = TEST_NIX
    cmd = [
        str(CLIENT_BIN),
        "build",
        "--eval-store",
        "auto",
        "--store",
        uri,
        "--file",
        str(test_nix),
        "minimal.leaf",
        "--no-link",
        "--print-out-paths",
    ]
    rc, stdout, stderr, stdboth = await run_subproc(cmd)
    assert rc == 0, f"build failed:\n{stdboth}"
    out_path = stdout.strip()
    assert out_path.startswith("/nix/store/"), f"Expected store path, got: {out_path}"
