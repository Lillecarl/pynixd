# pynixd Development Mandates

This document defines the foundational architectural patterns and engineering standards for `pynixd`. These instructions take precedence over general defaults.

## 1. Version Control: Jujutsu (jj)
- **Tool**: Use `jj` (Jujutsu), NOT `git`.
- **Committing**: Prefer `jj commit -m "..."` to finish a task. It creates a new revision and provides a clean working copy.
- **Squashing**: If your changes are a fixup for the last commit, prefer `jj squash --use-destination-message` to keep the commit message or `jj squash -m "..."` to update the commit message
- **Paging**: Always include `--no-pager` in all `jj` commands to ensure non-interactive execution.
- **Subagents must NEVER use VCS tools** (no `jj`, no `git`, no `jj squash`, no `jj commit`, nothing). Subagents are strictly limited to reading/writing files and running validation commands (`just cheap`). If a subagent cannot complete its task, it should report the error back to the primary agent and let the primary agent handle it.

## 2. Core Architectural Pattern: Request-Driven Execution
`pynixd` follows a strict three-tier execution pattern to separate protocol IO from business logic.

Important Nix protocol version support matrix:
Builder stores: >= 1.32 (nixbuild.net is 1.32)
Local stores: >= 1.35 (Lix is 1.35)
Pynixd will adversise 1.38 support even if local_store is 1.35 and translate where appropriate

1. **Server Dispatch** (`OpRequest.handle(proxy)`): 
   - Entry point for the `DaemonProxy`.
   - Decodes the request from the client wire.
   - Delegates logic to the store: `return await proxy.local_store.execute(request)`.
   - *Streaming operations* (like `NarFromPath` or `AddToStore`) override this to handle raw byte piping.

2. **Logic Hook** (`OpRequest.execute(store, client=None, suppress_last=False)`):
   - Where the "recipe" for an operation lives.
   - Implements optimizations (SQLite fast-paths, memory caches).
   - If no optimization exists, falls back to the wire: `return await store.call(self, client=client, suppress_last=suppress_last)`.

3. **Store Executor** (`Store.execute(request, ...)`):
   - Simple polymorphic dispatcher that calls `request.execute(self, ...)`.

4. **Transport** (`Store.call(request, ...)`):
   - Low-level wire protocol implementation.
   - Handles connection pooling, protocol magic, and handshake.

## 3. Stderr & Logging
- **`StderrBuffer`**: All buffered responses MUST include a `StderrBuffer` in their `stderr` field.
- **Real-time Forwarding**: If a `ClientConn` is provided to `execute()`, logs MUST be forwarded to `client.queue` in real-time while also being buffered in the response.
- **`suppress_last`**: When executing sub-operations (e.g., builds within a `BuildPaths` request), intermediate `STDERR_LAST` messages MUST be suppressed to avoid confusing the client.
- **Transparency**: No-op or cached operations MUST inject a `StderrNext` message (e.g., `"pynixd: IsValidPath (SQLite hit)"`) into the buffer for transparency.

## 4. Engineering Standards
- **Validation**: ALWAYS run `just precommit` before committing. This runs `ruff` (formatting/linting), `pyright` (type checking) and functionality tests.
  - **Subagent validation**: When verifying changes from a subagent, run `just cheap` at most (ruff + pyright). Do NOT run `just precommit` or the full test suite — that's overkill for individual file changes. Save the full test suite for final verification.
- **Type Safety**:
  - NEVER use string type hints (e.g., `"Store"`). Use `from __future__ import annotations` where needed for `TYPE_CHECKING` imports and forward references.
  - Use `if TYPE_CHECKING:` blocks for cross-module imports.
- **Imports**: All imports MUST be at the top of the file (or inside `if TYPE_CHECKING:` blocks). Lazy imports inside functions/methods are strictly forbidden unless they are absolutely necessary to break a circular import cycle. If you introduce a circular dependency, prefer refactoring (e.g., moving shared types to a neutral module) over lazy imports.
  - **Import ordering** (strict — ruff enforces this):
    1. `from __future__ import annotations`
    2. Standard library imports (grouped: `import X`, `from X import Y`)
    3. Third-party imports
    4. Local/pynixd imports (`from . import X`, `from ..module import Y`)
    5. `if TYPE_CHECKING:` block ( LAST among imports — only for type-only imports)
    6. Module-level constants
    7. Code (classes, functions, etc.)
  - `if TYPE_CHECKING:` blocks must contain ONLY type-only imports (imports not needed at runtime). Runtime local imports must NEVER appear after `if TYPE_CHECKING:`.
  - **Re-export pattern**: When re-exporting a name from another module (so it's accessible as `module.name` for consumers), use `from .module import Name as Name` — the `as` is required for re-export. Consolidate multiple re-exports into a single multi-line import block. Example:
    ```python
    from .constants import (
        PROTO as PROTO,
        MAGIC as MAGIC,
    )
    ```
  - **Asserts**: NEVER use `assert` statements outside of the `tests/` directory. For runtime validation, use explicit `if not cond: raise RuntimeError(...)`. To satisfy type checkers, use local variable aliasing or explicit `if cond is None` checks.
  - **Asyncio**: 
    - NEVER use `asyncio.get_event_loop()`. Use `asyncio.get_running_loop()` inside async functions. For timestamps, use `time.monotonic()` instead of `loop.time()`.
    - ALWAYS maintain a strong reference (e.g., in a class instance variable `set` or `list`) to background tasks created via `asyncio.create_task()`. Failure to do so allows the garbage collector to destroy the task mid-execution.
- **No-ops**: Restricted operations (like `SetOptions`, `AddPermRoot`, `AddIndirectRoot`) must be implemented as no-ops for regular users by overriding their `handle` method (not `execute`). If the user has `Role.ADMIN`, the operation should be executed normally. For other users, it should return success (`0` or `EmptyResponse`) and log its status via `StderrNext` for transparency. `execute` must always perform the actual upstream daemon operation.
- **HTTP Cache Streaming**: If a NAR transfer fails after the `200 OK` header is sent, the server MUST abruptly close the connection to signal failure to the client. Full buffering to avoid this is not supported due to memory constraints.
- **pathlib.Path**: Use pathlib.Path when dealing with any strings that aren't Nix daemon protocol related. Convert to string as late as possible if needed
- **Exception Visibility**: NEVER use bare `except Exception: pass` blocks for unexpected failures. All unexpected exceptions MUST be logged (e.g., `log.exception(...)` or `log.warning(...)`) to ensure hidden failures are visible in logs. If an exception is truly expected and should be ignored, use `contextlib.suppress(...)` with a comment explaining why.

## 5. Build Logic
Builds are the only "complex" operations in `pynixd`. They are handled via a global `BuildQueue` and a DAG-aware `Scheduler`. 
- `BuildPaths` and `BuildPathsWithResults` are decomposed into individual `BuildDerivation` requests.
- Each build executes in a spawned task, surviving client disconnects.
- Outputs are automatically pulled into the `LocalStore` upon successful completion.

## 6. Execution Sanity & Recovery
- **Halt on Ambiguity**: If a tool output indicates potential corruption (e.g., duplicate declarations in a `replace` output, unexpected truncations), or if you lose track of the file state relative to the VCS, **STOP immediately**. Do not attempt blind recovery (like `write_file` with partial content).
- **Verify Before Rewrite**: Before using `write_file` to "fix" a large file, you MUST have read the *entire* file in the current turn to ensure no data loss.
- **VCS Truth**: If `jj status` or `jj diff` contradicts your internal model of the changes, re-sync by reading the files from disk before taking further action. Do not guess.

## 7. Documentation & Onboarding
- **Glossary**: A comprehensive `GLOSSARY.md` is maintained in the repository root. It defines both foundational Nix terminology (NAR, StorePath, etc.) and `pynixd`-specific concepts (Trampolining, Three-Tier Execution).
- **Living Document**: The glossary is NOT exhaustive. All contributors (including AI agents) MUST expand the glossary when introducing or clarifying complex architectural patterns, specialized terminology, or non-obvious domain concepts.

## 8. Test Suite Rules

### Directory Structure
- **`tests/functional/`** — active end-to-end and integration tests
- **`tests/benchmark/`** — performance benchmarks
- **`tests/legacy/`** — old tests, do not modify

### Test Store Conventions
- All test stores MUST use the `/tmp/pynixd-stores` prefix (defined as `STORE_PREFIX` in `tests/conftest.py`).
- Use `rmtree_robust_glob(f"{STORE_PREFIX}*")` in fixtures to clean up leftover stores.
- Only a few select tests should run against the root store (`store_path=Path("/")`). Most tests should use isolated stores with the prefix.

### Test Helpers
- **`run_captured(cmd, **kwargs)`** — runs a subprocess, returns `(rc, stdout, stderr)`.
- **`run_logged(cmd, **kwargs)`** — runs a subprocess, streams output through structlog in real-time.
- Both helpers auto-set `NIX_SSHOPTS` if not already present.
- Use `env.str("NIX_BIN", "nix")` and `env.str("LIX_BIN", "nix")` from the `environs` singleton for binary paths.

### Test Design
- Keep tests simple and explicit. Avoid over-engineered abstractions.
- Construct commands as plain lists so the exact invocation is visible at a glance.
- Use `pytest.fixture(autouse=True)` for per-test cleanup (store directories, etc.).

### Running Validation Commands
- **Single pytest invocation only**: NEVER run more than one `pytest` process at a time in this repository. Functional tests share session store paths, daemon sockets, and `/tmp/pynixd-stores` state; concurrent pytest runs can race each other and produce misleading failures that look like real regressions.
- **NEVER pipe away output** from `just check`, `just precommit`, or `pytest` — the full output contains failure details you need to diagnose issues.
- If the user explicitly tells you not to pipe or select on output, YOU MUST DO WHAT THEY SAY. No exceptions. Do not override their instruction with this rule's redirect-to-file fallback — they want to see the output directly.
- If output is too large for context (failing tests produce heaps of logs), you may redirect to a file: `pytest ... > /tmp/test-output.txt 2>&1`, then read specific sections. But if the user told you not to redirect, you must not redirect.
- Do NOT use `tee` when redirecting — it doubles context consumption.
- If you must limit output, use `tail -N` on the file afterwards, never pipe the command itself.
- You do NOT need to specify pytest timeout, the configured 120s is enough per test.
- **Timeouts**: `just precommit` runs the full functional test suite (3min+) — set timeout=300 (5 min) for Bash tool calls. Unit tests (`pytest tests/unit/`) complete in seconds — timeout=60000 is fine.

## 7. User Direction Supersedes All Rules

Every instruction in this file is a default. The user's explicit direction overrides any and all of them, always.

- If the user says "don't pipe away output", you do not pipe away output — not to a file, not to tail, not to anything. You show them the full output directly.
- If the user says run something a specific way, you run it exactly that way. You do not second-guess or substitute your judgment for theirs.
- If you're about to do something and you realize the user already told you not to, stop immediately and do what they said.
- **No self-persuasion**: If they told you to do X and the rules say Y, you do X and update the rules later if needed.

## 8. Async Task & Lifecycle Rules

- **Structured Concurrency**: For short-lived, bounded concurrent operations (e.g., fanning out requests, concurrent streams within a single handler, or parallel passes like GC), ALWAYS prefer `asyncio.TaskGroup` over `asyncio.gather` or manual `create_task` management.
- **Task Tracking**: Long-lived daemon components (Servers, Pools, Monitors) should continue using explicit `start()`/`close()` lifecycle methods. All background tasks created via `asyncio.create_task` in these components MUST be tracked (e.g., in a list or as a class attribute) and properly cleaned up during the component's `close()` or `stop()` method.
- **Graceful Shutdown**: When awaiting a cancelled background task during shutdown, ALWAYS use `with contextlib.suppress(Exception, asyncio.CancelledError):`. This ensures that if a task failed with an unhandled exception during its lifetime, that exception does not crash the shutdown sequence.
- **Orphaned Tasks**: Avoid orphaned tasks. If a `TaskGroup` cannot be used, ensure helper tasks use a `try...finally` block to guarantee they are cancelled and awaited if the primary operation fails.
