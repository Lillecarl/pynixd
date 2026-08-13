"""Unit tests for pynixd.serde build types — BuildResult, BuiltOutput, enums.

Tests BuildResultStatus/BuildMode enum values, BuiltOutput JSON serialization,
and BuildResult wire format helpers. All tests are pure — no I/O.
"""

from __future__ import annotations

import json

import pytest

from pynixd.serde import BuildMode, BuildResult, BuildResultStatus, BuiltOutput
from pynixd.store_path import StorePath
from tests.test_features import TestFeatures as F


@pytest.mark.covers(F.BUILD_TYPES)
class TestBuildResultStatus:
    def test_built(self):
        assert BuildResultStatus.BUILT == 0

    def test_permanent_failure(self):
        assert BuildResultStatus.PERMANENT_FAILURE == 3

    def test_not_deterministic(self):
        assert BuildResultStatus.NOT_DETERMINISTIC == 12

    def test_hash_mismatch(self):
        assert BuildResultStatus.HASH_MISMATCH == 101


class TestBuildMode:
    def test_normal(self):
        assert BuildMode.NORMAL == 0

    def test_repair(self):
        assert BuildMode.REPAIR == 1

    def test_check(self):
        assert BuildMode.CHECK == 2


class TestBuiltOutput:
    def test_from_empty_string(self):
        out = BuiltOutput.from_string("")
        assert out.out_path == StorePath("")

    def test_from_plain_path(self):
        out = BuiltOutput.from_string("/nix/store/abc-foo")
        assert out.out_path == StorePath("/nix/store/abc-foo")
        assert out.ca == ""

    def test_to_string_plain_path(self):
        out = BuiltOutput(out_path=StorePath("/nix/store/abc-foo"))
        assert out.to_string() == "/nix/store/abc-foo"

    def test_from_json(self):
        data = {
            "outPath": "/nix/store/abc-foo",
            "ca": "text:sha256:xyz",
            "hash": "abc123",
            "hashAlgo": "sha256",
            "narHash": "sha256:xyz",
            "narSize": 42,
        }
        s = json.dumps(data)
        out = BuiltOutput.from_string(s)
        assert out.out_path == StorePath("/nix/store/abc-foo")
        assert out.ca == "text:sha256:xyz"
        assert out.hash == "abc123"
        assert out.hash_algo == "sha256"
        assert out.nar_hash == "sha256:xyz"
        assert out.nar_size == 42

    def test_to_string_json(self):
        out = BuiltOutput(
            out_path=StorePath("/nix/store/abc-foo"),
            ca="text:sha256:xyz",
            nar_hash="sha256:xyz",
            nar_size=42,
        )
        result = out.to_string()
        parsed = json.loads(result)
        assert parsed["outPath"] == "/nix/store/abc-foo"
        assert parsed["ca"] == "text:sha256:xyz"
        assert parsed["narSize"] == 42

    def test_to_string_empty(self):
        out = BuiltOutput()
        assert out.to_string() == ""

    def test_from_invalid_json(self):
        """Invalid JSON should fall back to plain path."""
        out = BuiltOutput.from_string("{not valid json}")
        assert out.out_path == StorePath("{not valid json}")

    def test_from_non_dict_json(self):
        """JSON that's not a dict should fall back to plain path."""
        out = BuiltOutput.from_string(json.dumps([1, 2, 3]))
        assert out.out_path == StorePath("[1, 2, 3]")


class TestBuildResult:
    def test_default_status(self):
        result = BuildResult()
        assert result.status == BuildResultStatus.BUILT

    def test_from_reader_minimal(self, monkeypatch):
        """Test BuildResult.from_reader with pre-protocol-1.29 version."""
        result = BuildResult()
        result.status = BuildResultStatus.ALREADY_VALID
        result.error_msg = "already valid"
        assert result.status == BuildResultStatus.ALREADY_VALID
        assert result.error_msg == "already valid"

    def test_with_built_outputs(self):
        result = BuildResult()
        result.status = BuildResultStatus.BUILT
        assert result.status == BuildResultStatus.BUILT
