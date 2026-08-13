# 06 — BuildDerivation wire format for DrvWithVersion

**Status**: N/A (confirmed not applicable)
**Depends on**: 05 (resolution — resolved derivations won't need this)
**Priority**: N/A — the wire protocol fundamentally cannot carry DrvWithVersion

## Original Problem

When pynixd sends a `BuildDerivation` request to a builder daemon, it serializes a `BasicDerivation` using `to_writer()`. The question was whether dynamic derivations (`is_dynamic=True`) need `DrvWithVersion("xp-dyn-drv",...)` ATerm format on the wire.

## Investigation Result

**The BuildDerivation wire protocol does NOT use ATerm format at all.**

Nix's `writeDerivation()` (in `src/libstore/derivations.cc:1019-1065`) writes a flat binary format:
- `uint64` output count, then per-output: (name, path, hashMethod, hashDigest)
- `StorePathSet` inputSrcs
- platform, builder, args, env

This is `BasicDerivation` serialization — it has **no `inputDrvs` field** (that's on `Derivation`, not `BasicDerivation`). The wire protocol **cannot convey `inputDrvs` or `dynamic_input_drvs`**, so `DrvWithVersion` vs `Derive(...)` is irrelevant on the wire.

The ATerm format (`Derive(...)` vs `DrvWithVersion("xp-dyn-drv",...)`) is used **only** for on-disk `.drv` files via `Derivation::unparse()`.

The Nix daemon handler for BuildDerivation (in `daemon.cc:584-658`):
1. Reads `BasicDerivation` from wire (no `inputDrvs`)
2. Upcasts to `Derivation` with empty `inputDrvs`
3. Since `hasDynamicDrvDep()` returns false, it writes as `Derive(...)` on disk

## Conclusion

pynixd's resolution pipeline (`_resolve_dynamic_derivation()`) correctly produces resolved `BasicDerivation` objects with all dynamic deps rewritten to concrete paths in `inputSrcs`. The `_unparse_basic_derivation()` function always outputs `Derive(...)` with empty `inputDrv` list. This is exactly what the wire protocol expects.

**No code changes needed.** The original task's recommendation to "skip for now" was correct, but the reason is stronger than originally stated — it's not just "we always resolve," it's that the wire protocol literally cannot carry DrvWithVersion.