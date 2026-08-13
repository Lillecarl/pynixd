"""Tests for the run_subproc helper.

All tests in this file are subprocess execution tests that don't trigger Store operations.
"""

from __future__ import annotations

from tests.conftest import run_subproc


class TestRunSubproc:
    """Test the run_subproc helper."""

    async def test_success(self):
        """Test successful subprocess execution.

        Store operations triggered:
        - None: This test only checks subprocess execution without triggering Store operations
        """
        rc, stdout, stderr, _ = await run_subproc(["echo", "hello"])
        assert stdout.strip() == "hello"

    async def test_failure(self):
        """Test subprocess failure handling.

        Store operations triggered:
        - None: This test only checks subprocess execution without triggering Store operations
        """
        rc, stdout, stderr, _ = await run_subproc(["false"], expected_retcode=1)
        assert rc == 1

    async def test_stderr(self):
        """Test subprocess stderr capture.

        Store operations triggered:
        - None: This test only checks subprocess execution without triggering Store operations
        """
        rc, stdout, stderr, _ = await run_subproc(["sh", "-c", "echo error >&2"])
        assert stderr.strip() == "error"

    async def test_nix_config(self):
        """Test NIX_CONFIG environment variable handling.

        Store operations triggered:
        - None: This test only checks subprocess execution without triggering Store operations
        """
        rc, stdout, stderr, _ = await run_subproc(
            ["sh", "-c", 'echo "$NIX_CONFIG"'],
            nix_config={"foo": "bar", "baz": "qux"},
        )
        assert "foo = bar" in stdout
        assert "baz = qux" in stdout
        assert ("substituters = https://nixkube.cachix.org unix:///nix/var/nix/daemon-socket/socket?root=/") in stdout

    async def test_nix_config_override(self):
        """Test NIX_CONFIG override behavior.

        Store operations triggered:
        - None: This test only checks subprocess execution without triggering Store operations
        """
        rc, stdout, stderr, _ = await run_subproc(
            ["sh", "-c", 'echo "$NIX_CONFIG"'],
            nix_config={"substituters": "https://example.org"},
        )
        assert "substituters = https://example.org" in stdout
        assert "substituters = https://nixkube.cachix.org unix:///nix/var/nix/daemon-socket/socket?root=/" not in stdout

    async def test_nix_config_merge(self):
        """Test NIX_CONFIG merging with environment variable.

        Store operations triggered:
        - None: This test only checks subprocess execution without triggering Store operations
        """
        rc, stdout, stderr, _ = await run_subproc(
            ["sh", "-c", 'echo "$NIX_CONFIG"'],
            env={"NIX_CONFIG": "existing = true"},
            nix_config={"foo": "bar"},
        )
        assert "existing = true" in stdout
        assert "foo = bar" in stdout
        assert "substituters = https://nixkube.cachix.org unix:///nix/var/nix/daemon-socket/socket?root=/" in stdout
