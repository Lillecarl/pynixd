# 07 — Fix QueryRealisation wire format

**Status**: DONE
**Priority**: Low → addressed (DrvOutput type safety + execute tracking)

## Original Problem

`QueryRealisationRequest` for text-hashed CA derivations fails with:
```
BackendError: Daemon error (Error): unknown hash algorithm '/nix/store/wsfdgkf6905rn06lq7x5445i594a1j88', expect 'blake3', 'md5', 'sha1', 'sha256', or 'sha512'
```

The daemon is parsing a store path as a hash algorithm.

## Root Cause

The Nix daemon expects `QueryRealisation` (op 43) to receive a `DrvOutput` identifier in the format:
```
sha256:<base16-hex-hash>!<outputName>
```

The hash is the derivation's `hashDerivationModulo` — NOT the `.drv` store path.

Nix's `DrvOutput::parse()` (in `src/libstore/realisation.cc:11-21`) splits on `!`, then calls `Hash::parseAnyPrefixed()` on the first part, which requires the `sha256:` prefix. Without it, the daemon tries to parse the store path as a hash algorithm.

pynixd's proxy path (client → pynixd → daemon) was already correct — clients send properly-formatted `DrvOutput` strings. The bug was only in pynixd's internal test script that constructed `QueryRealisationRequest(drv_output=f"{drv_store_path}!out")`.

## Fix Applied

1. **`DrvOutput` class** (`pynixd/store_path.py`): Replaced the `DrvOutput = str` type alias with a `str` subclass that validates the `!` separator on construction. Provides `.id_hash` and `.output_name` properties.

2. **`QueryRealisationRequest`** (`pynixd/operations/ca_derivations.py`): Uses `DrvOutput` type for the `drv_output` field. `from_reader()` wraps the wire string in `DrvOutput()`. Added `execute()` method that tracks `outPath` from realisations in the store's path tracker (consistent with `RegisterDrvOutputRequest`).

3. **`BuildResult.built_outputs`** (`pynixd/operations/base.py`): Key type changed from `str` to `DrvOutput`. `from_reader()` wraps wire strings in `DrvOutput()`.

4. **Test script** (`tests/ai/dynamic_drv.py`): Fixed `QueryRealisation` step to compute `hashDerivationModulo` from the `.drv` content instead of using the store path.

## What Was Already Correct

The pynixd proxy forwarding path was fine — when a Nix client sends `QueryRealisation` through pynixd, the `DrvOutput` string is read from the client wire and forwarded verbatim to the daemon. Clients always send the correct `sha256:hash!outName` format. The bug only manifested when pynixd code (or test scripts) tried to construct a `DrvOutput` from a store path.

## Note on ANSI Escapes

The original task title mentioned "ANSI bug" but the ANSI escape codes in daemon error messages are normal Nix daemon behavior when connected to a pty-like stream. Not a pynixd bug. Renamed for clarity.