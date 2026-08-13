# 02 — Text-hashed CA derivation output resolution

**Status**: Completed
**Depends on**: 01 (DerivedPath nested refs)
**Priority**: High — text-hashed outputs are the "gateway" to dynamic derivations

## Problem

When `pynixd` builds a **text-hashed CA derivation** (like `producingDrv` with `outputHashMode="text"`), the output path is unknown at evaluation time (it's a floating CA output). After building, the output IS a `.drv` file at a content-addressed path.

## Findings

**Text-hashed CA derivations already work correctly through pynixd.** The existing build flow:

1. `BuildDerivation` wire format carries the text-hashed output info correctly (`method="text:sha256"`)
2. The daemon builds it and returns `built_outputs` with the realisation
3. pynixd registers the realisation via `RegisterDrvOutputRequest` on both local and builder stores (lines 365-382 of `scheduler.py`)
4. `QueryDerivationOutputMap` correctly returns the text-hashed output path
5. Output paths are pulled back to the local store

No code changes to the scheduler or derivation resolution were needed — the existing CA realisation registration flow handles text-hashed outputs the same as any other CA floating output.

## Key difference from DEFERRED resolution

- **DEFERRED**: Non-CA derivation depends on CA output. Resolve placeholders in the deferred .drv, compute output paths, rewrite the .drv via AddToStore.
- **Text-hashed**: CA derivation whose output IS a .drv. No placeholder rewriting needed for the producingDrv itself — just build it and register the realisation. The inner .drv needs to be built separately (task 03).

## Test infrastructure fix

**Critical finding**: The default `get_test_store_kwargs()` configures `NIX_CONFIG = "substituters = ... unix:///nix/var/nix/daemon-socket/socket?root=/"` which makes the root system store a substituter. This means paths that already exist in the root store (e.g., from prior test runs) get substituted instead of built, making tests validate substitution behavior rather than actual build behavior.

Fix: The `dyn_env` fixture overrides `NIX_CONFIG` to exclude the root store substituter, so builds actually go through pynixd's scheduler.

## Tests added

1. `test_text_hashed_ca_build_root_store` — simple text-hashed CA derivation builds against root store
2. `test_text_hashed_ca_build_via_pynixd` — text-hashed CA derivation builds through pynixd proxy, with `QueryDerivationOutputMap` verification
3. `test_dynamic_drv_producing_via_pynixd` — `producingDrv` from `test-dyn-drv.nix` (text-hashed CA whose output IS a .drv) builds through pynixd; verifies output is a valid `.drv` ATerm and `QueryDerivationOutputMap` returns correct path

Also added `ca_text_hashed` fixture to `test-ca.nix` (simple text-hashed CA derivation).

## Files modified

- `test-ca.nix` — added `ca_text_hashed` fixture
- `tests/functional/test_ca_ops.py` — added 3 tests + `dyn_env` fixture