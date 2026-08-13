# Dynamic Derivation Modularization — Complete

## Result

The old `DynamicDerivationResolver` (renamed to `unknown_output_resolver.py`) and `CaRealisationManager` were replaced with two clean modules:

### New files

- **`pynixd/derivation_resolver.py`** (`DerivationResolver` class, ~260 lines): Unified pre-build resolver that handles CA realisation registration, deferred (inputDrv) resolution, and dynamic (DrvWithVersion) resolution via a single `resolve()` method. The two resolution paths share helpers for reading .drv files, collecting dep realisations, uploading resolved drvs to stores, and populating required_paths. Zero code duplication between deferred and dynamic paths.

- **`pynixd/trampoline.py`** (`Trampoline` class, ~200 lines): Extracted post-build lifecycle handler. Detects .drv outputs from dynamic builds, fires the trampoline (enqueues inner builds), rewires DAG dependencies via `link_dynamic_deps()`, and records results/failures back to `SchedulerBuildRequest`.

### Deleted files

- `pynixd/ca_realisation_manager.py` (78 lines) — absorbed into `DerivationResolver`
- `pynixd/unknown_output_resolver.py` (608 lines) — replaced by `DerivationResolver` + `Trampoline`

### Modified files

- `pynixd/scheduler.py` — updated imports, init (DerivationResolver + Trampoline), and 6 call sites
- `GLOSSARY.md` — updated Trampolining entry

### Net code reduction

- 686 lines deleted
- ~460 lines created
- **~226 lines net removed**, with much better separation of concerns and zero duplication

### Test status

- `just precommit`: all passes (pyright 0 errors, ruff clean, all 123 tests)
