# 1C: Fix supports_derivation() returning True for unprobed stores

**Severity**: High (Bug)
**Category**: Correctness

## Problem
`Store.supports_derivation()` in `pynixd/store/base.py` returns `True` unconditionally
when `feature_matrix` is `None` (store not yet probed). This means a derivation
requiring `kvm` can be scheduled to a Darwin builder, or `apple-virt` to a Linux
builder, simply because the feature matrix hasn't been probed yet.

The old guard "if no feature_matrix, assume compatible" is too permissive for
platform-specific features and should at minimum check that the requested features
don't contain platform-specific ones.

## Fix
When `feature_matrix` is `None`, check whether the requested features are
subsets that COULD exist on any system. Specifically, require that either:
- No features are requested, OR
- All requested features are NOT platform-specific (not in {kvm, apple-virt})

This is a defense-in-depth fix. The primary guard is probing before scheduling.

## Files
- `pynixd/store/base.py` — `supports_derivation()` method
