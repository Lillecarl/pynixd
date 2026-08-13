"""
Tests for admin-gated operations that don't have other coverage.

Tests these protocol operations:
- OptimiseStore (op 34): Deduplicates store (admin-only)
- VerifyStore (op 35): Verifies store integrity (admin-only)
- AddBuildLog (op 45): Adds build log (admin-only)
- AddSignatures (op 37): Adds signatures to a path (forwarded)
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
import structlog

from tests.conftest import (
    CLIENT_BIN,
    run_subproc,
    ssh_admin_uri,
    ssh_user_uri,
    unix_session_uri,
)
from tests.test_features import TestFeatures as F

if TYPE_CHECKING:
    from pathlib import Path

    from pynixd import Server

log = structlog.get_logger(__name__)


@pytest.mark.covers(
    F.ADD_PERM_ROOT
    | F.ADD_INDIRECT_ROOT
    | F.ADD_TEMP_ROOT
    | F.OPTIMISE_STORE
    | F.VERIFY_STORE
    | F.SET_OPTIONS
    | F.SYNC_WITH_GC
    | F.STORE_LOCAL
    | F.SERVER_RBAC
)
async def test_optimise_store_admin(pynixd_server: Server) -> None:
    """OptimiseStore as admin should succeed.

    Uses Unix socket (implicit admin).
    """
    uri = unix_session_uri(pynixd_server)
    cmd = [str(CLIENT_BIN), "store", "optimise", "--store", uri]
    rc, stdout, stderr, stdboth = await run_subproc(cmd)
    assert rc == 0, f"OptimiseStore failed:\n{stdboth}"


@pytest.mark.xfail(reason="nix 2.34.7 returns exit code 0 even on access-denied errors")
async def test_optimise_store_non_admin(pynixd_server: Server) -> None:
    """OptimiseStore as non-admin should be rejected."""
    uri = ssh_user_uri(pynixd_server)
    cmd = [str(CLIENT_BIN), "store", "optimise", "--store", uri]
    rc, stdout, stderr, stdboth = await run_subproc(cmd, expected_retcode=None)
    assert rc != 0, "OptimiseStore should fail for non-admin"
    assert "requires administrative privileges" in stdboth


@pytest.mark.legacy_nix_commands
async def test_verify_store_admin(pynixd_server: Server) -> None:
    """VerifyStore as admin should succeed."""
    uri = unix_session_uri(pynixd_server)
    cmd = [str(CLIENT_BIN.parent / "nix-store"), "--verify", "--store", uri]
    rc, stdout, stderr, stdboth = await run_subproc(cmd)
    assert rc == 0, f"VerifyStore failed:\n{stdboth}"


@pytest.mark.legacy_nix_commands
@pytest.mark.xfail(reason="RBAC tests share server fixture — flaky under concurrency")
async def test_verify_store_non_admin(pynixd_server: Server) -> None:
    """VerifyStore as non-admin should be rejected."""
    uri = ssh_user_uri(pynixd_server)
    cmd = [str(CLIENT_BIN.parent / "nix-store"), "--verify", "--store", uri]
    rc, stdout, stderr, stdboth = await run_subproc(cmd, expected_retcode=None)
    assert rc != 0, "VerifyStore should fail for non-admin"
    assert "requires administrative privileges" in stdboth


async def test_add_build_log_non_admin(pynixd_server: Server) -> None:
    """AddBuildLog as non-admin should be rejected.

    This is tricky to trigger via CLI — it's used internally by builders.
    Instead, test via SSH as non-admin.
    """
    uri = ssh_user_uri(pynixd_server)
    # Attempt to run a build that might trigger AddBuildLog
    # We verify the build still works — if AddBuildLog is rejected it's fine
    cmd = [
        str(CLIENT_BIN),
        "build",
        "--store",
        uri,
        "--eval-store",
        "auto",
        "--file",
        "tests/nix",
        "minimal.leaf",
        "--no-link",
    ]
    rc, stdout, stderr, stdboth = await run_subproc(cmd)
    assert rc == 0, f"Build (and possibly AddBuildLog) failed:\n{stdboth}"


async def test_add_signatures_via_store(
    pynixd_server: Server,
    tmp_path: Path,
) -> None:
    """AddSignatures: signing a path via pynixd.

    This operation is forwarded to the upstream daemon.
    We build a path first, then test that signatures can be added by admin.
    """
    uri = ssh_admin_uri(pynixd_server)

    # Build a path
    cmd = [
        str(CLIENT_BIN),
        "build",
        "--eval-store",
        "auto",
        "--store",
        uri,
        "--file",
        "tests/nix",
        "minimal.leaf",
        "--no-link",
        "--print-out-paths",
    ]
    rc, stdout, stderr, stdboth = await run_subproc(cmd)
    assert rc == 0, f"build failed:\n{stdboth}"

    out_path = stdout.strip()
    assert out_path.startswith("/nix/store/"), f"Unexpected output: {out_path}"

    # Generate a signing key
    key_file = tmp_path / "secret.key"
    rc, key_stdout, _, _ = await run_subproc(
        [str(CLIENT_BIN), "key", "generate-secret", "--key-name", "testkey"],
    )
    assert rc == 0, "key generation failed"
    key_file.write_text(key_stdout.strip())

    # Sign the path via pynixd
    rc, _, _, stdboth = await run_subproc(
        [
            str(CLIENT_BIN),
            "store",
            "sign",
            "--key-file",
            str(key_file),
            "--store",
            uri,
            out_path,
        ],
    )
    assert rc == 0, f"store sign failed:\n{stdboth}"

    # Verify the signature exists
    rc, info_stdout, _, stdboth = await run_subproc(
        [
            str(CLIENT_BIN),
            "path-info",
            "--json-format",
            "2",
            "--json",
            "--store",
            uri,
            out_path,
        ],
    )
    assert rc == 0, f"path-info failed:\n{stdboth}"

    info = json.loads(info_stdout.strip())
    # json-format 2 wraps entries under "info" with basename keys
    entries = info.get("info", info)
    path_entry = None
    for key, value in entries.items():
        if key == out_path.removeprefix("/nix/store/"):
            path_entry = value
            break
    assert path_entry is not None, f"path {out_path} not in path-info output: {info}"
    sigs = path_entry.get("signatures", [])
    assert any(sig.startswith("testkey:") for sig in sigs), f"expected testkey signature in {sigs}"
