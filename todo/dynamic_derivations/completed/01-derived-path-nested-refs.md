# 01 — Extend DerivedPath to support nested dynamic derivation references

**Status**: Completed
**Blocks**: All dynamic derivation work — this is the foundation
**Priority**: Critical — nothing else works without this

## Problem

`DerivedPath` (in `pynixd/derived_path.py`) only supports the old `drv!out1,out2` format. Dynamic derivations require nested references like `producingDrv.drv^out^out` — meaning "the `out` output of the derivation that IS the `out` output of `producingDrv`".

The Nix C++ code models this as `SingleDerivedPath::Built` chains:
```cpp
SingleDerivedPath::Built {
    .drvPath = SingleDerivedPath::Built {  // inner: producingDrv.drv^out
        .drvPath = SingleDerivedPath::Opaque { "/nix/store/.../producingDrv.drv" },
        .output = "out"
    },
    .output = "out"  // outer: the "out" of whatever derivation producingDrv^out IS
}
```

## Design

Chose Option A — recursive model mirroring Nix C++.

### Types defined

- `SingleDerivedPathOpaque(path: StorePath)` — opaque store path
- `SingleDerivedPathBuilt(drv_path: SingleDerivedPath, output: str)` — recursive built ref
- `SingleDerivedPath = SingleDerivedPathOpaque | SingleDerivedPathBuilt`
- `DerivedPathOpaque(path: StorePath)` — opaque derived path
- `DerivedPathBuilt(drv_path: SingleDerivedPath, outputs: OutputsSpec)` — built derived path
- `DerivedPathUnion = DerivedPathOpaque | DerivedPathBuilt`
- `OutputsAll` / `OutputsNames(frozenset[str])` — output specification

### Parsing

Both `!` and `^`-separated formats are supported via `parse_derived_path_legacy()` and `parse_derived_path()`. Parsing uses right-to-left `rfind`, matching Nix's C++ `parseWithSingle`/`parseWith`.

### Wire compatibility

`DerivedPath` remains a `StorePath` (str subclass) for backward compatibility with `read_string_set(DerivedPath)` and `write_string_set()`. Internally wraps a `DerivedPathUnion` accessible via `.derived`.

### Helper functions

- `dp_drv_path(dp)` / `dp_output_names(dp)` — accessors on `DerivedPathUnion`
- `dp_to_derivation(dp, store_path)` / `dp_to_outputs(dp, store_path)`
- `dp_is_nested(dp)` — checks for `SingleDerivedPathBuilt` at inner level

### Backward-compatible properties on `DerivedPath` class

- `.drv_path`, `.output_names`, `.to_derivation()`, `.to_outputs()`, `.is_nested`
- `.derived` — the structured `DerivedPathUnion`

## Verification

```python
dp = DerivedPath("/nix/store/xxx-producingDrv.drv!out!out")
assert dp.is_nested
assert isinstance(dp.derived.drv_path, SingleDerivedPathBuilt)
assert isinstance(dp.derived.drv_path.drv_path, SingleDerivedPathOpaque)
assert str(dp.derived.drv_path.drv_path.path) == "/nix/store/xxx-producingDrv.drv"
assert dp.derived.drv_path.output == "out"
assert dp.output_names == {"out"}
```

All checks pass. 80 functional tests pass, 0 pyright errors.

## Files modified

- `pynixd/derived_path.py` — core model (rewritten from 87 → ~230 lines)