# pynixd in the nanopynix umbrella

`nanopynix` is becoming the umbrella repository for Python implementations of
the Nix stack: bindings, public APIs, protocol tooling, daemon services, and
command-line interfaces. `pynixd` is the daemon-service project in that
umbrella. Until a concrete merge plan is chosen, it remains an independently
buildable and releasable project.

## Ownership boundary

```text
nanopynix umbrella
├── nanopynix
│   ├── native bindings and in-process Nix access
│   ├── Python-facing Nix APIs
│   └── worker RPC protocol and clients
└── pynixd
    ├── Nix daemon-wire protocol implementation
    ├── daemon/proxy, store adapters, and server transports
    ├── scheduling, builds, substitution, and cache services
    └── the `pynixd` executable
```

The boundary is by responsibility, not by a claim that every Nix-shaped type
must immediately have one canonical implementation. In particular,
`pynixd.serde` models the daemon wire contract, while the `nanopynix` public
API and worker protocol serve different callers. Consolidation needs an
explicit compatibility decision for each shared concept, such as `StorePath`,
`DerivedPath`, `ValidPathInfo`, and build results.

## Compatibility contract during the transition

`pynixd` must continue to provide all of the following from its own checkout:

- the `pynixd` Python import package;
- the `pynixd` console command and `python -m pynixd` entry point;
- standalone Hatch and Nix builds; and
- Nix outputs named `pynixd`, `libpynixd`, and `pynixd-docs`.

The umbrella must consume these as an external project boundary at first. It
must not rely on relative imports into a sibling checkout, path-dependent test
configuration, or unversioned copies of daemon protocol models. Any direct
runtime dependency on `nanopynix` is a later, deliberate API decision with a
declared minimum version and an integration test in both repositories.

## Daemon protocol package

The standard daemon wire contract now lives in the `nix-daemon-protocol`
subproject, whose import package is `nix_daemon_protocol`. It has its own
`pyproject.toml`, source tree, focused tests, Nix derivation, and flake output.
It owns only codecs: protocol constants, transport-neutral reader/writer
interfaces, standard request and response models, structured Nix value types,
and stderr stream encoding. It has no runtime import of `pynixd`.

For malformed input, the package offers diagnostics without a mandatory logging
dependency: it uses structlog when available, otherwise standard logging, and
does not configure either. One outermost decode failure event is emitted before
the original exception is re-raised.

`pynixd.daemon_extensions` owns private operation codes and models, including
the pynixd collect-garbage and batch-query/probe operations. `pynixd.serde`
remains a temporary compatibility facade so existing pynixd users can migrate
without an import-rename flag day. New reusable callers should depend on the
`nix-daemon-protocol` distribution and import from `nix_daemon_protocol`
directly.

## Staged convergence

1. **Keep projects independently healthy.** Preserve the compatibility
   contract above and keep `pynixd`'s package, executable, documentation, and
   Nix outputs named explicitly.
2. **Add umbrella composition.** Let the `nanopynix` flake/CI compose a pinned
   `pynixd` input and run the existing package and focused protocol checks
   without changing imports or publishing names.
3. **Choose shared contracts one at a time.** For a candidate shared type,
   compare its wire semantics, ownership/lifetime model, and supported Nix
   versions. Publish an adapter or a versioned shared package only after that
   comparison; do not replace a wire type just because its name matches.
4. **Introduce integration at a service seam.** Prefer an explicit adapter
   between a `nanopynix` store/client API and a `pynixd` store or daemon
   service. The adapter belongs at the boundary; core daemon scheduling and
   protocol dispatch remain `pynixd` responsibilities.
5. **Decide physical layout last.** Once package ownership, releases, and CI
   are proven, the projects may remain sibling repositories under one umbrella,
   become subdirectories in a monorepo, or use a split-source arrangement.
   None of those layouts requires a Python import rename today.

## Integration acceptance checks

Each umbrella integration change should demonstrate all of these:

- `pynixd` still builds and runs from this checkout in isolation;
- `import pynixd` and the `pynixd` command remain available from its installed
  package;
- `nanopynix` and `pynixd` can be installed together without import or console
  command collisions; and
- any adapter is exercised against the Nix protocol versions supported by both
  projects, rather than only against in-memory test doubles.

This keeps the eventual merger reversible: composition is established before
code ownership is changed.
