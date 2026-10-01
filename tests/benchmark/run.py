"""Run the decode benchmark inside the guest, and keep what it measured.

The tree is copied to /work and `PYTHONPATH` puts it first, so the benchmark
imports the edited `nix_daemon_protocol` and not the copy built into the
environment. `stream_decode.py` prints the module it imported, so a run that
read the environment's copy is visible in the log.
"""

from __future__ import annotations

from vivarium_runner import Machines

TIMEOUT = 600
"""Seconds for the phase. The benchmark itself runs in about a second."""

ACTIVITIES = 100_000
ROUNDS = 7


async def test(vms: Machines) -> None:
    [vm] = vms.values()
    await vm.succeed("chmod 0777 /artifacts")

    src = vms.settings["src"]
    await vm.succeed(f"cp -r {src} /work && chmod -R u+w /work")

    command = (
        "cd /work && "
        "PYTHONPATH=/work:/work/nix-daemon-protocol/src "
        f"python tests/benchmark/stream_decode.py {ACTIVITIES} {ROUNDS} "
        "> /artifacts/decode.log 2>&1"
    )
    rc, _ = await vm.execute(command, timeout=TIMEOUT, label="decode benchmark")
    log = (await vm.succeed("cat /artifacts/decode.log")).strip()
    print(f"[benchmark] exit {rc}\n{log}")
    if rc != 0:
        raise AssertionError(f"decode benchmark exited {rc}")
