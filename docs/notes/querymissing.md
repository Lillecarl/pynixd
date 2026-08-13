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
