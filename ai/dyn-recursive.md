# Dynamic Derivation Chain: Recursive childMap Support

## Background

`builtins.outputOf` wraps its first argument in a
`SingleDerivedPath::Built{drvPath=prev, output=name}`.  Because the first
argument is itself a `SingleDerivedPath`, the calls can be chained
**arbitrarily deeply** — each level adds one more `Built` wrapper.

At derivation instantiation this produces a
`DownstreamPlaceholder::fromSingleDerivedPathBuilt` chain, embedded in
the build environment as a placeholder string.  The `.drv` file's
`DrvWithVersion` section stores the nesting as a recursive
`DerivedPathMapNode` tree in `inputDrvs.childMap`.

## What we have

The existing `test_dyn_wrapper` exercises **2 levels** of nesting
(`producer!out!out = target!out`).  This works end-to-end:

1. Nix evaluates `builtins.outputOf producer.outPath "out"` → 2-deep
   `SingleDerivedPath::Built` → placeholder in build env
2. The `.drv` contains a 1-level `childMap`:
   `{producer: {out: [out]}}`
3. pynixd's `drv_parser.py:parse_input_drvs_dynamic` handles 1 level
4. `_resolve_deferred` creates a `DynamicBuildGoal` for the nested path
5. `_unparse_for_hash` serializes the 1-level childMap
6. `_do_build_with_derivation` writes a resolved `.drv` to the store

## Test fixture (works, checked in)

A 5-level chain can be expressed in Nix:

```nix
fiveDeep = builtins.outputOf
  (builtins.outputOf
    (builtins.outputOf
      (builtins.outputOf
        (builtins.outputOf producer.outPath "out")
        "out")
      "out")
    "out")
  "out";
```

This is defined in `tests/nix/dyn-drv.nix` as `deepWrapper` (but see
blockers below — the test is checked-in but expected to fail until the
parser/serialization is fixed).

## What breaks

### 1. Parser (`pynixd/drv_parser.py:671`)

`parse_input_drvs_dynamic` expects a flat string list for the inner
output names:

```python
nested_deps = self.parse_string_list()  # expects `["out"]`
```

But a 5-level chain produces a recursive `childMap`:

```
(hash,([],[(out,([],[(out,([],[(out,([],[(out,([out]))]))]))]))]))
```

The parser needs to detect `(` vs `[` and recurse.

**Fix sketch:**

```python
def _parse_child_map_node(self) -> dict:
    """Parse a DerivedPathMapNode recursively."""
    # ... handle [out1,out2] (leaf) vs ([outs],[childMap]) (nested)
```

Store the result in a recursive structure like:
```python
# Flat leaf:  {"_outs": ["out"]}
# Nested:     {"_outs": [], "out": {"_outs": ["out"]}}
#              or a dataclass
```

### 2. Resolution (`pynixd/goals/resolution.py`)

**`_resolve_deferred`** — the `dynamic_input_drvs` type is currently
`dict[StorePath, dict[str, list[str]]]` (1 level).  For deep nesting
it needs a recursive `Node` type.

The `DynamicBuildGoal` chain must also be built recursively: for an
N-level childMap, create N nested `DynamicBuildGoal`s instead of 1.

**`_unparse_for_hash`** — the `dynamic_input_drv_hashes` parameter
needs a recursive structure matching the `DerivedPathMapNode` tree.
Currently it's `dict[str, dict[str, list[str]]]` (1 level).  Deep
nesting needs `dict[str, Node]` where `Node` is `(flat_outs, childMap)`.

**`_collect_resolved_paths`** — same issue: needs recursive traversal
of the goal tree to collect modulo hashes for each nesting level.

### 3. Build derivation (`pynixd/goals/derivation.py`)

**`_do_build_with_derivation`** — the dynamic output path collection
needs to handle recursive `dynamic_input_drvs`.  Currently only one
level of `childMap` is traversed.

## The `downstream_placeholder` chain

For N-level `outputOf`, each level computes a different placeholder:

| Level | Function | Format |
|-------|----------|--------|
| 1 (top) | `unknownCaOutput(drvPath, "out")` | `nix-upstream-output:{hash}:{name}` |
| 2+ | `unknownDerivation(parent_placeholder, "out")` | `nix-computed-output:{compressed_parent}:{name}` |

These are already implemented in `pynixd/derivation_resolution.py`:
- `downstream_placeholder()` — level 1
- `downstream_placeholder_unknown_derivation()` — level 2+

## The `DynamicBuildGoal` chain

For `producer!out!out!out!out!out`, the goal tree needs 5 levels:

```
build(wrapper.drv!out)
  → ResolutionGoal._resolve_deferred
    → DynamicBuildGoal(producer!out!out!out!out!out)
      → outer: build(producer!out!out!out!out)  → DynamicBuildGoal again
        → outer: build(producer!out!out!out)       → DynamicBuildGoal again
          → outer: build(producer!out!out)           → DynamicBuildGoal again
            → outer: build(producer!out)               → DerivationBuildGoal
            → remainder: wrap(target.drv) → target!out
          → remainder: ...
```

Each level:
1. Builds outer (peels one `childMap` level)
2. Gets a `.drv` from the result
3. `wrap` creates a new `DerivedPath` with that `.drv` as root
4. Builds the remainder

## Required changes summary

| File | Change | Complexity |
|------|--------|------------|
| `drv_parser.py` | Recursive `_parse_child_map_node` | Medium |
| `goals/resolution.py` | Recursive `Node` type for `dynamic_input_drvs`; recursive `DynamicBuildGoal` creation in `_resolve_deferred`; recursive `_unparse_for_hash` | Medium |
| `goals/derivation.py` | Recursive path collection in `_do_build_with_derivation` | Medium |
| `derivation_resolution.py` | `resolve_dynamic_derivation` already handles 2 levels; needs recursive `unknownDerivation` chain for N>2 | Low |

## Test

The test case `test_deep_dynamic` is checked in to `tmp/test_goals.py` (line
451) and the Nix fixture `deepWrapper` is in `tests/nix/dyn-drv.nix`.  It
is NOT expected to pass until the parser and serialization are fixed.
