# Mini-plans for Future Test Coverage Improvement

This file documents areas that need complex rearchitecting or significant
infrastructure work before they can be adequately tested. These are **not**
blockers — they represent gaps that require non-trivial design investment.

## 1. Daemon Protocol Parser Unit Tests ✅ (done)

**Status**: Complete. `tests/unit/test_wire.py` has 99 tests covering:
- `BytesReader` in-memory reader (mirror of `BytesWriter`) added to `pynixd/wire.py`
- **Primitive roundtrips**: uint64 (0, 1, max, large), uint64s, optional (present/none/zero),
  string (empty, ascii, unicode, StorePath-typed), bytes (empty, small, aligned, padded),
  string_list (empty, single, multiple), string_set (empty, single, multiple, StorePath-typed)
- **Framed roundtrips**: single-chunk, multi-chunk, empty, ensure_eof, ensure_eof with trailing chunks
- **Operation serialization**: 38 test classes covering every wire-dispatched operation
  (and extension ops with wire format): request to_writer → from_reader and response
  to_writer → from_reader. Uses `BytesWriter` + `BytesReader` pair.
  - Skipped: ProbeSystems/ProbeFeatures (no-op serialize, never wire-dispatched)
  - Streaming ops (AddToStore, AddToStoreNar, AddMultipleToStore, NarFromPath) tested for
    their request header serialization; streaming response is handled separately
  - Known protocol quirks documented: `UnkeyedValidPathInfo.to_writer` strips `sha256:`
    prefix from nar_hash, request to_writer prepends opcode that dispatch loop consumes
- **OperationLogs**: Empty, StderrNext, StderrStartActivity serialization roundtrip

## 2. DRV Parser Unit Tests ✅ (done)

**Status**: Complete. 36 tests in `tests/unit/test_drv_parser.py`.

- **Live probes**: Session-scoped fixture evaluates `tests/nix/drv-probes.nix`
  (8 attributes covering simple, ca-floating, ca-fixed, text-hashed, dynamic,
  with-features, minimal, multi-output), reads real .drv content from the store,
  validates `parse_drv()` and `to_json()` env against canonical
  `nix derivation show` JSON — including a parameterized test that checks every
  probe matches env-to-env.
- **Manufactured edge cases**: Inline `Derive(...)` strings for deferred,
  empty env, escaped strings, multiple outputs, requiredSystemFeatures.
- **DrvWithVersion**: Dynamic derivation ATerm format parsing.
- **Properties**: `output_paths()`, `output_kinds()`, `required_system_features()`.
- **to_basic_derivation**: Tested with mocked `output_cache` and fallback.
- **Error handling**: Invalid syntax, unterminated string, unknown ATerm version, empty input.

## 3. Derivation Resolution Unit Tests

**Problem**: `pynixd/derivation_resolution.py` implements the full Nix
derivation resolution algorithm (CA → IA conversion, placeholder computation,
dynamic derivation resolution). This is **critical** correctness-sensitive
code with no unit tests.

**What's needed**:
- Test `_compress_hash()` — verify XOR-folding produces correct output for
  known inputs
- Test `downstream_placeholder()` — compare against known Nix values for
  specific derivation+output combinations
- Test `_make_store_path()` — verify store path computation matches Nix
  for given `name` + `hash` combinations
- Test `_hash_derivation_modulo()` — verify ATerm hashing matches Nix
  (this is the key operation for CA derivation identity)
- Test `resolve_derivation()` — full end-to-end with mock `Derivation`
- Test `resolve_dynamic_derivation()` — dynamic drv resolution
- Test `_rewrite_strings()` — verify string replacement ordering

**Approach**: This requires building a library of known-good inputs/outputs.
Ideal approach: hook into Nix's own derivation resolution (via `nix derivation
show` with `--recursive`) to generate test vectors, then verify pynixd produces
identical results. Add `tests/unit/test_derivation_resolution.py`.

## 4. MockUpstreamStore for Wire-Only Tests

**Problem**: Nearly all integration tests require a real Nix daemon (upstream)
to be running. This makes tests slow (~3 minutes for the full suite) and
environment-dependent.

**What's needed**: A `MockUpstreamStore` that:
- Responds to protocol operations with pre-recorded responses
- Supports configurable failure modes (connection drop, protocol error, timeout)
- Can simulate different protocol versions (1.32, 1.35, 1.38)

**Approach**: Add to `tests/mock_store.py` alongside the existing `MockStore`.
Use serialized wire-format responses captured from a real Nix daemon session.
This enables testing:
- Error recovery when upstream daemon disconnects
- Protocol version negotiation mismatch
- Slow upstream responses (timeout handling)
- Corrupt responses (malformed data)

## 5. HTTP Binary Cache Error Testing

**Problem**: `test_http_cache.py` and `test_http_upload.py` only test
success paths. Error scenarios (partial NAR upload, 404 responses,
connection drops mid-stream) are untested.

**What's needed**: An HTTP mock server that can simulate:
- 404 on narinfo fetch
- Truncated NAR responses (connection close mid-stream)
- Slow responses that trigger client timeouts
- Auth challenges (401)

**Approach**: Use `aiohttp.test_utils` or a lightweight mock HTTP server
in tests. Add `tests/functional/test_http_cache_errors.py`.

## 6. Protocol Compatibility Tests

**Problem**: pynixd advertises protocol 1.38 but translates for older stores
(1.32 builder stores). There are no tests for this translation layer.

**What's needed**: Test that:
- Operations work correctly when downstream speaks 1.32 (e.g., nixbuild.net)
- Operations work correctly when downstream speaks 1.35 (Lix)
- The feature matrix announcement respects the downstream version
- Operations that require features unavailable in older versions are handled

**Approach**: Create `LocalSocketStore` instances that fake older protocol
versions by intercepting the handshake. Add `tests/functional/test_protocol_compat.py`.

## 7. Concurrent Client / Connection Pool Tests

**Problem**: No test verifies behavior with multiple simultaneous clients.
Race conditions in the connection pool or session management would go
undetected.

**What's needed**: A test that:
- Opens N concurrent connections to the same pynixd server
- Issues independent operations on each
- Verifies all complete correctly
- Tests overlapping builds
- Tests connection pool exhaustion/recovery

**Approach**: Use `asyncio.gather` with a large number of concurrent
`LocalSocketStore` connections. Add `tests/functional/test_concurrency.py`.

## 8. System Feature Probing Tests

**Problem**: Feature probing (`ProbeSystems`/`ProbeFeatures`) is tested
indirectly via `no_probe` parameter in tests, but the probe logic itself
has no dedicated tests.

**What's needed**: Test that:
- Probe correctly identifies supported systems
- System features (`kvm`, `big-parallel`, etc.) are properly detected
- Probe failures don't crash the server
- Cached probe results are reused correctly across connections

**Approach**: Use `MockStore` with pre-configured available systems/features.
Add `tests/functional/test_feature_probing.py`.
