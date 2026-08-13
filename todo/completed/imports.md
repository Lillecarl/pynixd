# Non-Toplevel (Lazy) Imports Analysis

**Goal:** Identify all imports placed inside function/method bodies rather than at module top level.

**Standard:** Per AGENTS.md, `from __future__ import annotations` is required, and all non-TYPE_CHECKING imports should be at the top of the file. Lazy imports inside function bodies are only acceptable to break circular import cycles.

---

## Production Code (`pynixd/`)

**Result: CLEAN** — Zero lazy imports found. All imports are module-level or inside `if TYPE_CHECKING:` blocks. Good.

---

## Test Code (`tests/`)

Every lazy import below is in test code, where the pattern is conventionally acceptable (avoids importing modules until a specific test is hit), but could still be cleaned up for consistency.

### `tests/unit/test_drv_parser.py`

| Line | Import | Context |
|------|--------|---------|
| 47 | `import asyncio` | Inside `probes()` fixture |
| 361 | `from pynixd.drv_parser import to_basic_derivation` | Inside `test_simple_conversion` |
| 370 | `from pynixd.drv_parser import to_basic_derivation` | Inside `test_with_output_cache` |
| 381 | `from pynixd.drv_parser import to_basic_derivation` | Inside `test_cache_missing_adds_drv` |

Note: `to_basic_derivation` is used in all three tests — could be top-level. The `import asyncio` inside a synchronous `@pytest.fixture(scope="session")` is likely to avoid event loop issues with `asyncio.create_subprocess_exec`.

### `tests/unit/test_signing.py`

| Line | Import | Context |
|------|--------|---------|
| 117 | `from pynixd.operations.base import ValidPathInfo` | Inside `test_sign_path_info_roundtrip` |
| 140 | `from pynixd.signing import get_default_signing_key` | Inside `test_no_env_var` (after monkeypatch) |
| 147 | `from pynixd.signing import get_default_signing_key` | Inside `test_with_env_var` (after monkeypatch) |

Lines 140/147: Intentionally lazy — they must be imported **after** `monkeypatch.setenv`/`delenv` so the module-level singleton (`_DEFAULT_SIGNING_KEY`) is built with the right env.

### `tests/unit/test_store_path.py`

| Line | Import | Context |
|------|--------|---------|
| 56 | `from pathlib import Path` | Inside `test_to_path` |

Trivial — `Path` is used in a single test assertion.

### `tests/unit/test_utils.py`

| Line | Import | Context |
|------|--------|---------|
| 24 | `import hashlib` | Inside `test_known_nix_hash` |
| 66 | `import hashlib` | Inside `test_roundtrip_sha256` |

Both can be lifted to the top level.

### `tests/unit/test_derived_path.py`

| Line | Import | Context |
|------|--------|---------|
| 84 | `from pynixd.derived_path import dp_is_nested` | Inside `test_built_nested` |

### `tests/unit/test_types_derivation.py`

| Line | Import | Context |
|------|--------|---------|
| 222 | `from pynixd.system_features import PYNIXD_HANDLED_FEATURES` | Inside `test_effective_required_features` |

### `tests/unit/test_wire.py`

**Pattern:** Every operation serialization test method has a lazy import of its request/response class. This is intentional — ~30+ operation types, only the one being tested is imported.

| Line | Import |
|------|--------|
| 337 | `from pynixd.stderr import StderrNext` |
| 353 | `from pynixd.stderr import StderrNext, StderrStartActivity` |
| 401 | `from pynixd.operations.build_derivation import BuildDerivationRequest` |
| 421 | `from pynixd.operations.build_derivation import BuildDerivationResponse` |
| 437 | `from pynixd.operations.query_path_info import QueryPathInfoRequest` |
| 444 | `from pynixd.operations.query_path_info import QueryPathInfoResponse` |
| 460 | `from pynixd.operations.is_valid_path import IsValidPathRequest` |
| 467 | `from pynixd.operations.is_valid_path import IsValidPathResponse` |
| 483 | `from pynixd.operations.query_valid_paths import QueryValidPathsRequest` |
| 493-494 | `from pynixd.constants import proto` / `from pynixd.operations.query_valid_paths import QueryValidPathsRequest` |
| 506 | `from pynixd.operations.query_valid_paths import QueryValidPathsResponse` |
| 519 | `from pynixd.operations.add_signatures import AddSignaturesRequest` |
| 530 | `from pynixd.operations.add_signatures import AddSignaturesResponse` |
| 546 | `from pynixd.operations.query_referrers import QueryReferrersRequest` |
| 553 | `from pynixd.operations.query_referrers import QueryReferrersResponse` |
| 566 | `from pynixd.operations.ensure_path import EnsurePathRequest` |
| 573 | `from pynixd.operations.ensure_path import EnsurePathResponse` |
| 585 | `from pynixd.operations.add_temp_root import AddTempRootRequest` |
| 592 | `from pynixd.operations.add_temp_root import AddTempRootResponse` |
| 604 | `from pynixd.operations.add_indirect_root import AddIndirectRootRequest` |
| 611 | `from pynixd.operations.add_indirect_root import AddIndirectRootResponse` |
| 623 | `from pynixd.operations.add_perm_root import AddPermRootRequest` |
| 631 | `from pynixd.operations.add_perm_root import AddPermRootResponse` |
| 643 | `from pynixd.operations.find_roots import FindRootsRequest` |
| 650 | `from pynixd.operations.find_roots import FindRootsEntry, FindRootsResponse` |
| 666 | `from pynixd.operations.query_path_from_hash_part import QueryPathFromHashPartRequest` |
| 673 | `from pynixd.operations.query_path_from_hash_part import QueryPathFromHashPartResponse` |
| 685 | `from pynixd.operations.query_substitutable_paths import QuerySubstitutablePathsRequest` |
| 693 | `from pynixd.operations.query_substitutable_paths import QuerySubstitutablePathsResponse` |
| 706 | `from pynixd.operations.query_valid_derivers import QueryValidDeriversRequest` |
| 713 | `from pynixd.operations.query_valid_derivers import QueryValidDeriversResponse` |
| 726 | `from pynixd.operations.set_options import SetOptionsRequest` |
| 743 | `from pynixd.operations.set_options import SetOptionsResponse` |
| 751-752 | `from pynixd.constants import proto` / `from pynixd.operations.set_options import SetOptionsRequest` |
| 769 | `from pynixd.operations.collect_garbage import CollectGarbageRequest` |
| 783 | `from pynixd.operations.collect_garbage import CollectGarbageResponse` |
| 799 | `from pynixd.operations.query_all_valid_paths import QueryAllValidPathsRequest` |
| 806 | `from pynixd.operations.query_all_valid_paths import QueryAllValidPathsResponse` |
| 819-821 | `from pynixd.operations.query_derivation_output_map import QueryDerivationOutputMapRequest` |
| 828-830 | `from pynixd.operations.query_derivation_output_map import QueryDerivationOutputMapResponse` |
| 845 | `from pynixd.operations.query_missing import QueryMissingRequest` |
| 856 | `from pynixd.operations.query_missing import QueryMissingResponse` |
| 875 | `from pynixd.operations.optimise_store import OptimiseStoreRequest` |
| 882 | `from pynixd.operations.optimise_store import OptimiseStoreResponse` |
| 894 | `from pynixd.operations.verify_store import VerifyStoreRequest` |
| 902 | `from pynixd.operations.verify_store import VerifyStoreResponse` |
| 914 | `from pynixd.operations.add_build_log import AddBuildLogRequest` |
| 921 | `from pynixd.operations.add_build_log import AddBuildLogResponse` |
| 933 | `from pynixd.operations.build_paths import BuildPathsRequest` |
| 944 | `from pynixd.operations.build_paths import BuildPathsResponse` |
| 953 | `from pynixd.operations.build_paths import BuildPathsWithResultsRequest` |
| 964-965 | `from pynixd.operations.build_paths import BuildPathsWithResultsResponse` / `from pynixd.types import KeyedBuildResult` |
| 981-983 | `from pynixd.operations.query_subst_path_info import QuerySubstitutablePathInfoRequest` |
| 990-993 | `from pynixd.operations.query_subst_path_info import QuerySubstitutablePathInfoResponse` + `from pynixd.types.path_info import SubstitutablePathInfo` |
| 1011-1013 | `from pynixd.operations.query_subst_path_info import QuerySubstitutablePathInfoResponse` |
| 1026-1028 | `from pynixd.operations.query_subst_path_infos import QuerySubstitutablePathInfosRequest` |
| 1035-1039 | `from pynixd.operations.query_subst_path_infos import QuerySubstitutablePathInfosResponse` + `from pynixd.types.path_info import SubstitutablePathInfo` |
| 1066 | `from pynixd.operations.ca_derivations import QueryRealisationRequest` |
| 1075 | `from pynixd.operations.ca_derivations import QueryRealisationResponse` |
| 1086 | `from pynixd.operations.ca_derivations import RegisterDrvOutputRequest` |
| 1095 | `from pynixd.operations.ca_derivations import RegisterDrvOutputResponse` |
| 1108 | `from pynixd.operations.nar_from_path import NarFromPathRequest` |
| 1117 | `from pynixd.operations.add_to_store_nar import AddToStoreNarRequest` |
| 1126 | `from pynixd.operations.add_to_store_nar import AddToStoreNarResponse` |
| 1134 | `from pynixd.operations.add_multiple_to_store import AddMultipleToStoreRequest` |
| 1141 | `from pynixd.operations.add_multiple_to_store import AddMultipleToStoreResponse` |
| 1152 | `from pynixd.operations.sign_path_info import SignPathInfoRequest` |
| 1160 | `from pynixd.operations.sign_path_info import SignPathInfoResponse` |
| 1173 | `from pynixd.operations.add_to_store import AddToStoreRequest` |
| 1185 | `from pynixd.operations.add_to_store import AddToStoreResponse` |
| 1202 | `from pynixd.operations.query_path_infos import QueryPathInfosRequest` |
| 1210 | `from pynixd.operations.query_path_infos import QueryPathInfosResponse` |
| 1223 | `from pynixd.operations.query_closure import QueryClosureRequest` |
| 1231 | `from pynixd.operations.query_closure import QueryClosureResponse` |
| 1243 | `from pynixd.operations.query_closure_with_info import QueryClosureWithInfoRequest` |
| 1251 | `from pynixd.operations.query_closure_with_info import QueryClosureWithInfoResponse` |
| 1264-1266 | `from pynixd.operations.query_derivation_output_map_batch import QueryDerivationOutputMapBatchRequest` |
| 1274-1276 | `from pynixd.operations.query_derivation_output_map_batch import DerivationOutputMapBatchResponse` |

**Intentional pattern**: ~75 lazy imports in test_wire.py, one per operation test method. This is deliberate — avoids importing all 30+ operation types at module load. Keeping these lazy is reasonable.

### `tests/functional/mock_store.py`

| Line | Import | Context |
|------|--------|---------|
| 142 | `from pynixd.psi import CpuUtil` | Inside `cpu_util` property |
| 194 | `from pynixd.types.path_info import ValidPathInfo` | Inside `execute()` method |

These avoid circular imports — `mock_store.py` imports from `pynixd.operations.base` which may transitively touch modules that also import MockStore.

### `tests/benchmark/test_bench_nar.py`

| Line | Import | Context |
|------|--------|---------|
| 120 | `from tests.conftest import run_subproc` | Inside `_add_file()` |

### `tests/benchmark/test_bench_pynixd.py`

| Line | Import | Context |
|------|--------|---------|
| 122 | `from tests.conftest import run_subproc` | Inside `test_bench_local_socket_overhead` |

### `tests/ai/deferred_resolve.py`

| Line | Import | Context |
|------|--------|---------|
| 608-610 | `from pynixd.operations.query_derivation_output_map import QueryDerivationOutputMapRequest as QdomRequest` | Inside success-check block after BuildDerivation |

---

## Summary

| Directory | Lazy Imports | Notes |
|-----------|-------------|-------|
| `pynixd/` | **0** | Clean — all imports toplevel or TYPE_CHECKING |
| `tests/unit/test_wire.py` | ~75 | Deliberate per-op pattern |
| `tests/unit/` (other) | ~8 | Minor cleanup candidates |
| `tests/functional/mock_store.py` | 2 | Circular import workaround |
| `tests/benchmark/` | 2 | Trivial |
| `tests/ai/` | 1 | Trivial |

**Recommendation:** Production code is clean. The test `test_wire.py` lazy imports are an established pattern (avoids importing all ~30 ops at module load). The remaining ~8 in other unit test files could be promoted to top-level for consistency with minimal risk — except the `test_signing.py` ones which depend on monkeypatch timing.

---

## `cast("Type", value)` Stringified Casts (`mock_store.py`)

With `from __future__ import annotations`, annotations are strings at runtime, but `cast()` evaluates its first argument at call time — it returns the value unchanged and is a type-checker hint only. Using stringified casts (`cast("Resp", value)`) adds indirection with no runtime benefit.

**Files:** `tests/functional/mock_store.py`

| Line | Pattern |
|------|---------|
| 83 | `cast("NixReader", DummyRW(...))` |
| 84 | `cast("NixWriter", DummyRW(...))` |
| 166 | `cast("Connection", MockConnection(...))` |
| 183 | `cast("Resp", self.responses[...])` |
| 187 | `cast("Resp", QueryValidPathsResponse(...))` |
| 190 | `cast("Resp", QueryAllValidPathsResponse(...))` |
| 208 | `cast("Resp", QueryClosureWithInfoResponse(...))` |
| 231 | `cast("Any", conn)` |

**Fix:** Change `cast("Type", value)` → `cast(Type, value)`. The type is already imported (or available under `TYPE_CHECKING`). Since `from __future__ import annotations` is in effect, the annotation-style cast still requires the type to be importable at runtime — but `cast` already does, since it's called at runtime. Moving type-checking-only imports out of `TYPE_CHECKING` for `cast` targets is the right tradeoff.

---

## Completion (2026-05-04)

**All lazy imports fixed.** 12 source files modified, ~85 imports promoted to top-level.
Verified with `just cheap` (0 pyright errors, 0 ruff errors) after each edit.
Committed at `nvmqtyqm`.
