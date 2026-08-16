"""Unit tests for pynixd.drv_parser — ATerm .drv file parser.

Tests are split into two categories:
1. **Live probes**: Evaluate ``tests/nix/drv-probes.nix`` once per session via
   ``nix eval``, read the real .drv files, and validate parsing + to_json().
2. **Manufactured edge cases**: Static inline strings for parser features that
   are hard to produce via nix (escaped strings, empty env, deferred outputs, etc).
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

import anyio
import pytest

from pynixd.drv_parser import Derivation, parse_drv, to_basic_derivation
from pynixd.serde import OutputKind, StorePath as SerdeStorePath
from pynixd.store_path import DrvOutput, StorePath
from tests.conftest import NIX_BIN
from tests.test_features import TestFeatures as F

if TYPE_CHECKING:
    from pynixd.serde.aliases import OutputMap

_PROBES_NIX = Path(__file__).parent.parent.parent / "tests" / "nix" / "drv-probes.nix"


# ── Session-scoped fixture: evaluate drv-probes.nix once ───────────────────


@pytest.fixture(scope="session")
def drv_probes_path() -> Path:
    return _PROBES_NIX


@pytest.fixture(scope="session")
def probes(drv_probes_path: Path) -> dict[str, tuple[str, str, dict[str, Any]]]:
    """Evaluate all entries in drv-probes.nix.

    Returns {name: (drv_path, drv_content, canonical_derivation_show)}.
    Evaluated once per test session.
    """

    # `checks.pynixd` runs this suite in a build sandbox, which holds no Nix
    # binary, no store daemon and no network. Every test that reads a real
    # `.drv` therefore skips there, and the manufactured edge cases below
    # still run. Without this, 21 tests errored with `FileNotFoundError`.
    if shutil.which(str(NIX_BIN)) is None:
        pytest.skip(f"{NIX_BIN} is not on PATH, so no probe can be evaluated")

    async def _eval_all():
        # List attribute names using --apply
        proc = await asyncio.create_subprocess_exec(
            NIX_BIN,
            "eval",
            "--impure",
            "--file",
            str(drv_probes_path),
            "--apply",
            "builtins.attrNames",
            "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"Failed to list probes: {stderr.decode()}")
        names = json.loads(stdout.decode())

        result = {}
        for name in names:
            proc = await asyncio.create_subprocess_exec(
                NIX_BIN,
                "eval",
                "--impure",
                "--file",
                str(drv_probes_path),
                f"{name}.drvPath",
                "--raw",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(f"eval {name}.drvPath failed: {stderr.decode()}")
            drv_path = stdout.decode().strip()
            drv_content = await anyio.Path(drv_path).read_text()

            proc = await asyncio.create_subprocess_exec(
                NIX_BIN,
                "derivation",
                "show",
                drv_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(f"derivation show {name} failed: {stderr.decode()}")
            canonical = json.loads(stdout.decode())

            result[name] = (drv_path, drv_content, canonical)
        return result

    return asyncio.run(_eval_all())


# ── Helpers for extracting data from canonical JSON ────────────────────────


def _canonical_entry(canonical: dict[str, Any]) -> dict[str, Any]:
    """Get the single derivation entry from ``nix derivation show`` output.

    Handles both ``{"derivations": {...}, "version": 4}`` and the flat format.
    """
    d = canonical.get("derivations", canonical)
    return next(iter(d.values()))


def _canonical_drv_key(canonical: dict[str, Any]) -> str:
    d = canonical.get("derivations", canonical)
    return next(iter(d))


# ── Live probes tests ──────────────────────────────────────────────────────


@pytest.mark.covers(F.DRV_PARSE | F.DRV_SERIALIZE)
class TestLiveProbes:
    """Tests using real derivations from tests/nix/drv-probes.nix.

    Each test parses the .drv file and validates against
    ``nix derivation show`` canonical JSON.
    """

    def test_simple_env(self, probes):
        _, drv_content, canonical = probes["simple"]
        parsed = parse_drv(drv_content)
        result = parsed.to_json(_canonical_drv_key(canonical))
        entry = result[_canonical_drv_key(canonical)]
        assert entry["env"] == _canonical_entry(canonical)["env"]

    def test_simple_metadata(self, probes):
        _, drv_content, canonical = probes["simple"]
        parsed = parse_drv(drv_content)
        entry = _canonical_entry(canonical)
        assert parsed.platform == entry["system"]
        assert parsed.builder == entry["builder"]

    def test_simple_output_kind(self, probes):
        _, drv_content, _ = probes["simple"]
        parsed = parse_drv(drv_content)
        assert parsed.output_kinds() == [OutputKind.INPUT_ADDRESSED]

    def test_ca_floating_env(self, probes):
        _, drv_content, canonical = probes["ca-floating"]
        parsed = parse_drv(drv_content)
        result = parsed.to_json(_canonical_drv_key(canonical))
        assert result[_canonical_drv_key(canonical)]["env"] == _canonical_entry(canonical)["env"]

    def test_ca_floating_output_kind(self, probes):
        _, drv_content, _ = probes["ca-floating"]
        parsed = parse_drv(drv_content)
        assert parsed.output_kinds() == [OutputKind.CA_FLOATING]

    def test_ca_floating_no_output_path(self, probes):
        _, drv_content, _ = probes["ca-floating"]
        parsed = parse_drv(drv_content)
        assert parsed.outputs[0].path == ""

    def test_ca_fixed_env(self, probes):
        _, drv_content, canonical = probes["ca-fixed"]
        parsed = parse_drv(drv_content)
        result = parsed.to_json(_canonical_drv_key(canonical))
        assert result[_canonical_drv_key(canonical)]["env"] == _canonical_entry(canonical)["env"]

    def test_ca_fixed_output_kind(self, probes):
        _, drv_content, _ = probes["ca-fixed"]
        parsed = parse_drv(drv_content)
        # Fixed CA: path + hash_algo + hash_value all set
        assert parsed.output_kinds() == [OutputKind.CA_FIXED]

    def test_text_hashed_env(self, probes):
        _, drv_content, canonical = probes["text-hashed"]
        parsed = parse_drv(drv_content)
        result = parsed.to_json(_canonical_drv_key(canonical))
        assert result[_canonical_drv_key(canonical)]["env"] == _canonical_entry(canonical)["env"]

    def test_dynamic_env(self, probes):
        _, drv_content, canonical = probes["dynamic"]
        parsed = parse_drv(drv_content)
        result = parsed.to_json(_canonical_drv_key(canonical))
        assert result[_canonical_drv_key(canonical)]["env"] == _canonical_entry(canonical)["env"]

    def test_dynamic_has_dynamic_flag(self, probes):
        _, drv_content, _ = probes["dynamic"]
        parsed = parse_drv(drv_content)
        assert parsed.env["__dynamicDerivation"] == "1"
        assert parsed.required_system_features == {"recursive-nix"}

    def test_with_features(self, probes):
        _, drv_content, _ = probes["with-features"]
        parsed = parse_drv(drv_content)
        assert parsed.required_system_features == {"kvm", "big-parallel"}

    def test_multiple_input_drvs(self, probes):
        """Most probes have multiple input derivations (stdenv + bash)."""
        _, drv_content, _ = probes["simple"]
        parsed = parse_drv(drv_content)
        assert len(parsed.input_drvs) == 2
        # runCommand includes source-stdenv.sh as input srcs
        assert len(parsed.input_srcs) == 2

    def test_ca_has_input_srcs(self, probes):
        """CA derivations include source-stdenv.sh as input sources."""
        _, drv_content, _ = probes["ca-floating"]
        parsed = parse_drv(drv_content)
        assert len(parsed.input_srcs) == 2  # source-stdenv.sh + default-builder.sh

    def test_minimal(self, probes):
        """stdenvNoCC.mkDerivation — minimal build environment."""
        _, drv_content, canonical = probes["minimal"]
        parsed = parse_drv(drv_content)
        result = parsed.to_json(_canonical_drv_key(canonical))
        assert result[_canonical_drv_key(canonical)]["env"] == _canonical_entry(canonical)["env"]
        assert parsed.platform == "x86_64-linux"

    def test_to_json_matches_canonical(self, probes):
        """For every probe, verify to_json() produces the same env as nix derivation show."""
        for name, (_, drv_content, canonical) in probes.items():
            parsed = parse_drv(drv_content)
            result = parsed.to_json(_canonical_drv_key(canonical))
            entry = _canonical_entry(canonical)
            # We compare env dicts — should be identical
            assert result[_canonical_drv_key(canonical)]["env"] == entry["env"], f"env mismatch for {name}"


# ── Manufactured edge cases ────────────────────────────────────────────────


@pytest.mark.covers(F.DRV_PARSE)
class TestManufacturedExamples:
    """Tests using static inline strings for parser features hard to produce via nix."""

    def test_deferred(self):
        parsed = parse_drv('Derive([("out","","","")],[],[],"x86_64-linux","/bin/sh",["-c","true"],[("name","def")])')
        assert parsed.output_kinds() == [OutputKind.DEFERRED]

    def test_empty_env(self):
        parsed = parse_drv('Derive([("out","/nix/store/abc-foo","","")],[],[],"x86_64-linux","/bin/sh",[],[])')
        assert parsed.env == {}

    def test_escaped_strings(self):
        parsed = parse_drv(
            'Derive([("out","/nix/store/abc-foo","","")]'
            ',[],[],"x86_64-linux"'
            ',"/bin/sh",["-c","echo \\"hello\\""]'
            ',[("name","esc"),("desc","line1\\nline2\\ttab")])'
        )
        assert parsed.args == ["-c", 'echo "hello"']
        assert parsed.env["desc"] == "line1\nline2\ttab"

    def test_multiple_outputs(self):
        parsed = parse_drv(
            'Derive([("out","/nix/store/a-out","",""),("lib","/nix/store/a-lib","","")]'
            ',[],[],"x86_64-linux"'
            ',"/bin/sh",["-c","make"]'
            ',[("name","multi"),("outputs","out lib")])'
        )
        assert len(parsed.outputs) == 2

    def test_required_system_features(self):
        parsed = parse_drv(
            'Derive([("out","/nix/store/a-foo","","")]'
            ',[],[],"x86_64-linux"'
            ',"/bin/sh",["-c","true"]'
            ',[("name","feat"),("requiredSystemFeatures","kvm big-parallel")])'
        )
        assert parsed.required_system_features == {"kvm", "big-parallel"}


@pytest.mark.covers(F.DRV_PARSE | F.DYN_CHILD_MAP)
class TestDrvWithVersion:
    """Tests for the DrvWithVersion ATerm format.

    This format is only produced by Nix >= 2.18 for true dynamic derivations.
    The example below is manufactured based on the format spec.
    """

    def test_dynamic_format(self):
        parsed = parse_drv(
            'DrvWithVersion("xp-dyn-drv",'
            '[("out","","text:sha256","")]'
            ',[("/nix/store/dep.drv",([],[])),'
            '("/nix/store/simple.drv",["out"])]'
            ",[]"
            ',"x86_64-linux"'
            ',"/bin/sh",["-c","true"]'
            ',[("name","dyn")])'
        )
        assert parsed.is_dynamic
        assert StorePath("/nix/store/dep.drv") in parsed.dynamic_input_drvs
        assert StorePath("/nix/store/simple.drv") in parsed.input_drvs
        assert parsed.input_drvs[StorePath("/nix/store/simple.drv")] == ["out"]
        from pynixd.drv_parser import ChildMapNode

        assert parsed.dynamic_input_drvs[StorePath("/nix/store/dep.drv")] == ChildMapNode()


@pytest.mark.covers(F.DRV_PARSE | F.DRV_HASH_DERIVATION_MODULO | F.DRV_COMPUTE_STOREPATH)
class TestDerivationProperties:
    """Tests for Derivation property methods with explicit data."""

    def test_required_system_features_empty(self):
        assert Derivation(env={}).required_system_features == set()

    def test_output_paths(self):
        parsed = Derivation(
            outputs=[
                DrvOutput(output_name="out", path="/nix/store/abc-foo", hash_algo="", hash_value=""),
            ],
        )
        assert parsed.output_paths() == {"out": StorePath("/nix/store/abc-foo")}

    def test_output_kinds_mixed(self):
        parsed = Derivation(
            outputs=[
                DrvOutput(output_name="ia", path="/nix/store/a", hash_algo="", hash_value=""),
                DrvOutput(output_name="ca", path="", hash_algo="sha256", hash_value="xyz"),
                DrvOutput(output_name="flt", path="", hash_algo="sha256", hash_value=""),
            ],
        )
        assert parsed.output_kinds() == [
            OutputKind.INPUT_ADDRESSED,
            OutputKind.CA_FIXED,
            OutputKind.CA_FLOATING,
        ]

    def test_name(self, probes):
        """Name should be derived from the store path (hash-name.drv)."""
        for name, (drv_path, drv_content, _) in probes.items():
            parsed = parse_drv(drv_content)
            # parsed.name is derived from to_json; verify drv_path contains the expected name
            assert parsed.env.get("name") in drv_path, f"{name}: expected {parsed.env.get('name')} in {drv_path}"


@pytest.mark.covers(F.DRV_PARSE)
class TestParseDrvEdgeCases:
    def test_invalid_syntax(self):
        with pytest.raises(ValueError):  # noqa: PT011
            parse_drv("Derive(invalid")

    def test_unterminated_string(self):
        with pytest.raises(ValueError, match="Unterminated string"):
            parse_drv('Derive([("out","abc')

    def test_unknown_version(self):
        with pytest.raises(ValueError, match="Unknown derivation ATerm version"):
            parse_drv('DrvWithVersion("unknown-version",[]')

    def test_empty_text(self):
        with pytest.raises(ValueError):  # noqa: PT011
            parse_drv("")


@pytest.mark.covers(F.DRV_PARSE | F.DRV_SERIALIZE)
class TestToBasicDerivation:
    """to_basic_derivation with mocked output_cache avoids disk I/O."""

    async def test_simple_conversion(self, probes):

        _, drv_content, _ = probes["simple"]
        parsed = parse_drv(drv_content)
        result = await to_basic_derivation(parsed, Path("/tmp/fake-store"))
        assert len(result.outputs) == 1
        assert result.platform == "x86_64-linux"

    async def test_with_output_cache(self, probes):

        _, drv_content, _ = probes["simple"]
        parsed = parse_drv(drv_content)
        drv = next(iter(parsed.input_drvs.keys()))
        out_name = parsed.input_drvs[drv][0]
        cache: OutputMap = {drv: {out_name: StorePath(f"/nix/store/realized-{out_name}")}}
        result = await to_basic_derivation(parsed, Path("/tmp/fake-store"), output_cache=cache)
        assert SerdeStorePath(path=f"/nix/store/realized-{out_name}") in result.input_srcs

    async def test_cache_missing_adds_drv(self, probes):

        _, drv_content, _ = probes["simple"]
        parsed = parse_drv(drv_content)
        result = await to_basic_derivation(parsed, Path("/tmp/fake-store"))
        for drv_path in parsed.input_drvs:
            assert SerdeStorePath(path=str(drv_path)) in result.input_srcs


@pytest.mark.covers(F.DRV_PARSE)
class TestDrvOutputFields:
    def test_fields(self):
        o = DrvOutput(hash_algo="sha256", hash_value="xyz", output_name="out", path="/nix/store/a")
        assert o.hash_algo == "sha256"
        assert o.hash_value == "xyz"
        assert o.output_name == "out"
        assert o.path == "/nix/store/a"
        assert o.name == "out"


@pytest.mark.covers(F.DRV_PARSE | F.DRV_SERIALIZE)
class TestToJson:
    def test_serializable(self, probes):
        _, drv_content, _ = probes["simple"]
        parsed = parse_drv(drv_content)
        json.dumps(parsed.to_json("/nix/store/test.drv"))

    def test_ca_no_path(self, probes):
        _, drv_content, _ = probes["ca-floating"]
        parsed = parse_drv(drv_content)
        result = parsed.to_json("/nix/store/ca.drv")
        out_entry = result["/nix/store/ca.drv"]["outputs"]["out"]
        assert "path" not in out_entry


@pytest.mark.covers(F.DRV_PARSE | F.DRV_SERIALIZE)
class TestSerialize:
    """Tests for Derivation.serialize() roundtrips."""

    def test_traditional_roundtrip(self):
        text = (
            'Derive([("out","/nix/store/result","",""),'
            '("dev","/nix/store/dev","","")],'
            '[("/nix/store/dep.drv",["out"])],'
            '["/nix/store/src"],'
            '"x86_64-linux","/bin/bash",["-c","true"],'
            '[("name","test"),("key","value")])'
        )
        parsed = parse_drv(text)
        serialized = parsed.serialize()
        reparsed = parse_drv(serialized)
        assert reparsed.serialize() == serialized

    def test_dynamic_roundtrip(self):
        text = (
            'DrvWithVersion("xp-dyn-drv",'
            '[("out","/nix/store/result","","")],'
            '[("/nix/store/dep.drv",([],[])),'
            '("/nix/store/simple.drv",["out"])],'
            '[],"x86_64-linux","/bin/sh",["-c","true"],'
            '[("name","dyn")])'
        )
        parsed = parse_drv(text)
        serialized = parsed.serialize()
        reparsed = parse_drv(serialized)
        assert reparsed.serialize() == serialized

    def test_empty_derivation(self):
        text = 'Derive([],[],[],"x86_64-linux","/bin/sh",[],[])'
        parsed = parse_drv(text)
        assert parsed.serialize() == text

    def test_escaping(self):
        parsed = Derivation(
            outputs=[DrvOutput(output_name="out", path="", hash_algo="", hash_value="")],
            input_drvs={},
            input_srcs=set(),
            platform="x86_64-linux",
            builder='/bin/sh -c "echo \\"hello\\""',
            args=["-c", 'echo "hello"'],
            env={"key": "value\nwith\ttabs"},
        )
        serialized = parsed.serialize()
        reparsed = parse_drv(serialized)
        assert reparsed.builder == parsed.builder
        assert reparsed.args == parsed.args
        assert reparsed.env == parsed.env

    def test_live_probe_roundtrip(self, probes):
        """Every live probe must roundtrip idempotently."""
        for name, drv_content, _ in probes.values():
            parsed = parse_drv(drv_content)
            serialized = parsed.serialize()
            reparsed = parse_drv(serialized)
            assert reparsed.serialize() == serialized, f"roundtrip failed for {name}"
