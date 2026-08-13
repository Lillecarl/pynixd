# 2B: Deduplicate nix32_encode and NIX32_CHARS

**Severity**: Medium (Code Duplication)
**Category**: Maintainability

## Problem
`pynixd/derivation_resolution.py` redefines `NIX32_CHARS = "0123456789abcdfghijklmnpqrsvwxyz"`
on line 40. This exact constant and the `nix32_encode` function already exist in
`pynixd/utils.py`. The module uses `from .utils import nix32_encode` — NIX32_CHARS
is the only duplicate.

## Fix
Remove the local `NIX32_CHARS` constant from `derivation_resolution.py`.
Check what it's used for there — it's not actually referenced in the module
(the hashlib + nix32_encode functions use the import from utils). Just clean it up.

## Files
- `pynixd/derivation_resolution.py`
