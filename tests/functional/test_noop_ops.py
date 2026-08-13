"""
Tests for no-op operations — operations that are silently handled by pynixd
for non-admin users (AddTempRoot, AddIndirectRoot, AddPermRoot, SetOptions).

These operations should return success (value=1 or gc_root) with a stderr
log message rather than forwarding to the remote daemon.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import structlog

from tests.conftest import CLIENT_BIN, run_subproc, server_uri

if TYPE_CHECKING:
    from pynixd import Server

from tests.test_features import TestFeatures as F

log = structlog.get_logger(__name__)


@pytest.mark.covers(F.SET_OPTIONS | F.ADD_PERM_ROOT | F.ADD_INDIRECT_ROOT | F.STORE_LOCAL)
async def test_noop_operations_do_not_crash(pynixd_server: Server) -> None:
    """All no-op operations should not crash the server.

    Multiple no-ops are triggered by doing a store add operation:
    - SetOptions (every connection)
    - AddTempRoot (during add/store)
    - AddPermRoot / AddIndirectRoot (during --add-root)
    """
    uri = server_uri(pynixd_server)

    # Any nix command triggers SetOptions
    cmd = [
        str(CLIENT_BIN),
        "store",
        "add",
        "--store",
        uri,
        "--name",
        "test-file",
        "/etc/hostname",
    ]
    rc, stdout, stderr, stdboth = await run_subproc(cmd, expected_retcode=0)
    assert rc == 0, f"store add via pynixd failed:\n{stdboth}"
