# `Store::queryMissing`

Source: `src/libstore/misc.cc:102-315`

## Purpose

Given a set of top-level derived paths (derivations + output names, or opaque store paths), determines what will need to be built vs substituted vs is unknown. Called by:

- `nix build --dry-run` via `printMissing(store, pathsToBuild)` at `src/libcmd/installables.cc:628`
- The `Worker` at the start of every build to pre-populate substitute info (`src/libstore/build/worker.cc:339-340`):
  ```cpp
  /* Call queryMissing() to efficiently query substitutes. */
  store.queryMissing(topPaths);
  ```

## Return type

```cpp
struct MissingPaths {
    StorePathSet willBuild;        // derivations that must be built locally
    StorePathSet willSubstitute;   // output paths that can be fetched from substituters
    StorePathSet unknown;          // paths no one has
    uint64_t downloadSize;         // total bytes to download for willSubstitute
    uint64_t narSize;              // total uncompressed NAR size
};
```

## Algorithm

Walks the transitive dependency closure of the given targets using a `ThreadPool`.

### `DerivedPath::Built` handling (a derivation with specific wanted outputs)

```
doPath(Built{drvPath, outputs}):
  if already seen → return

  if !isValidPath(drvPath):
    → mark drvPath as "unknown"
    → return

  // Check which outputs are known and which are valid
  for each output in queryPartialDerivationOutputMap(drvPath):
    if outputPath is null → knownOutputPaths = false
    if output is wanted AND !isValidPath(outputPath) → add to invalid set

  if knownOutputPaths AND invalid is empty → return (all good)

  // Read the derivation to check allowSubstitutes
  drv = derivationFromPath(drvPath)
  drvOptions = derivationOptionsFromStructuredAttrs(drv)

  if CA derivation with unknown outputs AND useSubstitutes:
    → check each output's Realisation against every substituter
    → if any output has no realisation → knownOutputPaths = false

  if knownOutputPaths AND useSubstitutes AND substitutesAllowed:
    → for each invalid output, query ALL substituters via querySubstitutablePathInfos
    → if substituter has it: account downloadSize/narSize, mark willSubstitute
    → if no substituter has it: mark willBuild, recursively enqueue all inputDrvs
  else:
    → mark willBuild, recursively enqueue all inputDrvs
```

### `DerivedPath::Opaque` handling (a concrete store path)

```
doPath(Opaque{path}):
  if already seen → return
  if isValidPath(path) → return

  query all substituters via querySubstitutablePathInfos
  if any has it: → mark willSubstitute, account sizes, enqueue references
  else: → mark unknown
```

### Recursive expansion

When a derivation is marked `willBuild`, all its `inputDrvs` are enqueued (line 146-148):
```cpp
for (const auto & [inputDrv, inputNode] : drv.inputDrvs.map) {
    enqueueDerivedPaths(makeConstantStorePathRef(inputDrv), inputNode);
}
```

## `querySubstitutablePathInfos` (called for each invalid output)

In `src/libstore/store-api.cc:444-493`:

```cpp
void Store::querySubstitutablePathInfos(const StorePathCAMap & paths, SubstitutablePathInfos & infos) {
    if (!useSubstitutes) return;

    for (auto & path : paths) {
        for (auto & sub : getDefaultSubstituters()) {
            auto info = sub->queryPathInfo(subPath);
            // record downloadSize, narSize, references
        }
    }
}
```

Iterates **every configured substituter** per path, calling `queryPathInfo` on each.

## Daemon protocol path

Client calls `RemoteStore::queryMissing` (`src/libstore/remote-store.cc:751-772`):
- Sends `WorkerProto::Op::QueryMissing` to the daemon
- Daemon handler (`src/libstore/daemon.cc:946-956`):
  ```cpp
  case WorkerProto::Op::QueryMissing: {
      auto targets = WorkerProto::Serialise<DerivedPaths>::read(*store, rconn);
      logger->startWork();
      auto missing = store->queryMissing(targets);  // calls LocalStore::queryMissing
      logger->stopWork();
      WorkerProto::write(*store, wconn, missing.willBuild);
      WorkerProto::write(*store, wconn, missing.willSubstitute);
      WorkerProto::write(*store, wconn, missing.unknown);
      conn.to << missing.downloadSize << missing.narSize;
      break;
  }
  ```
- The daemon runs `Store::queryMissing` (the default implementation from `misc.cc`) — there is no `LocalStore` override.
- If the daemon is too old (protocol < 1.19), `RemoteStore::queryMissing` falls back to the default implementation which runs locally over the connection.

## Why it can take 30+ seconds

| Factor | Detail |
|---|---|
| **Walks the full dependency closure** | For a derivation like `hello` from nixpkgs, it walks every transitive dependency — hundreds to thousands of derivations, each visited exactly once |
| **N+1 substituter queries per output** | Every invalid output path triggers `querySubstitutablePathInfos`, which iterates all configured substituters (`getDefaultSubstituters()`) calling `queryPathInfo` on each. With 500 invalid outputs and 2 substituters, that's 1000 HTTP requests |
| **`queryPartialDerivationOutputMap` per drv** | Reads the `.drv` file (from daemon or disk) for each derivation to resolve output paths |
| **`derivationFromPath` per drv marked willBuild** | Full deserialization of the derivation to check structured attrs and `allowSubstitutes` |
| **Daemon is synchronous** | The daemon handler holds the connection for the entire duration — one `QueryMissing` blocks all other clients on that daemon connection |
| **No caching across invocations** | Every `nix build` call re-walks the entire graph from scratch. The result is thrown away after use |
| **No substituter-level batching** | `queryPathInfo` is called one path at a time per substituter. Many substituters (like cache.nixos.org) support narinfo batch lookups, but `queryMissing` doesn't use them here |
| **ThreadPool parallelism is limited** | The thread pool only parallelizes checking multiple invalid outputs of the *same* derivation against substituters (`misc.cc:274-275`). Different derivations are processed sequentially by the `doPath` queue |

## A realised output, and a sibling that is not

**`queryMissing` reads every output of a derivation to decide
`knownOutputPaths`, and it ignores the outputs that the caller wants.** The
loop is at `misc.cc:217-225`:

```cpp
for (auto & [outputName, pathOpt] : queryPartialDerivationOutputMap(drvPath)) {
    if (!pathOpt) {
        knownOutputPaths = false;
        break;
    }
    if (bfd.outputs.contains(outputName) && !isValidPath(*pathOpt))
        invalid.insert(*pathOpt);
}
```

The `break` runs for an output that the caller did not ask for. The next line
reads `bfd.outputs`, and the substituter loop at `misc.cc:250` reads
`bfd.outputs` as well. So two of the three tests honour the wanted outputs and
the first one does not. The comment above the loop states the intent that the
code misses: "CA derivations for which we have a trust mapping for all wanted
outputs".

**A content-addressed derivation reaches this state after one ordinary
build.** `DerivationGoal` holds one `wantedOutput`, and it registers a
realisation for that output alone, at `derivation-goal.cc:228-236`. The build
of the resolved derivation makes every output, and Nix maps one of them back
to the original derivation.

### The measurement

`ca/content-addressed.nix` gives `rootCA` the outputs `out`, `dev` and `foo`.
This runs against Nix 2.34.8, with no daemon and no pynixd:

```console
$ nix build -f content-addressed.nix rootCA^out
$ sqlite3 db.sqlite 'select drvPath, outputName from Realisations'
sha256:e76acd40...|out            # the original rootCA.drv, one output
sha256:39850e18...|dev
sha256:39850e18...|foo            # the resolved derivation, all three
sha256:39850e18...|out

$ nix build -f content-addressed.nix rootCA^out --dry-run
this derivation will be built:
  .../gfzi77x6b81brl6b3lmpgmi2vrn6wp1f-rootCA.drv
```

The output `out` is realised, and its path is valid. Nix still names the
derivation. After `nix build -f content-addressed.nix 'rootCA^*'` registers
`dev` and `foo` as well, the same `--dry-run` command names nothing. So the
siblings are the cause.

### What pynixd does

`QueryMissingPlanGoal._classify_derivation` reads the wanted outputs alone,
through `selected_output_paths`. pynixd also registers a realisation for every
output that the build made, in `_register_realisations`, and not for the
wanted one alone. Each half makes the answer of pynixd complete where the
answer of Nix is not, so pynixd leaves `rootCA.drv` out of `willBuild` and
`nix-daemon` puts it in.

**pynixd keeps its answer.** Issue #203 holds the difference, and issue #191
holds the convention for a defect of Nix that pynixd copies. This is not one
of those: pynixd does not copy this defect.

`Lillecarl/nix#312` reports the defect, and it gives the correction: read
`bfd.outputs` in the first test as well, which makes the three tests of the
function agree.
