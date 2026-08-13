# Content-Addressed (CA) Derivation Support

## Summary

CA derivations require pynixd to handle operations where output paths aren't known until build time. The root-store tests all pass (5/5), but the pynixd proxy test fails at the very first operation: `QueryMissing` with a `.drv!out` derived path causes the backend daemon connection to break after a stderr error.

**6 problems need fixing**, in this order:

1. **`QueryDerivationOutputMapResponse` uses `dict[str, StorePath]` but the protocol sends `dict[str, optional<StorePath>]`** — unresolved CA outputs are `""` (nullopt). pynixd treats them as `StorePath("")` and can't distinguish "known empty" from "not yet realised".

2. **`RegisterDrvOutput` (op 42) and `QueryRealisation` (op 43) are wire passthrough only** — no local store registration, no caching. After a CA build, pynixd must register realisations on its local store.

3. **Post-build realisation registration missing** — after `BuildDerivation` completes with CA outputs, `BuildResult.built_outputs` contains `{DrvOutput: Realisation}` dicts that pynixd passes through opaquely without calling `RegisterDrvOutput` on the local store.

4. **`BuildPathsWithResults` doesn't propagate CA realisations to the client** — the per-drv `built_outputs` field must include realisation data for CA drvs.

5. **PathTracker doesn't track CA realisation paths** — after a CA build, the realised output paths must be added to `PathTrackerInstance`.

6. **`DerivedPath.to_outputs()` returns empty paths for CA floating drvs** — it reads the `.drv` ATerm's static output map, which is empty for floating CA. Needs `QueryDerivationOutputMap` fallback.

## Test Infrastructure

- **`test-ca.nix`**: Fixture with 4 CA derivation targets
- **`tests/functional/test_ca_ops.py`**: 5 root-store tests (PASS), 1 pynixd test (FAIL at `QueryMissing`)
- **Managed daemon config**: `--extra-experimental-features ca-derivations` via `CA_EXTRA_ARGS` in `_ca_test_store_kwargs()`
- **Client config**: `CA_NIX_CONFIG = {"extra-experimental-features": "ca-derivations", "substituters": "https://nixkube.cachix.org"}` — the system daemon substituter MUST be excluded (it's Lix, doesn't support CA)
- **Root store builds work**: All 5 root-store tests pass against a managed Nix 2.34 daemon with CA enabled

---

## Verbose Research Notes

### What is a CA derivation?

Normal (input-addressed) derivations have output paths determined by the derivation content itself. You can read a `.drv` file and know exactly what store paths it will produce. Content-addressed derivations are different — their output paths depend on the *content* of the built output, which isn't known until the derivation actually builds.

There are two CA variants:
- **CA_FIXED**: Like the classic "fixed-output derivation" (`outputHashAlgo` + `outputHash` provided). The output path IS known before building (it's derived from the declared hash). This already works in pynixd — it's just a special kind of input-addressed derivation from the proxy's perspective.
- **CA_FLOATING**: Created with `__contentAddressed = true` and an `outputHashAlgo` but NO `outputHash`. The output path is unknown until the derivation builds. The `.drv` ATerm stores an empty string for the output path. This is what breaks pynixd.

When a CA_FLOATING derivation builds, the daemon:
1. Builds the derivation normally
2. Hashes the output content
3. Computes the output store path from that hash
4. Registers a **Realisation** via `registerDrvOutput()` — this maps a `DrvOutput` key (hash of the derivation modulo + output name) to the concrete output path
5. Future `queryPartialDerivationOutputMap()` calls can then resolve the output path

A derivation that *depends* on a CA derivation is called **DEFERRED** — its output path can't be computed until the CA dependency's realisation is known. The `.drv` ATerm has empty path *and* empty hash for DEFERRED outputs.

### The pynixd request-driven architecture (refresher)

pynixd follows a strict three-tier pattern. Knowing this is essential for understanding where CA fixes go:

1. **Server Dispatch** (`OpRequest.handle(proxy)` in `pynixd/operations/base.py`): Entry point. Decodes request from client wire, delegates to `proxy.execute(request)`.
2. **Logic Hook** (`OpRequest.execute(store, client, suppress_last)` in `pynixd/operations/base.py`): Where the "recipe" lives. Implements optimizations (SQLite fast-paths, memory caches). Falls back to `store.call(self, ...)` if no optimization exists.
3. **Store Executor** (`Store.execute(request, ...)` in `pynixd/store.py`): Calls `request.execute(self, ...)`.

For CA operations, ops 41/42/43 currently have NO custom `execute()` — they go straight to `store.call()` (wire passthrough). The fix needs to add `execute()` methods that handle local state.

### Protocol Details

#### Op 41: QueryDerivationOutputMap

**Wire format**: Client sends a `StorePath` (the `.drv` path). Response is `map<string, optional<StorePath>>` — keys are output names (`"out"`, `"dev"`), values are either the resolved store path or empty string (`""`) for unresolved floating CA outputs.

**The pynixd bug**: `pynixd/operations/query_derivation_output_map.py` — `QueryDerivationOutputMapResponse.items` is typed as `dict[str, StorePath]`. The `from_reader` reads values with `await reader.read_string(StorePath)` unconditionally, so an empty string from the daemon for an unresolved CA output becomes `StorePath("")`. The fix: change to `dict[str, StorePath | None]`, read empty string as `None`.

**How Nix serializes `optional<StorePath>`**: Empty string `""` = nullopt, non-empty string = the store path. See `src/libstore/common-protocol.cc` (~line 78-89) in the Nix source.

#### Op 42: RegisterDrvOutput

**Wire format** (protocol >= 1.31): Client sends a JSON string containing a `Realisation` object. Response is `OperationLogs` only (no data).

**pynixd**: `pynixd/operations/ca_derivations.py` — `RegisterDrvOutputRequest.realisation: dict` is opaque JSON. No custom `execute()` — pure wire passthrough. **Needs**: an `execute()` method that registers the realisation locally AND forwards to the backend store. This is called by the Nix client after a CA build completes, AND internally by pynixd after it builds a CA derivation via the scheduler.

**Important**: The daemon handler has protocol version branching (<1.31 uses a simplified `DrvOutput + StorePath` format, >=1.31 uses full Realisation JSON). pynixd only supports >= 1.31, so don't worry about the old format.

#### Op 43: QueryRealisation

**Wire format** (protocol >= 1.31): Client sends a `DrvOutput` string (format: `"sha256:abc123...!out"`). Response is a set of `Realisation` JSON strings.

**pynixd**: `pynixd/operations/ca_derivations.py` — `QueryRealisationResponse.realisations: list[dict]` is opaque. No custom `execute()`. **Needs**: local store lookup for registered realisations. The daemon returns at most one Realisation per DrvOutput.

#### Realisation JSON format (>=1.31)

```json
{
    "id": "sha256:abc123...!out",
    "outPath": "/nix/store/...",
    "signatures": ["sig1", "sig2"],
    "dependentRealisations": {}
}
```

`DrvOutput` is a string: `"sha256:abc123def456...!out"` (the hash of the derivation modulo content, `!`, output name).

### Build Flow for CA Derivations Through pynixd

The request lifecycle when a client does `nix build --store ssh-ng://... --file test-ca.nix ca_simple`:

1. **Client sends `BuildPathsWithResults`** with derived paths like `{DerivedPath("/nix/store/xxx-ca-simple.drv!out")}`
2. **`_decompose_build_paths()`** (in `pynixd/operations/build_paths.py`, top of file) calls `QueryMissing` to categorize paths into `will_build`, `will_substitute`, `unknown`
3. **`QueryMissing` hits the backend daemon** — the daemon's `queryMissing` internally calls `queryPartialDerivationOutputMap` which needs `queryRealisation` for CA drvs. If realisations aren't registered, the output is treated as "unknown" → falls into `will_build`
4. **For `will_build`**: The `_decompose_build_paths` reads each `.drv` file, creates `BuildDerivationRequest` objects, and enqueues them in the scheduler
5. **Scheduler runs the build** on a builder store
6. **Post-build**: `BuildDerivationResponse.built_outputs` contains `{DrvOutput_string: Realisation_dict}` for CA drvs
7. **pynixd currently does NOTHING with `built_outputs`** — it just returns the `BuildResult` status

**The current failure point**: Step 2/3. When pynixd forwards `QueryMissing` to the backend daemon for a CA `.drv!out`, the connection hangs and eventually times out (~115 seconds) then breaks with `IncompleteReadError`. The daemon returns a `StderrError` through the stderr stream but the connection doesn't close cleanly. This might actually be a bug in how pynixd handles stderr errors from the backend daemon during `QueryMissing` — the error is received but the response parsing continues and hits EOF.

### Important Data Structures

#### `DerivationOutput` (pynixd/operations/base.py, ~line 467)

Has `path: str`, `method: str` (hash algorithm), `hash_digest: str`. The `kind` property returns an `OutputKind` enum:

- `INPUT_ADDRESSED`: has path, no hash algo (normal derivations)
- `CA_FIXED`: has path + hash algo + hash (fixed-output, classic FOD)
- `CA_FLOATING`: no path, has hash algo, no hash (floating CA, **this is the problem case**)
- `DEFERRED`: no path, no hash algo, no hash (depends on a CA drv)
- `IMPURE`: no path, has hash algo, hash="impure"

#### `BuildResult.built_outputs` (pynixd/operations/base.py, ~line 774)

```python
@dataclass
class BuildResult:
    status: BuildStatus
    error_msg: str = ""
    built_outputs: dict[str, dict] = field(default_factory=dict)
```

The keys are `DrvOutput` strings like `"sha256:abc123!out"`, NOT output names like `"out"`. The values are parsed Realisation JSON dicts. This is how the daemon communicates CA realisation results back to the caller.

#### `DerivedPath` (pynixd/derived_path.py)

A `StorePath` subclass representing `.drv!output` notation. Key methods:
- `drv_path` property: the `.drv` part before `!`
- `output_names` property: set of output names after `!` (or `{"*"}` for wildcard)
- `to_outputs(store_path)`: reads the `.drv` file and resolves output names to store paths. **BUG**: for CA floating drvs, the `.drv` ATerm has empty output paths, so this returns `StorePath("")`. Needs a fallback to `QueryDerivationOutputMap` for dynamic resolution.

The `.drv` ATerm encodes CA outputs like:
```
("out", "", "r:sha256", "")  — CA_FLOATING: path unknown, algo=r:sha256, hash unknown
("out", "", "", "")           — DEFERRED: path unknown, algo unknown, hash unknown
```

### Where the Fixes Go

#### Fix 1: QueryDerivationOutputMapResponse

File: `pynixd/operations/query_derivation_output_map.py`

Change `items: dict[str, StorePath]` → `dict[str, StorePath | None]`. In `from_reader`, after reading the value string, check if empty → treat as `None`. In `to_writer`, write `None` as empty string.

This is a prerequisite for everything else because pynixd needs to correctly parse responses that include unresolved CA outputs.

#### Fix 2-3: Post-build realisation registration

File: `pynixd/operations/build_paths.py`, in `_decompose_build_paths` or the scheduler's build completion callback.

After a `BuildDerivation` completes and returns a `BuildResult` with non-empty `built_outputs`:
1. For each `(drv_output_str, realisation_dict)` in `built_outputs`
2. Create a `RegisterDrvOutputRequest(realisation=realisation_dict)`
3. Call `local_store.execute(register_req)` to register on the local store
4. Add the realised output path (`realisation_dict["outPath"]`) to the path tracker

The scheduler build completion path is in `pynixd/scheduler.py`. The `BuildQueue` enqueues tasks that return `BuildResult` futures. When the future resolves, the caller (in `_decompose_build_paths` or `BuildPathsRequest.execute`) should check for CA built_outputs and register them.

#### Fix 4: BuildPathsWithResults realisation propagation

File: `pynixd/operations/build_paths.py`, in the `BuildPathsWithResultsRequest.execute()` method.

`BuildPathsWithResultsResponse` contains `results: dict[str, BuildResult]` where keys are derivation paths. Each `BuildResult` has `built_outputs`. Currently this data flows from the backend but may not be preserved when pynixd decomposes the build into individual `BuildDerivation` calls. Ensure `built_outputs` from each per-drv `BuildResult` is merged into the `BuildPathsWithResultsResponse`.

#### Fix 5: PathTracker realisation tracking

File: `pynixd/path_tracker.py`

`PathTrackerInstance.add_known_path()` currently only handles concrete store paths. After registering a realisation (Fix 2-3), call `tracker.add_known_path(StorePath(realisation_dict["outPath"]))` so future `QueryMissing` / `QueryValidPaths` calls know about the CA output paths.

#### Fix 6: DerivedPath.to_outputs() CA fallback

File: `pynixd/derived_path.py`

`to_outputs()` currently reads the `.drv` file's static output map. For CA floating drvs, this returns empty/invalid paths. The fix: after getting static outputs, check if any are empty strings. If so, fall back to `QueryDerivationOutputMap` (op 41) to resolve dynamically. If the realisation isn't registered yet (returns `None`), skip that output — it's not yet built.

This method is called from `_decompose_build_paths` to figure out which store paths a derivation's outputs correspond to. For CA drvs, this can't be known statically.

### Nix Source References (at ~/Code/nix)

These are the authoritative protocol definitions. Line numbers are estimates.

- `src/libstore/daemon.cc` (~line 391-398): `QueryDerivationOutputMap` handler — calls `store->queryPartialDerivationOutputMap(path)`
- `src/libstore/daemon.cc` (~line 958-969): `RegisterDrvOutput` handler — protocol version branching for old/new Realisation format
- `src/libstore/daemon.cc` (~line 972-988): `QueryRealisation` handler — returns set of Realisation JSON strings
- `src/libstore/remote-store.cc` (~line 276-308): `queryPartialDerivationOutputMap` client — merges static outputs from evalStore with dynamic results from daemon
- `src/libstore/store-api.cc` (~line 385-432): `queryPartialDerivationOutputMap` logic — without `ca-derivations` feature, just returns static outputs; with it, queries realisations and merges
- `src/libstore/common-protocol.cc` (~line 78-89): `optional<StorePath>` serialization — empty string = nullopt
- `src/libstore/include/nix/store/worker-protocol.hh` (~line 188-233): Op code enum
- `src/libstore/include/nix/store/realisation.hh` (~line 24-84): `DrvOutput` and `Realisation` C++ struct definitions
- `src/libstore/build/derivation-goal.cc` (~line 79): requires `Xp::CaDerivations` for CA drvs
- `src/libstore/build/derivation-builder.cc` (~line 1962-1978): signs and registers realisations after build
- `src/libstore/local-store.cc` (~line 632, 642): `registerDrvOutput` requires `Xp::CaDerivations`
- `src/libutil/experimental-features.cc` (~line 33-42): `ca-derivations` feature definition

The Nix CA test suite at `tests/functional/ca/` has extensive CA test scripts. `content-addressed.nix` is the most comprehensive fixture — it defines `rootCA`, `dependentCA`, `transitivelyDependentCA`, `dependentNonCA`, and others.

### Experimental Feature Requirements

CA derivations require the `ca-derivations` experimental feature on both the client and daemon:

- **Client CLI**: `--extra-experimental-features ca-derivations` flag, or `NIX_CONFIG="extra-experimental-features = ca-derivations"`
- **Managed daemon**: `--extra-experimental-features ca-derivations` in `extra_args` (passed after `nix daemon --store <path>`)
- **NIX_CONFIG fallback**: The `--extra-experimental-features` flag works as a daemon argument (Nix accepts it after the subcommand)
- **System daemon caveat**: When running tests with `--store <managed_path>`, the system daemon substituter (`unix:///nix/var/nix/daemon-socket/socket?root=/`) in `NIX_CONFIG` can cause issues if the system daemon is a Lix binary that doesn't support CA. For CA tests, override `substituters` in `NIX_CONFIG` to exclude the system daemon socket.
- **`--eval-store`**: When building via pynixd (`--store ssh-ng://...`), the client evaluates locally. If `--eval-store auto` is used, it connects to the system daemon for eval. If the system daemon is Lix (no CA support), evaluation of `__contentAddressed` fails. Use `--eval-store <managed_store_path>` to point evaluation at the CA-enabled managed daemon.

### Checkpoint

- [x] Root store tests pass (5/5): `test_ca_simple_build_root_store`, `test_ca_multi_output_build_root_store`, `test_ca_depends_on_ca_root_store`, `test_non_ca_depends_on_ca_root_store`, `test_ca_query_derivation_output_map_root_store`
- [ ] pynixd proxy test fails at `QueryMissing` — backend daemon connection breaks ~115s timeout
- [ ] Fix `QueryDerivationOutputMapResponse` for `optional<StorePath>`
- [ ] Add `RegisterDrvOutput` execution after CA builds
- [ ] Propagate `built_outputs` in `BuildPathsWithResults`
- [ ] Add realisation tracking to `PathTracker`
- [ ] Update `DerivedPath.to_outputs()` for CA drvs