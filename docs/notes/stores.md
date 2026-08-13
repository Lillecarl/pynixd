# `NIX_REMOTE`, `--store`, `--eval-store` Interaction

Source: Nix repo, current HEAD.

## Configuration Sources

| Source | What it sets | Code location |
|---|---|---|
| `NIX_REMOTE` env var | `settings.storeUri` (default: `"auto"`) | `src/libstore/include/nix/store/globals.hh:184` |
| `--store` CLI flag | `settings.storeUri` (overrides `NIX_REMOTE`) | `src/libmain/shared.cc:289` |
| `--eval-store` CLI flag | `evalStoreUrl` on `MixEvalArgs` (independent) | `src/libcmd/common-eval-args.cc:151` |

## How Stores Are Created

### Builder store (`--store` / `NIX_REMOTE`)

`StoreCommand::getStore()` → `StoreCommand::createStore()` → `StoreConfigCommand::createStoreConfig()` returns `resolveStoreConfig(settings.storeUri.get())` (`src/libcmd/command.cc:78`).

`resolveStoreConfig` in `src/libstore/store-registration.cc:26-76` handles the `"auto"` case:
1. If `$NIX_STATE_DIR` is writable → `LocalStore`
2. Else if `nixDaemonSocketFile` exists → `UDSRemoteStore` (connects to daemon)
3. Else on Linux, non-root, without overrides → chroot store in `~/.local/share/nix/root`
4. Else fallback → `LocalStore`

### Eval store (`--eval-store`)

`EvalCommand::getEvalStore()` (`src/libcmd/command.cc:159-163`):
```cpp
evalStore = evalStoreUrl ? openStore(StoreReference{*evalStoreUrl}) : getStore();
```
If `--eval-store` is absent, it reuses the same store as `--store`/`NIX_REMOTE`. If present, it opens a completely independent store connection.

## How `nix build` Wires Them Together

`CmdBuild` inherits: `InstallablesCommand` → `SourceExprCommand` → `MixFlakeOptions` → `EvalCommand` → `MixEvalArgs`

The `run()` method in `src/nix/build.cc:141` calls:
```cpp
Installable::build(getEvalStore(), store, Realise::Outputs, installables, ...);
```

This flows into `Installable::build2()` (`src/libcmd/installables.cc:600`) which calls `store->buildPathsWithResults(paths, bMode, evalStore)`.

### LocalStore path

`Store::buildPathsWithResults` (`src/libstore/build/entry-points.cc:49`) creates a `Worker(*this, evalStore ? *evalStore : *this)`. The `Worker` uses `worker.evalStore` throughout the build lifecycle:
- `worker.evalStore.readDerivation(path)` — resolving derivation metadata
- `worker.evalStore.queryPartialDerivationOutputMap(path)` — checking what outputs exist
- Copying input sources from eval store to build store (`derivation-building-goal.cc:81-91`)
- Checking validity and adding temp roots on eval store

### RemoteStore path

`RemoteStore::buildPathsWithResults` (`src/libstore/remote-store.cc:583`) first calls `copyDrvsFromEvalStore()` (`src/libstore/remote-store.cc:550-568`):

```cpp
void RemoteStore::copyDrvsFromEvalStore(const std::vector<DerivedPath> & paths,
                                         std::shared_ptr<Store> evalStore) {
    if (evalStore && evalStore.get() != this) {
        RealisedPath::Set drvPaths2;
        for (const auto & i : paths) {
            std::visit(overloaded{
                [&](const DerivedPath::Built & bp) { drvPaths2.insert(bp.drvPath->getBaseStorePath()); },
                [&](const DerivedPath::Opaque &) { /* assume already there */ },
            }, i.raw());
        }
        copyClosure(*evalStore, *this, drvPaths2);
    }
}
```

Then sends `WorkerProto::Op::BuildPathsWithResults` to the remote daemon with the derived paths. The remote daemon is expected to fetch or build those paths.

### Important caveats

| Store type | `--eval-store` support |
|---|---|
| `RemoteStore` (daemon) | Supported — copies `.drv` files before building |
| `LocalStore` | Supported — Worker uses eval store directly |
| `LegacySSHStore` | **Not supported** — throws error (`legacy-ssh-store.cc:217-218`) |
| `RestrictedStore` (sandbox) | **Not supported** — asserts `!evalStore` (`restricted-store.cc:266`) |

## Practical Use

```sh
# Default: everything on one daemon
NIX_REMOTE=daemon nix build .#myPackage

# Explicit equivalent
nix build --store daemon .#myPackage

# Split: eval on one daemon, build on another
nix build \
  --eval-store unix:///path/to/eval-daemon \
  --store unix:///path/to/builder-daemon

# Eval-store alone: eval daemon != build daemon
nix build \
  --eval-store unix:///path/to/eval-daemon \
  .#myPackage
```

When `--eval-store` and `--store` differ:
- Evaluation reads `.drv` files and source from the eval store
- Only the `.drv` closure (derivations + their direct inputs) is copied to the builder store via `copyClosure`
- Build execution and output storage happen on the builder store
- The eval store is consulted for derivation metadata during the build (output hashes, input derivation resolution)
- Source inputs not already in the builder store are copied there on demand (`derivation-building-goal.cc:81-91`)
