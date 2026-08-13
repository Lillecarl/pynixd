"""Unit tests for pynixd.derived_path — derived path parsing and serialization.

Tests the single-class DerivedPath with property-based dispatch,
OutputsSpec, parsing with both `^` and `!` separators, chain walking,
and all public properties/methods.
"""

from __future__ import annotations

import pytest

from pynixd.derived_path import (
    DerivedPath,
    OutputsAll,
    OutputsNames,
    parse_derived_path,
    parse_derived_path_legacy,
)
from pynixd.store_path import StorePath
from tests.test_features import TestFeatures as F


@pytest.mark.covers(F.DERIVED_PATH)
class TestOutputsSpec:
    def test_outputs_all_to_string(self):
        assert OutputsAll().to_string() == "*"

    def test_outputs_names_sorted(self):
        spec = OutputsNames(frozenset({"out", "lib", "dev"}))
        assert spec.to_string() == "dev,lib,out"

    def test_outputs_names_single(self):
        spec = OutputsNames(frozenset({"out"}))
        assert spec.to_string() == "out"


class TestDerivedPathOpaque:
    def test_opaque_path(self):
        dp = DerivedPath("/nix/store/abc-foo")
        assert dp.is_opaque is True
        assert dp.is_nested is False
        assert dp.outputs is None
        assert dp.drv_path == "/nix/store/abc-foo"
        assert dp.output_names == set()
        assert dp.base_store_path() == StorePath("/nix/store/abc-foo")
        assert dp.chain == ()

    def test_to_string_opaque(self):
        dp = DerivedPath("/nix/store/abc-foo")
        assert str(dp) == "/nix/store/abc-foo"
        assert dp.to_string() == "/nix/store/abc-foo"

    def test_isinstance_hierarchy(self):
        dp = DerivedPath("/nix/store/abc-foo")
        assert isinstance(dp, DerivedPath)
        assert not isinstance(dp, StorePath)
        assert not isinstance(dp, str)

    def test_bare_drv_normalized_to_built_all(self):
        dp = DerivedPath("/nix/store/abc.drv")
        assert dp.is_opaque is False
        assert isinstance(dp.outputs, OutputsAll)
        assert dp.output_names == {"*"}
        assert dp.drv_path == "/nix/store/abc.drv"

    def test_opaque_drv_has_star_outputs(self):
        """An opaque .drv path gets output_names = {'*'} (convenience)."""
        dp = parse_derived_path_legacy("/nix/store/abc.drv")
        assert dp.is_opaque is False
        assert dp.output_names == {"*"}


class TestDerivedPathBuilt:
    def test_built_with_single_output(self):
        dp = DerivedPath("/nix/store/abc.drv!out")
        assert dp.is_opaque is False
        assert dp.is_nested is False
        assert isinstance(dp.outputs, OutputsNames)
        assert dp.output_names == {"out"}
        assert dp.drv_path == "/nix/store/abc.drv"
        assert dp.base_store_path() == StorePath("/nix/store/abc.drv")
        assert dp.chain == ()

    def test_built_with_all_outputs(self):
        dp = DerivedPath("/nix/store/abc.drv!*")
        assert dp.is_opaque is False
        assert isinstance(dp.outputs, OutputsAll)
        assert dp.output_names == {"*"}
        assert dp.drv_path == "/nix/store/abc.drv"

    def test_built_with_specific_outputs(self):
        dp = DerivedPath("/nix/store/abc.drv!out,lib")
        assert dp.is_opaque is False
        assert isinstance(dp.outputs, OutputsNames)
        assert dp.output_names == {"out", "lib"}

    def test_to_string_built(self):
        dp = DerivedPath("/nix/store/abc.drv!out")
        assert str(dp) == "/nix/store/abc.drv!out"
        assert "out" in dp.to_string()

    def test_built_to_string_roundtrip(self):
        s = "/nix/store/abc.drv!out"
        dp = DerivedPath(s)
        assert str(dp) == s


class TestDerivedPathNested:
    def test_nested_single_chain(self):
        dp = DerivedPath("/nix/store/a.drv!out!lib")
        assert dp.is_opaque is False
        assert dp.is_nested is True
        assert dp.chain == ("out",)
        assert dp.drv_path == "/nix/store/a.drv"
        assert isinstance(dp.outputs, OutputsNames)
        assert dp.output_names == {"lib"}

    def test_nested_to_string(self):
        dp = DerivedPath("/nix/store/a.drv!out!lib")
        assert dp.to_string() == "/nix/store/a.drv^out^lib"
        assert str(dp) == "/nix/store/a.drv!out!lib"

    def test_outer_peels_one_level(self):
        dp = DerivedPath("/nix/store/a.drv!out!lib")
        outer = dp.outer
        assert outer.is_nested is False
        assert outer.drv_path == "/nix/store/a.drv"
        assert outer.output_names == {"out"}

    def test_outer_on_non_nested_returns_self(self):
        dp = DerivedPath("/nix/store/abc.drv!out")
        assert dp.outer is dp

    def test_wrap_replaces_root(self):
        dp = DerivedPath("/nix/store/a.drv!out!lib")
        inner_drv = StorePath("/nix/store/xxx-inner.drv")
        next_dp = dp.wrap(inner_drv)
        assert next_dp.drv_path == "/nix/store/xxx-inner.drv"
        assert next_dp.is_nested is False
        assert next_dp.output_names == {"lib"}

    def test_nested_decomposition(self):
        """Walk a nested path step by step."""
        dp = DerivedPath("/nix/store/a.drv!out!lib")

        # Step 1: build outer
        step1 = dp.outer
        assert str(step1) == "/nix/store/a.drv!out"

        # After building step1 we get an intermediate .drv
        # Step 2: wrap with the final outputs
        inner = StorePath("/nix/store/xxx.drv")
        step2 = dp.wrap(inner)
        assert str(step2) == "/nix/store/xxx.drv!lib"


class TestParseDerivedPath:
    def test_opaque_path(self):
        dp = parse_derived_path("/nix/store/abc-foo")
        assert dp.is_opaque is True
        assert dp.drv_path == "/nix/store/abc-foo"

    def test_bare_drv_normalized_to_built_all(self):
        dp = parse_derived_path("/nix/store/abc.drv")
        assert dp.is_opaque is False
        assert isinstance(dp.outputs, OutputsAll)

    def test_built_with_output(self):
        dp = parse_derived_path("/nix/store/abc.drv^out")
        assert dp.is_opaque is False
        assert isinstance(dp.outputs, OutputsNames)
        assert dp.output_names == {"out"}

    def test_nested_built(self):
        dp = parse_derived_path("/nix/store/abc.drv^out^lib")
        assert dp.is_opaque is False
        assert dp.is_nested is True
        assert dp.chain == ("out",)

    def test_multiple_outputs(self):
        dp = parse_derived_path("/nix/store/abc.drv^out,lib")
        assert dp.is_opaque is False
        assert isinstance(dp.outputs, OutputsNames)
        assert dp.output_names == {"out", "lib"}


class TestParseDerivedPathLegacy:
    def test_legacy_built_with_output(self):
        dp = parse_derived_path_legacy("/nix/store/abc.drv!out")
        assert dp.is_opaque is False
        assert isinstance(dp.outputs, OutputsNames)
        assert dp.output_names == {"out"}

    def test_legacy_bare_drv(self):
        dp = parse_derived_path_legacy("/nix/store/abc.drv")
        assert dp.is_opaque is False
        assert isinstance(dp.outputs, OutputsAll)


class TestHelperAccessors:
    def test_drv_path_opaque(self):
        dp = DerivedPath("/nix/store/abc-foo")
        assert dp.drv_path == "/nix/store/abc-foo"

    def test_drv_path_built(self):
        dp = DerivedPath("/nix/store/abc.drv!out")
        assert dp.drv_path == "/nix/store/abc.drv"

    def test_output_names_opaque(self):
        dp = DerivedPath("/nix/store/abc-foo")
        assert dp.output_names == set()

    def test_output_names_drv(self):
        dp = DerivedPath("/nix/store/abc.drv")
        assert dp.output_names == {"*"}

    def test_output_names_built_all(self):
        dp = DerivedPath("/nix/store/abc.drv!*")
        assert dp.output_names == {"*"}

    def test_output_names_built_specific(self):
        dp = DerivedPath("/nix/store/abc.drv!out")
        assert dp.output_names == {"out"}

    def test_is_nested_false_opaque(self):
        dp = DerivedPath("/nix/store/abc-foo")
        assert dp.is_nested is False

    def test_is_nested_false_simple_built(self):
        dp = DerivedPath("/nix/store/abc.drv!out")
        assert dp.is_nested is False

    def test_is_nested_true(self):
        dp = DerivedPath("/nix/store/a.drv!out!lib")
        assert dp.is_nested is True


class TestDerivedPathStrSubclass:
    def test_construction_from_opaque(self):
        dp = DerivedPath("/nix/store/abc-foo")
        assert dp.derived is dp
        assert dp.drv_path == "/nix/store/abc-foo"
        assert dp.output_names == set()

    def test_construction_from_built_legacy(self):
        dp = DerivedPath("/nix/store/abc.drv!out")
        assert dp.derived is dp
        assert dp.drv_path == "/nix/store/abc.drv"
        assert dp.output_names == {"out"}

    def test_construction_from_bare_drv(self):
        dp = DerivedPath("/nix/store/abc.drv")
        assert dp.derived is dp
        assert dp.drv_path == "/nix/store/abc.drv"
        assert dp.output_names == {"*"}

    def test_is_nested(self):
        dp = DerivedPath("/nix/store/a.drv!out!lib")
        assert dp.is_nested is True


class TestDunderMethods:
    def test_str_is_wire_format(self):
        dp = DerivedPath("/nix/store/abc.drv!out")
        assert str(dp) == "/nix/store/abc.drv!out"

    def test_repr(self):
        dp = DerivedPath("/nix/store/abc.drv!out")
        assert repr(dp) == "DerivedPath('/nix/store/abc.drv!out')"

    def test_format(self):
        dp = DerivedPath("/nix/store/abc.drv!out")
        assert f"{dp}" == "/nix/store/abc.drv!out"
        assert f"{dp:>40}" == f"{'/nix/store/abc.drv!out':>40}"

    def test_equality(self):
        a = DerivedPath("/nix/store/abc.drv!out")
        b = DerivedPath("/nix/store/abc.drv!out")
        c = DerivedPath("/nix/store/abc.drv!lib")
        assert a == b
        assert a != c

    def test_hash(self):
        a = DerivedPath("/nix/store/abc.drv!out")
        b = DerivedPath("/nix/store/abc.drv!out")
        assert hash(a) == hash(b)
        assert len({a, b}) == 1
