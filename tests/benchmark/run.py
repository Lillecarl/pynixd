"""Run a benchmark inside the guest, and keep what it measured.

One script serves every phase, and `vms.phase` picks the benchmark. `decode`
and `raw` are micro-measurements; `system` is the whole pipeline, and is the
one that would notice a fault the other two cannot see.
"""

from __future__ import annotations

import shlex
import time

from vivarium_runner import Machines

TIMEOUT = 1800
"""Seconds for one phase."""

ACTIVITIES = 100_000
ROUNDS = 7
OPERATIONS = 20_000
"""How many of each op the raw pump sends through a daemon."""


async def test(vms: Machines) -> None:
    if vms.phase == "decode":
        await _decode(vms)
    elif vms.phase == "raw":
        await _raw(vms)
    elif vms.phase == "system":
        await _system(vms)
    else:
        raise RuntimeError(f"no benchmark for phase {vms.phase!r}")


async def _decode(vms: Machines) -> None:
    """Decode the activity stream a build makes, without a build.

    `PYTHONPATH` puts the tree first, so it imports the edited
    `nix_daemon_protocol` and not the copy built into the environment;
    `stream_decode.py` prints the module it imported.
    """
    [vm] = vms.values()
    await vm.succeed("chmod 0777 /artifacts")

    src = vms.settings["src"]
    await vm.succeed(f"rm -rf /work && cp -r {src} /work && chmod -R u+w /work")

    command = (
        "cd /work && PYTHONPATH=/work:/work/nix-daemon-protocol/src "
        f"python tests/benchmark/stream_decode.py {ACTIVITIES} {ROUNDS} "
        "> /artifacts/decode.log 2>&1"
    )
    rc, _ = await vm.execute(command, timeout=TIMEOUT, label="decode benchmark")
    print(f"[benchmark] decode exit {rc}\n{(await vm.succeed('cat /artifacts/decode.log')).strip()}")
    if rc != 0:
        raise AssertionError(f"decode benchmark exited {rc}")


async def _raw(vms: Machines) -> None:
    """Pump IsValidPath and AddTempRoot through each daemon, and count.

    The same client sends to both, so the daemon is the only thing that
    differs. It runs as root on purpose: the client opens the socket the
    benchmark names, rather than the `auto` store that root would take.
    """
    [vm] = vms.values()
    await vm.succeed("chmod 0777 /artifacts")

    src = vms.settings["src"]
    await vm.succeed(f"rm -rf /work && cp -r {src} /work && chmod -R u+w /work")

    command = (
        "cd /work && PYTHONPATH=/work:/work/nix-daemon-protocol/src "
        f"python tests/benchmark/raw_ops.py {OPERATIONS} "
        f"{vms.settings['upstream']} {vms.settings['socket']} "
        "> /artifacts/raw.log 2>&1"
    )
    rc, _ = await vm.execute(command, timeout=TIMEOUT, label="raw ops")
    print(f"[benchmark] raw exit {rc}\n{(await vm.succeed('cat /artifacts/raw.log')).strip()}")
    if rc != 0:
        raise AssertionError(f"raw ops benchmark exited {rc}")


async def _system(vms: Machines) -> None:
    """Build a system of its own through each daemon, and time it.

    Evaluating nixpkgs asks the daemon about every derivation, the realisation
    substitutes the closure, and the impure noise of `system.nix` makes it
    build ten thousand things on top. This is the whole pipeline: a fault the
    op pump cannot see shows here. Each daemon builds a *different* system,
    named after it, because they front one store and the same system built
    twice would be a no-op through whichever daemon went second.
    """
    [vm] = vms.values()
    expression = f"{vms.settings['src']}/tests/benchmark/system.nix"

    roles = [
        ("nix-daemon", vms.settings["upstream"], "nixdaemon"),
        ("pynixd", vms.settings["socket"], "pynixd"),
    ]

    failed = []
    for label, socket, host_name in roles:
        inner = (
            f"NIX_PATH=nixpkgs={vms.settings['nixpkgs']} NIX_REMOTE=unix://{socket} "
            # `--impure`, for the noise derivations of `system.nix`.
            f"nix build --impure --argstr hostName {host_name} --file {expression} --no-link "
            "> /dev/null 2>&1"
        )
        start = time.monotonic()
        rc, _ = await vm.execute(f"su - tester -c {shlex.quote(inner)}", timeout=TIMEOUT, label=label)
        print(f"[benchmark] {label:10} system build {time.monotonic() - start:7.2f}s rc={rc}")
        if rc != 0:
            failed.append(label)

    if failed:
        raise AssertionError(f"system build failed through {', '.join(failed)}")

    # The facts behind the two numbers: what each client actually asked
    # pynixd for, and how long pynixd spent on each operation. A tester who
    # cannot write the store's database reaches the daemon for all of it, so
    # this is the load the build put on the front-end, operation by operation.
    breakdown = await vm.succeed(
        "journalctl -u pynixd --no-pager -o cat | grep client_op_timing | tail -1 || true"
    )
    print(f"[benchmark] pynixd op breakdown: {breakdown.strip()}")
