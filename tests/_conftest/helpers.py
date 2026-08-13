"""Filesystem and subprocess helpers for tests."""

from __future__ import annotations

import asyncio
import contextlib
import glob as glob_module
import os
import shlex
import shutil
import signal
import stat
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from pynixd.nix_config import NixConfig
from pynixd.serde import NarFromPathRequest
from pynixd.serde import StorePath as SerdeStorePath
from pynixd.serde.context import WriteContext
from tests._conftest.constants import DEFAULT_NIX_CONFIG, DEFAULT_SSH_OPTS

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from pynixd.store import DaemonStore

log = structlog.get_logger(__name__)


def rmtree_robust(path: str | Path) -> None:
    """Recursively remove a directory or file, unsetting read-only bits as needed."""
    path = Path(path)
    if not path.exists():
        return

    if path.is_dir():

        def handle_errors(func, path, _excinfo):
            try:
                Path(path).chmod(stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
                func(path)
            except OSError:
                pass  # Best-effort cleanup; ignore failures

        shutil.rmtree(path, onerror=handle_errors)
    else:
        try:
            path.unlink()
        except PermissionError:
            try:
                path.chmod(stat.S_IWRITE | stat.S_IREAD)
                path.unlink()
            except OSError:
                pass  # Best-effort cleanup; ignore failures
        except OSError:
            pass  # Best-effort cleanup; ignore failures


def rmtree_robust_glob(pattern: str) -> None:
    """Remove all directories matching a glob pattern."""
    for path_str in glob_module.glob(pattern):  # noqa: PTH207
        rmtree_robust(Path(path_str))


def serde_path(path: object) -> SerdeStorePath:
    """Convert domain/test path objects to the wire StorePath model."""
    return SerdeStorePath(path=str(path))


def serde_path_set(paths: Iterable[object]) -> set[SerdeStorePath]:
    """Convert a path iterable to the wire StorePath set used by request models."""
    return {serde_path(path) for path in paths}  # pyright: ignore[reportUnhashable]


async def read_nar_from_store(store: DaemonStore, path: object, nar_size: int) -> bytes:
    """Read a NAR stream from a store using the streaming NarFromPath protocol."""
    async with store.transfer_conn() as conn:
        await NarFromPathRequest(path=serde_path(path)).to_writer(WriteContext.from_conn(conn))
        await conn.w.drain()
        await conn.r.drain_stderr()
        return await conn.r.readexactly(nar_size)


async def run_subproc(
    cmd: Sequence[str | Path],
    verbose: bool = True,
    expected_retcode: int | None = 0,
    nix_config: NixConfig | dict[str, str] | None = None,
    **kwargs,
) -> tuple[int, str, str, str]:
    """Run a command, streaming stdout/stderr through structlog in real-time.

    Args:
        cmd: Command and arguments to run.
        verbose: If True, stream output to structlog in real-time.
        expected_retcode: If not None, raise if return code doesn't match. Defaults to 0.
        nix_config: NixConfig object or dict for NIX_CONFIG env var.
        **kwargs: Additional arguments passed to create_subprocess_exec.

    Returns:
        tuple of (returncode, stdout, stderr, combined)
    """
    run_env = kwargs.pop("env", {})
    if "NIX_SSHOPTS" not in run_env:
        run_env["NIX_SSHOPTS"] = DEFAULT_SSH_OPTS

    if isinstance(nix_config, NixConfig):
        config_str = nix_config.to_nix_config_env()
    elif nix_config is not None:
        default_config = {
            "substituters": "https://nixkube.cachix.org unix:///nix/var/nix/daemon-socket/socket?root=/",
            "trusted-public-keys": "nixkube.cachix.org-1:H8UE0jlI9pxHexK/NhDmEoLDarJXp1WTymQrsajlh7M=",
        }
        merged = default_config | nix_config
        config_str = "\n".join(f"{k} = {v}" for k, v in merged.items())
    else:
        config_str = DEFAULT_NIX_CONFIG.to_nix_config_env()

    if "NIX_CONFIG" in run_env:
        run_env["NIX_CONFIG"] = f"{run_env['NIX_CONFIG']}\n{config_str}"
    else:
        run_env["NIX_CONFIG"] = config_str

    str_cmd = [str(c) for c in cmd]
    log.debug("run_subproc", cmd=shlex.join(str_cmd), env=run_env)
    proc = await asyncio.create_subprocess_exec(
        *str_cmd,
        env=os.environ.copy() | run_env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
        **kwargs,
    )

    stdout: list[str] = []
    stderr: list[str] = []
    stdboth: list[str] = []

    async def stream(name: str, accumulator: list[str], pipe) -> None:
        while True:
            line = await pipe.readline()
            decoded_line = line.decode()
            accumulator.append(decoded_line)
            stdboth.append(decoded_line)
            if not line:
                break
            if verbose:
                log.info(name, message=decoded_line.rstrip())

    try:
        await asyncio.gather(
            stream("stdout", stdout, proc.stdout),
            stream("stderr", stderr, proc.stderr),
        )
        await proc.wait()
    except asyncio.CancelledError:
        log.warning("run_subproc_cancelled", cmd=shlex.join(str_cmd), pid=proc.pid)
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
                await proc.wait()
        raise
    rc = proc.returncode if proc.returncode is not None else 0
    if expected_retcode is not None and rc != expected_retcode:
        raise RuntimeError(
            f"Command failed with rc={rc} (expected {expected_retcode}):\n{''.join(stdboth)}",
        )
    return (rc, "".join(stdout), "".join(stderr), "".join(stdboth))
