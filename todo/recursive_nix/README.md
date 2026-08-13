# Recursive Nix — Task Breakdown

Experimental Nix feature: allows a derivation builder to invoke `nix build` from within its sandbox, producing new store paths and even nested derivations that get built by the outer daemon.

## Key Insight

Recursive-nix is **not a protocol change** — it's an access-control layer. The inner build connects to the same daemon protocol via a Unix socket bind-mounted into the sandbox. The daemon wraps all operations through a `RestrictedStore` that gates reads, censors metadata, and blocks dangerous ops.

For pynixd, the architecture is **Approach B**: pynixd doesn't run the sandbox (the backend store does). It just needs to ensure the backend enables `recursive-nix` for derivations that declare it in `requiredSystemFeatures`, and the rest follows the existing dynamic-derivation trampoline pattern.

## System Features

`requiredSystemFeatures` is a set of arbitrary strings on a derivation. A builder is only eligible if its `system-features` is a superset. There is no protocol negotiation — it's pure string subset matching at schedule time.

### Known Feature Strings

| Feature | Auto-added when |
|---|---|
| `nixos-test` | Always (back compat) |
| `benchmark` | Always (back compat) |
| `big-parallel` | Always (back compat) |
| `uid-range` | Linux |
| `kvm` | `/dev/kvm` readable+writable |
| `apple-virt` | macOS with `hasVirt()` |
| `ca-derivations` | `experimental-features` includes `ca-derivations` |
| `recursive-nix` | `experimental-features` includes `recursive-nix` |

Users can also set arbitrary custom strings in `nix.conf`.

## How Nix Implements It

1. Build goal checks `requiredSystemFeatures` against `system-features` at schedule time
2. If `recursive-nix` is in `requiredSystemFeatures`, the build goal starts an **in-process daemon thread** on `tmpDir/.nix-socket`
3. `NIX_REMOTE=unix:///build/.nix-socket` is set in the builder's env
4. The inner daemon uses `RestrictedStore` wrapping `LocalStore`:
   - Gates all reads behind `isAllowed()` (inputPaths ∪ addedPaths)
   - Passes through `AddToStore`, `AddToStoreNar`, `BuildPaths`, `BuildPathsWithResults` — new paths are added to allowlist via `goal.addDependency()`
   - **Blocks**: `BuildDerivation`, `RegisterDrvOutput`, `CollectGarbage`, `AddPermRoot`, `AddIndirectRoot`, `AddTempRoot`, `FindRoots`
   - **Skips**: `SetOptions`
   - **Censors**: `QueryPathInfo` (strips deriver, signatures, registration time)
   - Connection is always `NotTrusted`
5. New paths are **bind-mounted into the sandbox** namespace in real-time (Linux-specific, uses saved namespace FDs)
6. When outer build finishes, `addedPaths` are valid references for output closure checking

## How pynixd Should Implement It

pynixd is a proxy, not a build executor. The backend store runs the sandbox. So:

- pynixd parses `requiredSystemFeatures` from `.drv` ATerm during build decomposition
- pynixd checks each backend store's `system-features` to determine eligibility
- pynixd passes `experimental-features = recursive-nix` (or other needed features) to the backend via `--option` when scheduling
- The backend store handles the restricted daemon internally
- From pynixd's perspective, recursive-nix builds produce `.drv` outputs that trampoline — same pattern as dynamic derivations

## Task Dependency Graph

```
01 (SystemFeatures enum + drv parsing)
├── 02 (Per-store feature tracking)
│   └── 03 (Build scheduling: feature compatibility check)
│       └── 04 (Pass required features to backend via --option)
│           └── 05 (Recursive-nix: restricted daemon awareness)
└── 06 (Feature mismatch error reporting)
```

## Execution Order

| # | Task | Priority | Depends on | Status |
|---|------|----------|------------|--------|
| 01 | Parse `requiredSystemFeatures` from `.drv` ATerm into enum set | Critical | — | TODO |
| 02 | Track `system-features` per `Store` (from daemon handshake/config) | High | 01 | TODO |
| 03 | Check feature compatibility at build scheduling time — deny if no builder has required features | High | 01, 02 | TODO |
| 04 | Pass required experimental features to backend via `--option extra-experimental-features` | Medium | 03 | TODO |
| 05 | Recursive-nix: ensure backend enables `recursive-nix` experimental feature for qualifying derivations | Medium | 04 | TODO |
| 06 | Clear error messages when feature requirements can't be met (which features missing, which builders lack them) | Low | 03 | TODO |

## Research Questions

- [ ] Does `--option` override or append to daemon config? If a builder already has `ca-derivations` enabled, does passing it again cause issues?
- [ ] Should pynixd advertise `recursive-nix` and `ca-derivations` in its own daemon handshake `get_extension_features()`?
- [ ] How does the `--builders` SSH protocol pass `system-features`? Does pynixd need to propagate it?
- [ ] For dynamic derivations produced by recursive-nix builds: does pynixd need to do anything special, or does the existing trampoline handle them?
- [ ] Should `requiredSystemFeatures` be part of `BasicDerivation` (wire format) or only in `Derivation` (ATerm)?