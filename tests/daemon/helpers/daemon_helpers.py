"""What the daemon session's phases share: running as the user, and reading answers.

The work runs as `tester`, never root. Nix's `auto` store opens the store
directly for a user who can write it, so a root `nix build` on the pynixd
guest would never reach pynixd, and would pass for the wrong reason.
"""

from __future__ import annotations

import json
import re
import shlex

from uml_runner import Machine

SERVERS = ("daemon", "control")

VOLATILE = ("registrationTime",)
"""Fields that differ between two correct builds: when each was registered."""


async def as_tester(vm: Machine, command: str, *, timeout: float = 300) -> str:
    """`command`'s stdout alone, run as tester.

    The agent returns stdout and stderr as one text, and Nix writes its
    progress and warnings to stderr -- "these derivations will be built"
    with a `.drv` path in it, which a caller looking for the last store path
    took for the answer. stderr still reaches a failure's message.
    """
    stdout = f"/tmp/as-tester-{abs(hash(command))}"
    await vm.succeed(f"su - tester -c {shlex.quote(command)} > {stdout}", timeout=timeout)
    return await vm.succeed(f"cat {stdout}; rm -f {stdout}")


async def attempt(vm: Machine, command: str, *, user: str = "tester", timeout: float = 120) -> tuple[int, str]:
    """`command`'s exit status and its stderr, run as `user`, for a command that may fail."""
    stderr = f"/tmp/attempt-{abs(hash(command))}"
    rc, _ = await vm.execute(f"su - {user} -c {shlex.quote(command)} > /dev/null 2> {stderr}", timeout=timeout)
    return rc, await vm.succeed(f"cat {stderr}; rm -f {stderr}")


def errors(stderr: str) -> list[str]:
    """The `error:` and `warning:` lines of a client's stderr, without colour.

    A temporary link's name carries a pid and a random number, so two
    correct runs name different ones.
    """
    plain = re.sub(r"\x1b\[[0-9;]*m", "", stderr)
    plain = re.sub(r"\.tmp-link-\d+-\d+", ".tmp-link-N", plain)
    return [line.strip() for line in plain.splitlines() if line.strip().startswith(("error:", "warning:"))]


def store_path(output: str) -> str:
    """The last store path in a command's stdout."""
    paths = [line.strip() for line in output.splitlines() if line.strip().startswith("/nix/store/")]
    if not paths:
        raise AssertionError(f"no store path in: {output!r}")
    return paths[-1]


async def instantiate(vm: Machine, settings: dict, job: str) -> str:
    """The `.drv` of one piece of work, written into `vm`'s store by its daemon.

    `builtins.storePath` gives the builder its context, so busybox is an
    input the sandbox mounts, not a string it cannot reach.
    """
    expression = (
        "derivation {"
        f' name = "pynixd-daemon-{job}";'
        f' system = "{settings["system"]}";'
        f' builder = "${{builtins.storePath "{settings["busybox"]}"}}/bin/sh";'
        f' args = [ "-c" "echo {job} > $out" ];'
        " }"
    )
    return store_path(await as_tester(vm, f"nix-instantiate --expr {shlex.quote(expression)}"))


async def path_info(vm: Machine, path: str, *, store: str | None = None) -> dict:
    """`nix path-info --json` for one path, without the fields that differ by time.

    Nix has written this as an object keyed by path and as a list, so both
    are accepted.
    """
    where = f"--store {shlex.quote(store)} " if store else ""
    raw = json.loads(await as_tester(vm, f"nix path-info --json {where}{shlex.quote(path)}"))
    entry = next(iter(raw.values())) if isinstance(raw, dict) else raw[0]
    return {key: value for key, value in entry.items() if key not in VOLATILE}


def differences(daemon: dict, control: dict) -> dict[str, tuple[object, object]]:
    return {
        key: (daemon.get(key), control.get(key))
        for key in sorted(set(daemon) | set(control))
        if daemon.get(key) != control.get(key)
    }
