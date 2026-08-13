"""Feature flags for the pynixd test suite.

Use ``TestFeatures`` to annotate tests with the features they cover.
Broad-scope tests run first; any test whose features are a subset of
already-verified features can be skipped at runtime (see conftest.py
for the subsumption hook).

Each member represents a single capability of the pynixd system or
the Nix daemon worker protocol.  Tests may cover multiple features.

Nix daemon operation opcodes are from ``WorkerProto::Op`` in
``nix/src/libstore/include/nix/store/worker-protocol.hh``.
"""

from __future__ import annotations

from enum import Flag, auto


class TestFeatures(Flag):
    """Granular feature flags covering pynixd's entire test surface.

    Categories:
    - DAEMON_PROTOCOL:  Every Nix daemon worker operation (opcodes 1-47+)
    - CONTENT_TYPES:    Derivation types (regular, CA fixed/floating, dynamic, etc.)
    - GOAL:             pynixd's internal DAG-based goal execution system
    - STORE:            Store implementations (local, SSH, HTTP, reverse, etc.)
    - SERVER:           Server features (auth, delegation, session, extensions)
    - SUBSTITUTION:     Fetching store paths from remote substituters
    - GARBAGE_COLLECT:  GC operations
    - INTERNAL:         Parsers, encoders, and utilities
    - PYNIXD_SPECIFIC:  Operations unique to pynixd (not in stock Nix protocol)
    - BENCHMARK:        Performance and stress tests
    """

    # ═══════════════════════════════════════════════════════════
    # Nix daemon protocol — WorkerProto::Op (all 40+ operations)
    # ═══════════════════════════════════════════════════════════

    # IS_VALID_PATH (op 1): Check whether a store path is valid (exists in store).
    # The most basic store query.  Used by the goal system's dependency-available? gate
    # and by resolution to decide whether to fall back to in-memory outputs.
    IS_VALID_PATH = auto()
    # QUERY_REFERRERS (op 6): Find all store paths that reference a given path.
    # Used by GC root computation and reverse-dependency tracking.
    QUERY_REFERRERS = auto()
    # ADD_TO_STORE (op 7): Ingest a store path via NAR protocol (deprecated).
    # The older NAR-based ingestion — superseded by ADD_TO_STORE_NAR in protocol >= 1.25.
    ADD_TO_STORE = auto()
    # ADD_TEXT_TO_STORE (op 8): Ingest text/literal file content (obsolete since Nix 3.0).
    # Replaced by ADD_TO_STORE for all content types.
    ADD_TEXT_TO_STORE = auto()
    # BUILD_PATHS (op 9): High-level build request for multiple derivations.
    # pynixd decomposes this into per-derivation BUILD_DERIVATION calls via the goal
    # system.  Returns a flat list of status codes.
    BUILD_PATHS = auto()
    # ENSURE_PATH (op 10): Ensure a store path exists (substitute or build it).
    # Used by `nix copy` and substitution fallback paths.
    ENSURE_PATH = auto()
    # ADD_TEMP_ROOT (op 11): Add a temporary GC root for a store path.
    # Protects paths from garbage collection during a session.
    ADD_TEMP_ROOT = auto()
    # ADD_INDIRECT_ROOT (op 12): Register an indirect GC root.
    # Creates a symlink in /nix/var/nix/gcroots/auto pointing to a store path.
    # No-op for unprivileged users in pynixd.
    ADD_INDIRECT_ROOT = auto()
    # SYNC_WITH_GC (op 13): Flush pending GC root registrations to disk.
    # Ensures the GC sees all roots before deciding what to delete.
    SYNC_WITH_GC = auto()
    # FIND_ROOTS (op 14): Enumerate all GC roots (both permanent and temporary).
    # Used by the GC to compute the live set of store paths.
    FIND_ROOTS = auto()
    # QUERY_DERIVER (op 18, obsolete): Query which derivation produced a store path.
    # OBSOLETE — replaced by QUERY_PATH_INFO which includes the deriver field.
    QUERY_DERIVER = auto()
    # SET_OPTIONS (op 19): Update daemon settings at runtime.
    # Accepts key=value option pairs.  No-op in pynixd for regular users;
    # logged via LogNext for transparency.
    SET_OPTIONS = auto()
    # COLLECT_GARBAGE (op 20): Run garbage collection — delete paths not reachable
    # from any GC root.  Supports min-age and max-freed bounds.
    COLLECT_GARBAGE = auto()  # Stock Nix collect-garbage (op 20)
    # QUERY_SUBSTITUTABLE_PATH_INFO (op 21): Query whether a single path is
    # available from substituters.  Returns nar info + hash.
    QUERY_SUBSTITUTABLE_PATH_INFO = auto()
    # QUERY_DERIVATION_OUTPUTS (op 22, obsolete): Query output names for a derivation.
    # OBSOLETE — replaced by QUERY_DERIVATION_OUTPUT_MAP (op 41).
    QUERY_DERIVATION_OUTPUTS = auto()
    # QUERY_ALL_VALID_PATHS (op 23): List every valid (registered) store path.
    # Used for full store enumeration.
    QUERY_ALL_VALID_PATHS = auto()
    # QUERY_PATH_INFO (op 26): Query metadata for a single store path.
    # Returns nar hash, nar size, references, deriver, registration time,
    # ultimate flag, and signatures.  The most-used query operation.
    QUERY_PATH_INFO = auto()
    # QUERY_DERIVATION_OUTPUT_NAMES (op 28, obsolete): Query just the output names
    # of a derivation (without mapping to store paths).  OBSOLETE.
    QUERY_DERIVATION_OUTPUT_NAMES = auto()
    # QUERY_PATH_FROM_HASH_PART (op 29): Find store paths matching a partial hash.
    # Returns all paths whose hash starts with the given prefix.
    QUERY_PATH_FROM_HASH_PART = auto()
    # QUERY_SUBSTITUTABLE_PATH_INFOS (op 30): Batch query for substitutable path
    # metadata.  Returns nar info + hashes for multiple paths at once.
    QUERY_SUBSTITUTABLE_PATH_INFOS = auto()
    # QUERY_VALID_PATHS (op 31): Batch-check which of a list of paths are valid.
    # Used by the goal system to determine which inputs are already available.
    QUERY_VALID_PATHS = auto()
    # QUERY_SUBSTITUTABLE_PATHS (op 32): Given a set of paths, return the subset
    # that are available from substituters.  Used for build planning.
    QUERY_SUBSTITUTABLE_PATHS = auto()
    # QUERY_VALID_DERIVERS (op 33): For a given output path, find which derivations
    # could have produced it (by matching the output store path).
    QUERY_VALID_DERIVERS = auto()
    # OPTIMISE_STORE (op 34): Deduplicate identical store paths by replacing
    # copies with hard links.  Scans the whole store for matching content.
    OPTIMISE_STORE = auto()
    # VERIFY_STORE (op 35): Verify store integrity — rehash every path and
    # check for corruption.  Reports mismatches.
    VERIFY_STORE = auto()
    # BUILD_DERIVATION (op 36): The core single-derivation build primitive.
    # Sends a full BasicDerivation (parsed ATerm) over the wire, NOT by reading
    # from the .drv file at drvPath (which is bookkeeping only).  The daemon
    # deserialises the struct fields directly.  Used by both regular and CA builds.
    BUILD_DERIVATION = auto()
    # ADD_SIGNATURES (op 37): Attach signatures to an existing store path's
    # ValidPathInfo.  Used by binary cache import workflows.
    ADD_SIGNATURES = auto()
    # NAR_FROM_PATH (op 38): Stream the NAR serialisation of a store path.
    # Used by substitution (`nix copy --from`), HTTP cache serving,
    # and verification processes.
    NAR_FROM_PATH = auto()
    # ADD_TO_STORE_NAR (op 39): Ingest a NAR file into the store (successor
    # to ADD_TO_STORE).  Accepts a NAR stream, registers the path with
    # references and hash.  Used by `nix copy --to` and trampoline ingestion.
    ADD_TO_STORE_NAR = auto()
    # QUERY_MISSING (op 40): Given a list of paths, report which need building
    # and which can be substituted.  Used by `nix build`'s planner.
    # NOT YET IMPLEMENTED in pynixd (both Nix and pynixd raise errors here
    # for dynamic derivation chains).
    QUERY_MISSING = auto()
    # QUERY_DERIVATION_OUTPUT_MAP (op 41): Map derivation output names to their
    # realised store paths.  Handles both regular (input-addressed) and CA outputs.
    # For CA derivations, consults the realisations table.
    QUERY_DERIVATION_OUTPUT_MAP = auto()
    # REGISTER_DRV_OUTPUT (op 42): Register a realisation for a CA derivation.
    # Associates a (hash modulo, output name) key with the actual output path
    # after build.  Required before QUERY_REALISATION can succeed.
    REGISTER_DRV_OUTPUT = auto()
    # QUERY_REALISATION (op 43): Look up the realised output path for a CA
    # derivation output by (hash modulo, output name).  Inverse of REGISTER_DRV_OUTPUT.
    QUERY_REALISATION = auto()
    # ADD_MULTIPLE_TO_STORE (op 44): Batch-add multiple store paths in a single
    # protocol round-trip.  Used by `nix copy --to` for many paths at once.
    ADD_MULTIPLE_TO_STORE = auto()
    # ADD_BUILD_LOG (op 45): Attach a build log to a derivation output path.
    # Used by CI systems and `nix log` to retrieve build output.
    ADD_BUILD_LOG = auto()
    # BUILD_PATHS_WITH_RESULTS (op 46): Like BUILD_PATHS but returns per-derivation
    # results (status, output paths, error message).  Used by nix's modern build UI.
    BUILD_PATHS_WITH_RESULTS = auto()
    # ADD_PERM_ROOT (op 47): Add a permanent GC root.
    # Creates a named symlink in the gcroot directory.  No-op for unprivileged
    # users in pynixd; logged via LogNext for transparency.
    ADD_PERM_ROOT = auto()

    # ═══════════════════════════════════════════════════════════
    # Pynixd-specific operations (not in stock Nix protocol)
    # ═══════════════════════════════════════════════════════════

    # PYNIXD_COLLECT_GARBAGE: pynixd's extended GC operation with per-store
    # granularity and progress reporting beyond stock Nix's COLLECT_GARBAGE.
    PYNIXD_COLLECT_GARBAGE = auto()
    # PROBE_SYSTEMS: Probe the system's supported platforms and features.
    # Used at startup to build the feature matrix.  Not part of Nix's protocol.
    PROBE_SYSTEMS = auto()
    # PROBE_FEATURES: Probe specific feature flags from the daemon.
    # Used for capability negotiation beyond the stock handshake.
    PROBE_FEATURES = auto()
    # QUERY_CLOSURE: Compute the transitive closure (all reachable store paths)
    # from a starting set.  Used by copy operations to determine what to transfer.
    QUERY_CLOSURE = auto()
    # QUERY_CLOSURE_WITH_INFO: Like QUERY_CLOSURE but returns path info for
    # every member of the closure.  Used by copy operations for batch metadata.
    QUERY_CLOSURE_WITH_INFO = auto()
    # QUERY_PATH_INFOS: Batch QUERY_PATH_INFO for multiple paths.
    # More efficient than N sequential QUERY_PATH_INFO calls.
    QUERY_PATH_INFOS = auto()
    # QUERY_DERIVATION_OUTPUT_MAP_BATCH: Batch QUERY_DERIVATION_OUTPUT_MAP for
    # multiple derivations.  Used by the goal system for bulk resolution.
    QUERY_DERIVATION_OUTPUT_MAP_BATCH = auto()
    # SIGN_PATH_INFO: Attach cryptographic signatures to ValidPathInfo structs.
    # Used for binary cache signing workflows.
    SIGN_PATH_INFO = auto()
    # IS_VALID_PATH (pynixd wrapped): pynixd wraps Nix's IsValidPath (op 1)
    # as a standalone operation class for use within the goal system.
    IS_VALID_PATH_PYNIXD = auto()
    # QUERY_SUBST_PATH_INFO (pynixd single): Single-path variant of
    # QUERY_SUBSTITUTABLE_PATH_INFOS, used by the substitution manager
    # for individual path lookups.
    QUERY_SUBST_PATH_INFO = auto()

    # ═══════════════════════════════════════════════════════════
    # Nix store content / derivation types
    # ═══════════════════════════════════════════════════════════

    # REGULAR: Standard input-addressed derivation (output path known at eval time).
    # The .drv contains concrete output paths in its outputs list.  No CA semantics.
    REGULAR = auto()
    # FIXED_OUTPUT: Fixed-output derivation (outputHash declared upfront).
    # The daemon builds and verifies the hash matches.  The .drv carries an
    # expected output path derived from the declared hash.
    FIXED_OUTPUT = auto()
    # CA_FLOATING: Content-addressed derivation with outputHash="" (floating).
    # Output path is NOT known at eval time — computed at build time via
    # hashDerivationModulo.  Requires REGISTER_DRV_OUTPUT + QUERY_REALISATION.
    CA_FLOATING = auto()
    # CA_FIXED: Content-addressed derivation with outputHash set (fixed).
    # Like FIXED_OUTPUT but under CA semantics (__contentAddressed=true).
    # Output path IS known at eval time, but the daemon registers a realisation.
    CA_FIXED = auto()
    # CA_TEXT_HASHED: Content-addressed derivation with outputHashMode="text".
    # Output path is derived from the hash of file content (not a NAR hash).
    # Used for .drv files and other text-like outputs in CA builds.
    CA_TEXT_HASHED = auto()
    # CA_MULTI_OUTPUT: CA derivation with multiple named outputs (e.g., out, dev).
    # Exercises multi-output realisation resolution.
    CA_MULTI_OUTPUT = auto()
    # CA_DEPENDS_ON_CA: A CA derivation whose build script references another CA
    # derivation's output.  Exercises cross-CA reference resolution in the daemon.
    CA_DEPENDS_ON_CA = auto()
    # DEFERRED: Non-CA derivation that takes one or more CA derivations as inputs.
    # The .drv has path="" and hash="" for those outputs — they must be resolved
    # at build time via hashDerivationModulo → register → query → rewrite.
    DEFERRED = auto()
    # TEXT_OUTPUT: Derivation that produces a simple text file (most common case).
    TEXT_OUTPUT = auto()
    # MULTI_OUTPUT: Derivation with multiple named outputs (non-CA variant).
    # Exercises per-output build requests and result routing.
    MULTI_OUTPUT = auto()
    # BIG_OUTPUT: Store path with large content (10MB+).  Exercises streaming,
    # NAR handling, and transfer at scale.
    BIG_OUTPUT = auto()
    # SYMLINK_OUTPUT: Store path containing symlinks.  Exercises NAR symlink
    # node handling and filesystem traversal.
    SYMLINK_OUTPUT = auto()
    # DIRECTORY_OUTPUT: Store path with nested directory structure.  Exercises
    # recursive NAR traversal and directory node handling.
    DIRECTORY_OUTPUT = auto()
    # DAG_BUILD: A DAG-shaped dependency tree (fan-in/fan-out).  Exercises the
    # scheduler's ability to parallelise independent branches and serialise
    # dependent ones.
    DAG_BUILD = auto()
    # PARALLEL_BUILD: Many independent derivations built concurrently.
    # Stress-tests the goal system's parallelism and resource management.
    PARALLEL_BUILD = auto()
    # SUBSTITUTABLE: A derivation whose output exists on a binary cache
    # (substitutable).  Tests the substitution fallback path.
    SUBSTITUTABLE = auto()

    # ═══════════════════════════════════════════════════════════
    # Dynamic derivation features (builtins.outputOf chains)
    # ═══════════════════════════════════════════════════════════

    # DYN_PRODUCING_DRV: CA builder that copies a .drv ATerm into its output.
    # This is the "producer" step — the output IS a .drv file, not a regular
    # artifact.  The .drv content is later read by the DynamicBuildGoal.
    DYN_PRODUCING_DRV = auto()
    # DYN_RESOLVE_INNER_DRV: Reading .drv content from a CA output and wrapping
    # it as a DynamicBuildGoal.  Exercises _resolve_drv_target and chain-derived
    # path detection (no .drv extension heuristics).
    DYN_RESOLVE_INNER_DRV = auto()
    # DYN_CHILD_MAP: Recursive ChildMapNode parsing from the .drv ATerm's
    # dynamic_input_drvs field.  Exercises _parse_child_map_node and the
    # (flat_outs, [(child_name, child_node), ...]) format.
    DYN_CHILD_MAP = auto()
    # DYN_CHAIN: A derivation with multiple levels of nested builtins.outputOf
    # (e.g., outputOf(outputOf(producer, "out"), "out")).  Exercises the
    # ChildMapNode → DerivedPath chain conversion via _child_map_to_paths.
    DYN_CHAIN = auto()
    # DYN_DEEP: 5+ levels of deep dynamic chain.  Exercises the deepest nesting
    # pynixd can produce, with multi-level unknownDerivation hashing in
    # _resolve_dynamic_node for DownstreamPlaceholder computation.
    DYN_DEEP = auto()
    # DYN_PLACEHOLDER_CHAIN: Computing the correct DownstreamPlaceholder chain
    # for N-level dynamic derivations, using iterated unknownDerivation hashing.
    # Each level wraps the previous placeholder in another unknownDerivation layer.
    DYN_PLACEHOLDER_CHAIN = auto()
    # DYN_MIXED_DEPS: A single .drv with regular + CA + dynamic inputs simultaneously
    # (multiple dynamic_input_drvs entries at different chain depths).  Exercises
    # the heterogeneous dependency resolver.
    DYN_MIXED_DEPS = auto()
    # DYN_DEFERRED_PLUS: A deferred derivation that also has dynamic inputs.
    # Boundary case for _resolve_deferred with mixed input_drvs + dynamic_input_drvs.
    # Requires the trampoline pattern to work around .drv extension constraints.
    DYN_DEFERRED_PLUS = auto()
    # DYN_NAR_ROUNDTRIP: Fetch a .drv file via NAR_FROM_PATH, parse the NAR,
    # extract file content, and compare byte-for-byte with the original filesystem
    # file.  Verifies NAR protocol accuracy for derivation files.
    DYN_NAR_ROUNDTRIP = auto()
    # DYN_OUTPUT_OF: The builtins.outputOf builtin itself — the Nix language
    # primitive that creates SingleDerivedPath::Built references with nested
    # chains.  Any test using outputOf exercises this.
    DYN_OUTPUT_OF = auto()
    # DYN_WRAPPER_BUILD: The full dynamic derivation chain end-to-end:
    # build producingDrv → read inner .drv → build inner → build wrapper.
    # The canonical "hello world" of dynamic derivations.
    DYN_WRAPPER_BUILD = auto()

    # ═══════════════════════════════════════════════════════════
    # Goal system (pynixd's DAG-based build orchestrator)
    # ═══════════════════════════════════════════════════════════

    # GOAL_BUILD: A single BuildGoal that delegates to the daemon via
    # BUILD_DERIVATION.  Covers dependency resolution, output registration,
    # and result collection.
    GOAL_BUILD = auto()
    # GOAL_OPAQUE: A PathSubstitutionGoal that handles substitution of already-known
    # paths.  Creates sub-goals for references, then substitutes the path.
    # Used for inputs already in the store or needing remote fetch.
    GOAL_OPAQUE = auto()
    # GOAL_RESOLUTION: A ResolutionGoal that resolves deferred CA-output paths.
    # Computes hashDerivationModulo, queries realisations via QUERY_REALISATION,
    # rewrites placeholders with actual paths, and fills in output paths via
    # makeOutputPath.
    GOAL_RESOLUTION = auto()
    # GOAL_DYNAMIC: A DynamicBuildGoal that handles one link in a dynamic chain.
    # Builds the producer, reads the inner .drv content, creates sub-goals
    # recursively for the remaining chain.  Falls back gracefully when the
    # chain collapses to a non-.drv final output.
    GOAL_DYNAMIC = auto()
    # GOAL_DEFERRED: ResolutionGoal for deferred (non-CA with CA-input) derivations.
    # Flows through compute_storepath → hashDerivationModulo → realisations
    # → placeholder rewrite → compute_storepath (final) pipeline.
    GOAL_DEFERRED = auto()
    # GOAL_DAG: The goal system's DAG scheduler.  Handles topological ordering,
    # fan-in/fan-out parallelism, dependency propagation, and cancellation.
    GOAL_DAG = auto()
    # GOAL_BUILD_QUEUE: The global BuildQueue that manages per-derivation build
    # tasks via asyncio.  Handles build lifecycle, output ingestion, and result
    # aggregation.  Builds survive client disconnects.
    GOAL_BUILD_QUEUE = auto()
    # GOAL_MANAGER: The GoalManager that caches goal results (BuildResult,
    # ResolutionResult) and prevents redundant building.  Shares resolved
    # outputs across derivations that share inputs.
    GOAL_MANAGER = auto()
    # GOAL_PATH_MAP: DynamicPathMap tracking (drv_path, *chain_output_names) →
    # actual store path.  Used by DynamicBuildGoal to compute chains and by
    # resolution to answer "what is the actual path for this chain?"
    GOAL_PATH_MAP = auto()
    # GOAL_TRAMPOLINE: The trampoline pattern — when a CA output contains a .drv
    # ATerm with a wrong name, register it under the correct name via
    # ADD_TO_STORE_NAR and then build it.  Needed for DYN_DEFERRED_PLUS.
    GOAL_TRAMPOLINE = auto()
    # GOAL_SCHEDULER: The scheduler that manages goal creation and lifecycle.
    # Coordinates between BuildQueue, GoalManager, and individual goals.
    GOAL_SCHEDULER = auto()

    # ═══════════════════════════════════════════════════════════
    # Store implementations
    # ═══════════════════════════════════════════════════════════

    # STORE_LOCAL: LocalSocketStore — connects to a real Nix daemon via Unix
    # socket.  The primary store backend used by almost all functional tests.
    STORE_LOCAL = auto()
    # STORE_SSH: SSH store protocol (ssh-ng).  Exercises the SSH-ng wire format,
    # connection pooling, channel multiplexing, and remote build delegation.
    # Requires the daemon listening on a TCP port with SSH access configured.
    STORE_SSH = auto()
    # STORE_UNIX: Unix socket protocol used by the session bridge server.
    # Enables unprivileged client access to the daemon through pynixd's
    # unix-paired endpoint.
    STORE_UNIX = auto()
    # STORE_REVERSE: Reverse store — builds requested by an external builder
    # write outputs into pynixd's store.  Used by the build delegation feature
    # where a remote builder sends results back.
    STORE_REVERSE = auto()
    # STORE_DELEGATOR: DaemonDelegatorStore — a store proxy that forwards
    # protocol operations to the system Nix daemon.  Used as a fallback store
    # by the session bridge when no local store is configured.
    STORE_DELEGATOR = auto()
    # STORE_POOL: Connection pool for store backends.  Manages connection
    # lifecycle, dirty/clean state tracking, and transparent reconnection.
    STORE_POOL = auto()
    # STORE_HTTP_BINARY_CACHE: HTTP binary cache (read) — fetches store paths
    # via HTTPS with .narinfo lookup + NAR streaming.  Used as a substituter
    # in conjunction with the SubstitutionManager.
    STORE_HTTP_BINARY_CACHE = auto()
    # STORE_HTTP_BINARY_CACHE_WRITE: HTTP binary cache (write) — uploads store
    # paths via PUT .narinfo + PUT nar requests.  The write side of cache
    # population for publishing.
    STORE_HTTP_BINARY_CACHE_WRITE = auto()

    # ═══════════════════════════════════════════════════════════
    # Server features
    # ═══════════════════════════════════════════════════════════

    # SERVER_SSH: SSH server mode — pynixd listens for ssh-ng connections.
    # Exercises the full SSH protocol stack including authentication,
    # channel setup, and command multiplexing.
    SERVER_SSH = auto()
    # SERVER_HTTP: HTTP binary cache server — pynixd serves as a Nix binary
    # cache over HTTP.  Handles .narinfo queries and NAR downloads.
    SERVER_HTTP = auto()
    # SERVER_HTTP_AUTH: HTTP server with htpasswd authentication (Basic auth).
    # Exercises RBAC-gated read access to the binary cache.
    SERVER_HTTP_AUTH = auto()
    # SERVER_HTTP_UPLOAD: HTTP server with upload support (PUT /nix-cache-info,
    # PUT .nar, PUT .narinfo).  The write side of the HTTP server.
    SERVER_HTTP_UPLOAD = auto()
    # SERVER_UNIX: Unix socket server — pynixd listens for local clients via
    # a Unix domain socket.  Used by the session bridge for unprivileged access.
    SERVER_UNIX = auto()
    # SERVER_SESSION_BRIDGE: Session bridge server — a low-privilege server
    # that delegates protocol operations to a full Nix daemon via
    # DaemonDelegatorStore.  Enables unprivileged client builds.
    SERVER_SESSION_BRIDGE = auto()
    # SERVER_RBAC: Role-based access control — admin vs regular-user operation
    # gating.  Privileged operations (SET_OPTIONS, ADD_PERM_ROOT, etc.) are
    # no-ops for non-admin users and produce transparency logs.
    SERVER_RBAC = auto()
    # SERVER_FEATURE_PROBE: System feature probing — pynixd probes the daemon
    # at startup to discover supported features (CA derivations, dynamic
    # derivations, recursive nix, etc.) and populates the feature matrix.
    SERVER_FEATURE_PROBE = auto()
    # SERVER_HANDSHAKE: Daemon protocol version negotiation and feature exchange
    # at connection time.  Ensures client and server agree on wire format,
    # protocol version, and capability set.
    SERVER_HANDSHAKE = auto()
    # SERVER_KUBERNETES_API: Kubernetes custom resource API — manage pynixd
    # stores as Kubernetes Custom Resources
    # (pynixdstores.cd.pynixd.io).
    SERVER_KUBERNETES_API = auto()
    # SERVER_SFTP: SFTP server — expose store paths over SFTP for external
    # file system mounting and debugging.
    SERVER_SFTP = auto()
    # SERVER_BUILD_LOG_PUBSUB: Real-time build log streaming via pub/sub channels.
    # Clients receive build output as it's produced, without polling.
    SERVER_BUILD_LOG_PUBSUB = auto()
    # SERVER_PSI_GATING: Pressure Stall Information-based admission control.
    # Reject builds when system CPU, IO, or memory pressure exceeds configured
    # thresholds.  Prevents system thrashing.
    SERVER_PSI_GATING = auto()
    # SERVER_PARAM_LOGS: Per-test parameterised log file output.  Each test
    # gets its own log file with relative timestamps, custom formatting,
    # and test-specific filtering.
    SERVER_PARAM_LOGS = auto()

    # ═══════════════════════════════════════════════════════════
    # Substitution / binary cache reads
    # ═══════════════════════════════════════════════════════════

    # COPY_SINGLE: Copy a single store path from one store to another.
    # Exercises the full copy pipeline: QUERY_CLOSURE → NAR_FROM_PATH → ADD_TO_STORE_NAR
    # for one store path.
    COPY_SINGLE = auto()
    # COPY_MULTIPLE: Bulk-copy many store paths in a single `nix copy` invocation.
    # Exercises batch closure computation, parallel NAR streaming, and
    # ADD_MULTIPLE_TO_STORE ingestion.
    COPY_MULTIPLE = auto()

    # SUBSTITUTE: General substitution — fetching store paths from any remote
    # substituter.  Exercises the SubstitutionManager's fallback chain
    # and path verification.
    SUBSTITUTE = auto()
    # SUBSTITUTE_HTTP: Substitution from an HTTP binary cache (cache.nixos.org
    # or a pynixd HTTP server).  Covers .narinfo parsing, nar download,
    # integrity verification, and path registration.
    SUBSTITUTE_HTTP = auto()
    # SUBSTITUTE_HTTP_AUTH: Substitution from an authenticated HTTP binary cache
    # (Basic auth).  Exercises credential management and auth header passing
    # in the substituter pipeline.
    SUBSTITUTE_HTTP_AUTH = auto()
    # SUBSTITUTABLE_PATH_INFO: The SubstitutablePathInfo query — check whether
    # a set of paths is available from substituters without downloading them.
    # Used for build planning.
    SUBSTITUTABLE_PATH_INFO = auto()
    # SUBSTITUTION_MANAGER: The SubstitutionManager orchestrates multiple
    # substituters in priority order, manages download parallelism, caches
    # results, and handles errors.
    SUBSTITUTION_MANAGER = auto()

    # ═══════════════════════════════════════════════════════════
    # Garbage collection
    # ═══════════════════════════════════════════════════════════

    # GC_COLLECT: General garbage collection — delete store paths not reachable
    # from any GC root.  Covers root computation, live set scanning, and deletion.
    GC_COLLECT = auto()
    # GC_MIN_AGE: GC with minimum age threshold — keep paths younger than
    # N seconds.  Exercises the age-based retention logic.
    GC_MIN_AGE = auto()
    # GC_MAX_FREED: GC with a maximum freeing limit — stop after freeing
    # N bytes.  Exercises incremental GC bounds and early termination.
    GC_MAX_FREED = auto()
    # GC_GLOBAL: GC with global root computation — identify roots across all
    # connected stores and compute the combined live set.
    GC_GLOBAL = auto()
    # GC_FIND_ROOTS: The FIND_ROOTS operation as used by the GC pipeline.
    GC_FIND_ROOTS = auto()

    # ═══════════════════════════════════════════════════════════
    # Internal correctness (parsers, encoders, utilities)
    # ═══════════════════════════════════════════════════════════

    # WIRE_ENCODE: Wire protocol encoding — serialise pynixd types to the
    # Nix daemon binary wire format (varints, length-prefixed strings,
    # store paths, sets, maps, optional fields).
    WIRE_ENCODE = auto()
    # WIRE_DECODE: Wire protocol decoding — deserialise daemon responses
    # back into pynixd types from the binary wire format.
    WIRE_DECODE = auto()
    # DRV_PARSE: .drv ATerm parsing — parse the canonical Derive("...", ...)
    # ATerm format from Nix into pynixd's Derivation dataclass.  Covers all
    # fields: outputs, inputDrvs, inputSrcs, system, builder, args, env.
    DRV_PARSE = auto()
    # DRV_SERIALIZE: .drv ATerm serialisation — produce the canonical
    # Derive("...", ...) ATerm string from a Derivation dataclass.  Used by
    # compute_storepath and hashDerivationModulo algorithms.
    DRV_SERIALIZE = auto()
    # DRV_HASH_DERIVATION_MODULO: The hashDerivationModulo algorithm — compute
    # the CA derivation fingerprint.  Masks input-addressed output paths with
    # "000...000" for determinism.  Critical for CA floating and deferred builds.
    DRV_HASH_DERIVATION_MODULO = auto()
    # DRV_COMPUTE_STOREPATH: The compute_storepath algorithm (TextInfo) — derive
    # the .drv's own store path from its ATerm content.  Used by the goal
    # system to compute resolved .drv paths without writing to filesystem.
    DRV_COMPUTE_STOREPATH = auto()
    # DERIVED_PATH: The DerivedPath type — parsing and serialisation of
    # derived path references (e.g., /nix/store/xxx!out!out!out for chains).
    # Includes the DerivationPath component for drv-only references.
    DERIVED_PATH = auto()
    # STORE_PATH_ENCODE: StorePath encoding and decoding — the base32 store
    # path hash format (/nix/store/<hash>-<name>).
    STORE_PATH_ENCODE = auto()
    # UTILS_NIX32: The nix32 encoding/decoding utilities — the custom base-32
    # character set used in Nix store path hashes.
    UTILS_NIX32 = auto()
    # UTILS_CRYPTO: Cryptographic helpers (compress_hash, hash prefixing,
    # hex/base32 conversions).
    UTILS_CRYPTO = auto()
    # NAR_PARSE: NAR (Nix ARchive) format parsing — parse the Nix Archive
    # format into pynixd's node types (NarRegular, NarSymlink, NarDirectory).
    NAR_PARSE = auto()
    # NAR_SERIALIZE: NAR file format serialisation — produce canonical NAR
    # bytes from pynixd's in-memory node representation.
    NAR_SERIALIZE = auto()
    # NAR_STREAM: NAR streaming verification — verify that large NARs can be
    # streamed between client and server without full buffering.
    NAR_STREAM = auto()

    # ═══════════════════════════════════════════════════════════
    # Build metadata & types
    # ═══════════════════════════════════════════════════════════

    # BUILD_TYPES: The BuildResult, BuiltOutput, KeyedBuildResult, and related
    # types — serialisation, deserialisation, and construction of build status
    # structures returned by BUILD_PATHS_WITH_RESULTS.
    BUILD_TYPES = auto()
    # PATH_INFO: The ValidPathInfo and SubstitutablePathInfo types — nar hash,
    # references, deriver, signatures, registration time.
    PATH_INFO = auto()
    # SIGNING: Store path signing and signature verification.  Used by binary
    # cache import/export and trusted-path workflows.
    SIGNING = auto()
    # PERSISTENCE: Store metadata persistence — sqlite-backed store info tables,
    # realisations table, valid paths, GC roots.  Exercises store restart and
    # reuse across server lifecycles.
    PERSISTENCE = auto()

    # ═══════════════════════════════════════════════════════════
    # Extension & delegation
    # ═══════════════════════════════════════════════════════════

    # EXTENSION_BUILD: Build extension — a Systemd service that dispatches
    # BUILD_DERIVATION to a configured builder.  Exercises the pynixd
    # extension API for custom build execution.
    EXTENSION_BUILD = auto()
    # EXTENSION_DELEGATION: General extension delegation — forwarding protocol
    # operations from a user-facing store to an extension store.  The core
    # extensibility mechanism of pynixd's store architecture.
    EXTENSION_DELEGATION = auto()

    # ═══════════════════════════════════════════════════════════
    # Benchmark / stress
    # ═══════════════════════════════════════════════════════════

    # BENCHMARK_PARALLEL: Parallel build benchmark — build many derivations
    # concurrently (100+ leaves) and measure throughput under load.
    BENCHMARK_PARALLEL = auto()
    # BENCHMARK_DEEP: Deep dependency chain benchmark — build a long linear
    # chain and measure sequential dependency overhead.
    BENCHMARK_DEEP = auto()
    # BENCHMARK_LARGE_FILE: Large file benchmark — build and copy store paths
    # of 100MB+ to test streaming and transfer throughput.
    BENCHMARK_LARGE_FILE = auto()

    # ═══════════════════════════════════════════════════════════
    # Common groupings (composite masks for convenient markers)
    # ═══════════════════════════════════════════════════════════

    # All daemon protocol operations that perform builds.
    BUILD_ALL = BUILD_DERIVATION | BUILD_PATHS | BUILD_PATHS_WITH_RESULTS | ENSURE_PATH
    # All protocol operations that ingest/store content.
    INGEST_ALL = (
        ADD_TO_STORE | ADD_TEXT_TO_STORE | ADD_TO_STORE_NAR | ADD_MULTIPLE_TO_STORE | ADD_BUILD_LOG | ADD_SIGNATURES
    )
    # All protocol operations for GC roots.
    ROOT_ALL = ADD_TEMP_ROOT | ADD_PERM_ROOT | ADD_INDIRECT_ROOT | SYNC_WITH_GC | FIND_ROOTS
    # All stock Nix daemon protocol operations (every WorkerProto::Op).
    PROTOCOL_ALL = (
        IS_VALID_PATH
        | QUERY_REFERRERS
        | ADD_TO_STORE
        | ADD_TEXT_TO_STORE
        | BUILD_PATHS
        | ENSURE_PATH
        | ADD_TEMP_ROOT
        | ADD_INDIRECT_ROOT
        | SYNC_WITH_GC
        | FIND_ROOTS
        | QUERY_DERIVER
        | SET_OPTIONS
        | COLLECT_GARBAGE
        | QUERY_SUBSTITUTABLE_PATH_INFO
        | QUERY_DERIVATION_OUTPUTS
        | QUERY_ALL_VALID_PATHS
        | QUERY_PATH_INFO
        | QUERY_DERIVATION_OUTPUT_NAMES
        | QUERY_PATH_FROM_HASH_PART
        | QUERY_SUBSTITUTABLE_PATH_INFOS
        | QUERY_VALID_PATHS
        | QUERY_SUBSTITUTABLE_PATHS
        | QUERY_VALID_DERIVERS
        | OPTIMISE_STORE
        | VERIFY_STORE
        | BUILD_DERIVATION
        | ADD_SIGNATURES
        | NAR_FROM_PATH
        | ADD_TO_STORE_NAR
        | QUERY_MISSING
        | QUERY_DERIVATION_OUTPUT_MAP
        | REGISTER_DRV_OUTPUT
        | QUERY_REALISATION
        | ADD_MULTIPLE_TO_STORE
        | ADD_BUILD_LOG
        | BUILD_PATHS_WITH_RESULTS
        | ADD_PERM_ROOT
    )
    # All pynixd-specific (non-stock) operations.
    PYNIXD_SPECIFIC_ALL = (
        PYNIXD_COLLECT_GARBAGE
        | PROBE_SYSTEMS
        | PROBE_FEATURES
        | QUERY_CLOSURE
        | QUERY_CLOSURE_WITH_INFO
        | QUERY_PATH_INFOS
        | QUERY_DERIVATION_OUTPUT_MAP_BATCH
        | SIGN_PATH_INFO
        | IS_VALID_PATH_PYNIXD
        | QUERY_SUBST_PATH_INFO
    )
    # All CA derivation content types.
    CA_ALL = CA_FLOATING | CA_FIXED | CA_TEXT_HASHED | CA_MULTI_OUTPUT | CA_DEPENDS_ON_CA
    # All non-dynamic derivation content types.
    CONTENT_ALL = REGULAR | FIXED_OUTPUT | CA_ALL | DEFERRED | TEXT_OUTPUT | MULTI_OUTPUT | SUBSTITUTABLE
    # All dynamic derivation chain features.
    DYN_ALL = (
        DYN_PRODUCING_DRV
        | DYN_RESOLVE_INNER_DRV
        | DYN_CHILD_MAP
        | DYN_CHAIN
        | DYN_DEEP
        | DYN_PLACEHOLDER_CHAIN
        | DYN_MIXED_DEPS
        | DYN_DEFERRED_PLUS
        | DYN_NAR_ROUNDTRIP
        | DYN_OUTPUT_OF
        | DYN_WRAPPER_BUILD
    )
    # All goal system features.
    GOAL_ALL = (
        GOAL_BUILD
        | GOAL_OPAQUE
        | GOAL_RESOLUTION
        | GOAL_DYNAMIC
        | GOAL_DEFERRED
        | GOAL_DAG
        | GOAL_BUILD_QUEUE
        | GOAL_MANAGER
        | GOAL_PATH_MAP
        | GOAL_TRAMPOLINE
        | GOAL_SCHEDULER
    )
    # All store backend types.
    STORE_ALL = STORE_LOCAL | STORE_SSH | STORE_UNIX | STORE_REVERSE | STORE_DELEGATOR | STORE_POOL
    # All HTTP binary cache server features (read + write + auth).
    HTTP_CACHE_ALL = (
        STORE_HTTP_BINARY_CACHE | STORE_HTTP_BINARY_CACHE_WRITE | SERVER_HTTP | SERVER_HTTP_AUTH | SERVER_HTTP_UPLOAD
    )
    # All server features.
    SERVER_ALL = (
        SERVER_SSH
        | SERVER_HTTP
        | SERVER_HTTP_AUTH
        | SERVER_HTTP_UPLOAD
        | SERVER_UNIX
        | SERVER_SESSION_BRIDGE
        | SERVER_RBAC
        | SERVER_FEATURE_PROBE
        | SERVER_HANDSHAKE
        | SERVER_KUBERNETES_API
        | SERVER_SFTP
        | SERVER_BUILD_LOG_PUBSUB
        | SERVER_PSI_GATING
        | SERVER_PARAM_LOGS
    )
    # All garbage collection features.
    GC_ALL = GC_COLLECT | GC_MIN_AGE | GC_MAX_FREED | GC_GLOBAL | GC_FIND_ROOTS
    # All substitution features.
    SUBSTITUTE_ALL = (
        SUBSTITUTE | SUBSTITUTE_HTTP | SUBSTITUTE_HTTP_AUTH | SUBSTITUTABLE_PATH_INFO | SUBSTITUTION_MANAGER
    )
    # All internal parser/encoder/utility features.
    INTERNAL_ALL = (
        WIRE_ENCODE
        | WIRE_DECODE
        | DRV_PARSE
        | DRV_SERIALIZE
        | DRV_HASH_DERIVATION_MODULO
        | DRV_COMPUTE_STOREPATH
        | DERIVED_PATH
        | STORE_PATH_ENCODE
        | UTILS_NIX32
        | UTILS_CRYPTO
        | NAR_PARSE
        | NAR_SERIALIZE
        | NAR_STREAM
    )
    # All benchmark/stress features.
    BENCHMARK_ALL = BENCHMARK_PARALLEL | BENCHMARK_DEEP | BENCHMARK_LARGE_FILE
    # All extension delegation features.
    EXTENSION_ALL = EXTENSION_BUILD | EXTENSION_DELEGATION
    # All build types and metadata features.
    META_ALL = BUILD_TYPES | PATH_INFO | SIGNING | PERSISTENCE
