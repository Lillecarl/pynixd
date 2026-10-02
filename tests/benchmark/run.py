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

BUILD_TIMEOUT = 240
"""Seconds for one role's build.

The goal is a thousand trivial derivations. One build is one `execute` call,
and this cap keeps a wedged transport from holding the session open: a timed-out
build is a result, and a faster one than the phase's own timeout.
"""

ACTIVITIES = 100_000
ROUNDS = 7
OPERATIONS = 20_000
"""How many of each op the raw pump sends through a daemon."""

HOT_PASSES = 5
"""How many hot builds each daemon runs. The best is reported.

The container shares the host's CPU, so one pass carries whatever else the
host was doing. A cold build stays one pass: it is mostly moving data, and the
host's load moves it less than it moves a few seconds of daemon traffic.
"""


async def test(vms: Machines) -> None:
    if vms.phase == "decode":
        await _decode(vms)
    elif vms.phase == "raw":
        await _raw(vms)
    elif vms.phase == "profile":
        await _profile(vms)
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


PROFILE_WRAPPER = """import atexit
import os
import sys

import pyinstrument

prof = pyinstrument.Profiler(interval=0.0005)
_done = [False]


def dump():
    if _done[0]:
        return
    _done[0] = True
    prof.stop()
    with open("/artifacts/profile.txt", "w") as handle:
        handle.write(prof.output_text(unicode=True, color=False, show_all=True))


atexit.register(dump)

prof.start()
os.environ.setdefault("PYNIXD_CONFIG", "/etc/pynixd/pynixd.json")
sys.argv = ["pynixd", "daemon"]
from pynixd.__main__ import main

main()
"""


async def _profile(vms: Machines) -> None:
    """Profile pynixd while a raw pump drives it, and keep the flame text.

    The numbers of `raw` say how fast an operation is; this says where the
    time went. `pyinstrument` samples the daemon while the pump runs, and the
    wrapper writes a text report when the daemon shuts down on SIGTERM. The
    daemon is started by hand here, so the profiler wraps it: the systemd unit
    starts pynixd before the phase can.

    The phase is not a gate. Read `/artifacts/profile.txt`.
    """
    [vm] = vms.values()
    await vm.succeed("chmod 0777 /artifacts")

    src = vms.settings["src"]
    await vm.succeed(f"rm -rf /work && cp -r {src} /work && chmod -R u+w /work")

    # The profiled daemon takes the socket itself, so the unit must let it go.
    await vm.succeed("systemctl stop pynixd.service pynixd.socket; rm -f " + vms.settings["socket"])

    wrapper = "/work/profile_daemon.py"
    await vm.succeed(f"cat > {wrapper} <<'PYEOF'\n{PROFILE_WRAPPER}\nPYEOF")

    # Start the profiled daemon in the background. It reads the tree first, so
    # the profile is of the edited code.
    start = (
        "cd /work && PYTHONPATH=/work:/work/nix-daemon-protocol/src "
        f"nohup python {wrapper} > /artifacts/profile_daemon.log 2>&1 & echo $!"
    )
    pid_out = await vm.succeed(start)
    print(f"[benchmark] profiled pynixd started: {pid_out.strip()}")

    # Wait for the socket, then pump it with fewer operations than `raw`:
    # sampling makes the daemon several times slower.
    await vm.succeed(
        f"for _ in $(seq 1 100); do [ -S {vms.settings['socket']} ] && break; sleep 0.2; done",
        timeout=60,
    )
    pump = (
        "cd /work && PYTHONPATH=/work:/work/nix-daemon-protocol/src "
        f"python tests/benchmark/raw_ops.py {OPERATIONS // 4} "
        f"{vms.settings['upstream']} {vms.settings['socket']} "
        "> /artifacts/profile_pump.log 2>&1 || true"
    )
    await vm.execute(pump, timeout=TIMEOUT, label="profile pump")
    print((await vm.succeed("cat /artifacts/profile_pump.log")).strip())

    # SIGTERM the wrapper so its atexit handler writes the report, by the pid
    # it printed and not by a name (a `pkill -f` matches this shell too).
    pid = pid_out.strip().splitlines()[-1].strip()
    await vm.succeed(
        f"kill -TERM {shlex.quote(pid)}; for _ in $(seq 1 50); do "
        f"[ -f /artifacts/profile.txt ] && break; sleep 0.2; done"
    )
    profile = await vm.succeed("cat /artifacts/profile.txt")
    print("[benchmark] pynixd profile (top):\n" + "\n".join(profile.splitlines()[:60]))
    if not profile.strip():
        raise AssertionError("the profiler wrote no report")


async def _system(vms: Machines) -> None:
    """Build a whole closure through each daemon, cold and hot, and time both.

    Evaluating nixpkgs asks the daemon about every derivation, the realisation
    substitutes the closure, and the impure noise of `system.nix` makes it
    build a thousand things on top. This is the whole pipeline: a fault the
    op pump cannot see shows here.

    **The hot run is the number that matters.** A cold build is mostly moving
    data: it fetches the closure and writes a thousand outputs. A hot build of
    the same goal has every path already, so what is left is the traffic the
    client sends to the daemon and the answer it reads back. That is the
    incremental cost of the front-end, and the only part the two daemons can
    differ in.

    Each daemon builds its own goal, once cold and then `HOT_PASSES` times
    hot. The goal is named by `hostName`, so the two daemons never build the
    same closure and one cannot warm the other's cold run. The **best** hot
    pass is reported, because the container shares the host's CPU.
    """
    [vm] = vms.values()
    expression = f"{vms.settings['src']}/tests/benchmark/system.nix"

    roles = {
        "nix-daemon": (vms.settings["upstream"], "nixdaemon"),
        "pynixd": (vms.settings["socket"], "pynixd"),
    }

    cold_times: dict[str, float] = {}
    hot_times: dict[str, tuple[float, int]] = {}
    failed = []
    # One stamp for the whole phase. It is fresh to the store, so each daemon's
    # first build really builds, and it is the same for that daemon's later
    # builds, so those really are hot.
    stamp = str(int(time.time()))

    for label, (socket, host_name) in roles.items():
        inner = (
            f"NIX_PATH=nixpkgs={vms.settings['nixpkgs']} NIX_REMOTE=unix://{socket} "
            f"nix build --argstr hostName {host_name} --argstr stamp {stamp} "
            f"--file {expression} --no-link > /dev/null 2>&1"
        )
        # One cold build, and only the cold one is timed. Then the hot passes.
        start = time.monotonic()
        rc, _ = await vm.execute(f"su - tester -c {shlex.quote(inner)}", timeout=BUILD_TIMEOUT, label=label)
        cold_times[label] = time.monotonic() - start
        print(f"[benchmark] {label:10} cold {cold_times[label]:7.2f}s rc={rc}")
        if rc != 0:
            failed.append(f"{label} cold")

        best = (float("inf"), 0)
        for pass_index in range(HOT_PASSES):
            start = time.monotonic()
            rc, _ = await vm.execute(f"su - tester -c {shlex.quote(inner)}", timeout=BUILD_TIMEOUT, label=label)
            elapsed = time.monotonic() - start
            best = min(best, (elapsed, pass_index), key=lambda pair: pair[0])
            print(f"[benchmark] {label:10} hot {pass_index + 1} {elapsed:7.2f}s rc={rc}")
            if rc != 0:
                failed.append(f"{label} hot {pass_index + 1}")
        hot_times[label] = best

    if failed:
        raise AssertionError(f"system build failed through {', '.join(failed)}")

    for label in roles:
        cold = cold_times[label]
        hot, pass_index = hot_times[label]
        print(f"[benchmark] {label:10} cold {cold:7.2f}s  hot {hot:7.2f}s (best of {HOT_PASSES})")

    # What the client actually asked pynixd for, and how long pynixd spent on
    # each operation. A tester who cannot write the store's database reaches
    # the daemon for all of it, so this is the load on the front-end. The
    # largest `total_ops` is the build; a small session is an eval.
    #
    # pynixd logs one JSON record per session at close. `python -c` parses
    # each one, sorts by `total_ops`, and prints the two largest with their
    # breakdown, so a build is not lost under the eval sessions around it.
    breakdown = await vm.succeed(
        "journalctl -u pynixd --no-pager -o cat "
        "| grep client_op_timing "
        r"""| python3 -c 'import sys, json
rows = []
for line in sys.stdin:
    line = line.strip()
    if not line.startswith("{"):
        continue
    rec = json.loads(line)
    rows.append((
        rec.get("total_ops", 0),
        rec.get("total_time", ""),
        rec.get("total_encode", ""),
        rec.get("breakdown", {}),
        rec.get("encode_breakdown", {}),
    ))
rows.sort(reverse=True)
for total_ops, total_time, total_encode, ops, encode in rows[:2]:
    print(f"session total_ops={total_ops} dispatch={total_time} encode={total_encode}")
    for name, value in sorted(ops.items(), key=lambda kv: kv[1], reverse=True):
        print(f"    {name:28} {value}")
    top = sorted(encode.items(), key=lambda kv: kv[1], reverse=True)[:5]
    if top:
        print("    encode:")
        for name, value in top:
            print(f"        {name:24} {value}")
'"""
        " || true"
    )
    print(f"[benchmark] pynixd op breakdown:\n{breakdown.strip()}")
