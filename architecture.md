# pynixd architecture

## Overview

pynixd is a Nix daemon protocol proxy that distributes build workloads across multiple remote builders while maintaining a local store for caching and serving query results.

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│   Client    │────▶│   Router     │────▶│   Local     │
│  Connection │     │              │     │   Store     │
└─────────────┘     └──────┬───────┘     └─────────────┘
                           │
                    ┌──────▼───────┐
                    │ Build Queue  │
                    │   (global)   │
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │  Scheduler   │
                    └──────┬───────┘
                           │
         ┌─────────────────┼─────────────────┐
         ▼                 ▼                 ▼
   ┌───────────┐   ┌───────────┐   ┌───────────┐
   │  Backend  │   │  Backend  │   │  Backend  │
   │   Pool    │   │   Pool    │   │   Pool    │
   └───────────┘   └───────────┘   └───────────┘
```

## Components

### 1. ClientConnection

**Responsibility**: Handle the Nix protocol exchange with a single client.

**Operations**:
- Protocol handshake (magic, version negotiation)
- Read request ops, write response ops
- Forward stderr messages during builds

**Local operations** (QueryPathInfo, NarFromPath, AddToStore*, etc.):
- Routed directly to LocalStore, no queueing

**Build operations** (BuildDerivation, BuildPaths):
- Enqueued to BuildQueue with metadata
- Client connection stored for response delivery

### 2. LocalStore

**Responsibility**: Persistent connection to local nix-daemon.

**Operations**:
- Query operations (IsValidPath, QueryPathInfo, QueryValidPaths, NarFromPath, etc.)
- Store mutations (AddToStore, AddToStoreNar, AddMultipleToStore)
- Direct SQLite access to store db for path size queries (for scheduling decisions)

**Properties**:
- One connection per ClientConnection (not shared)
- Each LocalStore has its own StoreDB instance

### 3. BuildQueue

**Responsibility**: Global queue for build operations.

**Entry schema**:
```python
@dataclass
class QueuedBuild:
    id: int  # incrementing build ID (1, 2, 3, ...)
    request: BuildDerivationRequest  # includes derivation + inputs
    clients: list[ClientConnection]  # multiple clients waiting for result
    enqueued_at: float  # monotonic timestamp
    input_size: int  # total NAR size of inputs
    status: BuildStatus  # pending, preparing_transfer, queued, building, completed, failed

    # Computed, changes over time as path availability changes
    ranked_backends: list[tuple[str, int]]  # [(backend_id, num_inputs), ...] sorted by locality

    # Active transfer state
    transfer_to_backup: str | None = None  # proactive transfer in progress to backup
    transfer_progress: float = 0.0  # 0.0 to 1.0
```

**Operations**:
- `enqueue(build)` - add to queue (deduplicates if same derivation already queued)
- `get_pending()` - return all pending builds
- `update_status(id, status)` - mark progress
- `attach_client(id, client)` - add another client to wait for result
- `remove_completed()` - clean up old entries

**Deduplication**: When a new build comes in, check if identical derivation (same drv + same inputs) is already queued. If so, attach new client to existing entry instead of creating new.

### 4. Scheduler

**Responsibility**: Decide where and when to execute builds.

**Scheduling algorithm**:

1. **Queue always** (no immediate dispatch):
   - All builds go through the queue
   - If no contention (no other builds queued), dispatch is immediate

2. **Locality matching** (re-evaluated on each scheduling pass):
   - For each pending build, score backends by number of input_srcs available
   - Ranking changes dynamically as paths are added/removed from backends
   - Build's `ranked_backends` is updated on each scheduling pass

3. **Per-worker queue assignment**:
   - Builds are not permanently assigned to a backend
   - When slot opens on backend X: find lowest-ID build that currently ranks X as #1
   - That build gets the slot (may switch from backup to primary)
   - If no build ranks X as #1, look for #2, etc.

4. **Proactive path transfer to backup**:
   - When build is assigned to backend A (best locality) but slot not available:
     - Start transferring missing paths to backend B (second-best locality) in background
     - Update build's `ranked_backends` to reflect B now has more paths
   - When slot opens on A: cancel transfer to B, execute on A
   - If transfer completes before slot opens: update ranking, might execute on B instead

5. **Parallelism limits**:
   - Max N concurrent builds per backend (default 2, configurable per backend)
   - Track in-flight builds per backend in BackendPool
   - A build is "in queue" but not assigned until slot opens

**Scheduling loop**:
- Single scheduler task waiting on an `asyncio.Event`
- Triggers set the event:
  - New build enqueued
  - Build completes (slot opens)
  - Path transfer completes (availability changed)
- When event is set:
  1. Clear event
  2. Re-score all pending builds (update ranked_backends based on current path state)
  3. For each backend with available slot: find lowest-ID build that ranks it highest, assign
  4. For builds queued on full backend: start/update/cancel proactive transfers
  5. Dispatch newly assigned builds to BackendPool
- This ensures only one scheduler runs at a time

### 5. BackendPool

**Responsibility**: Manage SSH connections to builder hosts and background transfers.

**Properties**:
- Maintains persistent SSH connections to each configured backend
- One connection per backend (not per build)
- Tracks in-flight build count per backend
- Tracks active path transfers

**Operations**:
- `acquire_slot(backend_id)` - get permission to build (respects limit)
- `release_slot(backend_id)` - mark build complete
- `execute_build(backend_id, request)` - run BuildDerivation, return response
- `start_transfer(backend_id, paths)` - begin background transfer of paths
- `cancel_transfer(transfer_id)` - cancel an in-progress transfer
- `get_path_availability()` - return dict of backend -> set of available paths

### 6. OutputPuller (part of BackendPool or separate)

**Responsibility**: After successful build, copy outputs from builder to local store.

**Process** (after BuildDerivation succeeds):
1. For each output path in `built_outputs`:
   a. QueryPathInfo on backend → get PathInfo
   b. NarFromPath on backend → get NAR bytes
   c. AddToStoreNar on local → inject into local store
   d. Update path tracker with new local paths
2. Mark build as completed, deliver response to client

### 7. PathTracker

**Responsibility**: Track which paths exist on which backends.

**Data**:
- `local_paths: set[str]` - paths in our local store
- `backend_paths: dict[str, set[str]]` - backend_id → paths available

**Updated via**:
- Local store operations (AddToStore*)
- QueryValidPaths from backends
- After output pull, mark as local

**Used by**: Scheduler for locality scoring

## Request flow

### Local operation (QueryPathInfo)
```
Client → ClientConnection → LocalStore → Client
```

### Build operation (BuildDerivation)
```
Client → ClientConnection → BuildQueue
                               │
                          Scheduler (decides backend)
                               │
                        BackendPool.execute_build()
                               │
                        OutputPuller (pull outputs to local)
                               │
                          BuildQueue (mark complete)
                               │
                          ClientConnection → Client
```

## Edge cases

1. **Client disconnects while build in queue**: Mark build as failed, don't deliver response

2. **Client disconnects while build in progress**:
   - Let build complete on backend
   - Don't pull outputs (no one to deliver them to)
   - Optionally: pull anyway to populate local cache

3. **Backend connection drops during build**:
   - Mark build as failed
   - Return error to client
   - Increment failure count for backend (maybe mark unhealthy?)

4. **Build fails on backend**:
   - Forward error to client
   - Don't pull outputs

5. **Multiple clients requesting same derivation**:
   - Each gets its own queued entry
   - Could optimize by detecting duplicates and reusing results

## Configuration

```python
@dataclass
class Config:
    # Local store
    local_socket: str = "/nix/var/nix/daemon-socket/socket"

    # Scheduler
    default_parallel_per_backend: int = 2
    base_timeout_seconds: float = 10.0
    timeout_per_mb: float = 1.0  # timeout = base + size_mb * per_mb
    scheduler_interval_seconds: float = 1.0

    # Backend pool
    backends: list[BackendConfig]
```

## Open questions (answered)

1. **Build result caching**: Yes - deduplication attaches multiple clients to same queued build. After build completes, we pull outputs to local store before responding. Subsequent NAR/QueryPathInfo requests serve from local.

2. **Missing outputs**: Unclear how this would happen in practice - Nix ensures outputs are valid. (Not a concern for now.)

3. **Build timeouts**: No timeouts - let Nix's built-in stuck build detector handle hung builds. We only manage queueing/transferring input paths.

4. **Backend failure handling**: Exponential backoff on backend connection failures.