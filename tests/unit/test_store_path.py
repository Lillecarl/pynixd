"""Unit tests for pynixd.store_path — StorePath and DrvOutput.

Tests str subclass behavior, property extraction, and wire format helpers.
All tests are pure — no I/O, no mocking.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pynixd.store_path import DrvOutput, StorePath
from tests.test_features import TestFeatures as F


@pytest.mark.covers(F.STORE_PATH_ENCODE)
class TestStorePathConstruction:
    def test_from_string(self):
        sp = StorePath("/nix/store/abc123-foo")
        assert str(sp) == "/nix/store/abc123-foo"

    def test_from_store_path(self):
        sp1 = StorePath("/nix/store/abc123-foo")
        sp2 = StorePath(sp1)
        assert str(sp2) == str(sp1)

    def test_from_store_path_preserves_extrainfo(self):
        sp1 = StorePath("/nix/store/abc123-foo", extrainfo="because")
        sp2 = StorePath(sp1)
        assert sp2.extrainfo == "because"


class TestStorePathProperties:
    def test_name(self):
        sp = StorePath("/nix/store/abc123-foo")
        assert sp.name == "abc123-foo"

    def test_hash_part(self):
        sp = StorePath("/nix/store/abc123-foo")
        assert sp.hash_part() == "abc123"

    def test_base_name(self):
        sp = StorePath("/nix/store/abc123-foo")
        assert sp.base_name() == "foo"

    def test_base_name_no_hash(self):
        sp = StorePath("/nix/store/justname")
        assert sp.base_name() == ""

    def test_is_derivation_true(self):
        sp = StorePath("/nix/store/abc123-foo.drv")
        assert sp.is_derivation()

    def test_is_derivation_false(self):
        sp = StorePath("/nix/store/abc123-foo")
        assert not sp.is_derivation()

    def test_to_path(self):

        sp = StorePath("/nix/store/abc123-foo")
        assert sp.to_path() == Path("/nix/store/abc123-foo")


class TestStorePathWithStorePrefix:
    def test_already_prefixed(self):
        sp = StorePath("/nix/store/abc123-foo")
        assert sp.with_store_prefix() is sp

    def test_bare_basename(self):
        sp = StorePath("abc123-foo")
        result = sp.with_store_prefix()
        assert str(result) == "/nix/store/abc123-foo"

    def test_bare_basename_preserves_extrainfo(self):
        sp = StorePath("abc123-foo", extrainfo="test")
        result = sp.with_store_prefix()
        assert result.extrainfo == "test"


class TestStorePathEquality:
    def test_equal(self):
        assert StorePath("/nix/store/a-foo") == StorePath("/nix/store/a-foo")

    def test_not_equal(self):
        assert StorePath("/nix/store/a-foo") != StorePath("/nix/store/b-bar")

    def test_hashable(self):
        s = {StorePath("/nix/store/a-foo"), StorePath("/nix/store/b-bar")}
        assert len(s) == 2


class TestStorePathRepr:
    def test_repr_no_extra(self):
        sp = StorePath("/nix/store/a-foo")
        assert "extrainfo" not in repr(sp)

    def test_repr_with_extra(self):
        sp = StorePath("/nix/store/a-foo", extrainfo="reason")
        assert "reason" in repr(sp)


class TestDrvOutputConstruction:
    def test_valid_format(self):
        do = DrvOutput("sha256:abc123def456!out")
        assert str(do) == "sha256:abc123def456!out"

    def test_invalid_no_bang(self):
        with pytest.raises(ValueError, match="Invalid DrvOutput"):
            DrvOutput("sha256:abc123def456")

    def test_empty_string(self):
        do = DrvOutput("")
        assert str(do) == ""

    def test_from_drv_output(self):
        do1 = DrvOutput("sha256:abc!out")
        do2 = DrvOutput(do1)  # type: ignore[arg-type]
        assert str(do2) == "sha256:abc!out"


class TestDrvOutputProperties:
    def test_id_hash(self):
        do = DrvOutput("sha256:abc!out")
        assert do.id_hash == "sha256:abc"

    def test_output_name(self):
        do = DrvOutput("sha256:abc!out")
        assert do.output_name == "out"

    def test_output_name_multiple_bangs(self):
        do = DrvOutput("sha256:a!b!c")
        assert do.output_name == "b!c"

    def test_repr(self):
        do = DrvOutput("sha256:abc!out")
        assert "DrvOutput" in repr(do)
