# Deferred Derivation Resolution — Approaches Tried

## Problem
A non-CA derivation depends on a CA derivation. At evaluation time, Nix
can't compute the output path because the CA output is unknown. The .drv
file stores `path=""` for each output — this is a **deferred** derivation.

The deferred derivation's env contains `downstream_placeholder(drv, out)`
strings that must be replaced with actual CA output paths before building.

## Approaches

### 1. Delegate to CADerivationHandler (DIDN'T WORK)
`DerivationHandler` saw `output.path == ""` and dispatched to
`CADerivationHandler`. But CADerivationHandler assumes CA semantics
(content-addressed hash), and the daemon doesn't know the output path
either → `$out` empty → `sh: can't create : nonexistent directory`.

### 2. Send empty paths, let daemon resolve (DIDN'T WORK)
Sent `BuildDerivationRequest` with empty output paths in the
`BasicDerivation`, relying on the daemon to resolve placeholders using
registered realisations. The Nix daemon **does not** resolve deferred
derivations during `BuildDerivation` — it just uses the output paths
from the `BasicDerivation`. Empty path → `$out=""` → builder fails.

### 3. Pre-compute hash via _resolve_deferred_outputs (HASH MISMATCH)
Used `_resolve_deferred_outputs` from the old `derivation_resolution`
module. This calls `_hash_derivation_modulo()` which calls
`_unparse_basic_derivation()` — produces an ATerm WITHOUT `input_drvs`
(since `BasicDerivation` has no such field). But the daemon reads the
`.drv` file which HAS `input_drvs`. The daemon's `hashDerivationModulo`
replaces `input_drvs` with modulo values before hashing, while
`_unparse_basic_derivation` just omits them entirely. These produce
different hashes → `OUTPUT_REJECTED`.

### 4. Hash via Derivation.serialize() (HASH MISMATCH — different direction)
Took the parsed `Derivation`, rewrote env/args with resolved paths,
called `Derivation.serialize()` which includes `input_drvs` as raw
store path references. Hashed the full ATerm. But the daemon's
`hashDerivationModulo` replaces `input_drvs` with modulo hash values
BEFORE hashing — not raw store paths. Different hash → wrong output path.

### 5. Clear input_drvs, serialize Derivation (CURRENT — SHOULD WORK)
Same as approach 3 but uses the actual `Derivation.serialize()` (not
`_unparse_basic_derivation`) after clearing `input_drvis` and
`dynamic_input_drvs`. Should produce the same ATerm format as the
daemon produces AFTER its modulo-replacement step, since:
- Daemon replaces each `input_drv` entry with a fixed-size modulo hash
  placeholder
- These placeholders are not part of the ATerm format — they collapse
  to the same empty `[]` as clearing them outright
- Only input_srcs (which are real store paths) remain
- Output paths, env, args match the resolved derivation
