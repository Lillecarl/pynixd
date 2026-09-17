#!/usr/bin/env python3
"""pynixd's suites, in a guest that is thrown away when they finish.

A guest and not a derivation, because these suites start nix daemons,
pynixd daemons, ssh servers and http servers, and build into stores they
make themselves. A build sandbox cleans up files and nothing else: a
process that outlives its test keeps running on the machine that ran it.
Poweroff ends a kernel and a disk image both. Measured while writing
this: a UML guest leaked by an earlier session had been using a whole
core for thirty-one hours.

That is also what makes the leak countable. Nothing here has to survive
one, so the census below can be exact.

One process per suite: `tests/unit` and `nix-daemon-protocol/tests`
interfere, and one process for both is how 57 failures once hid behind a
green run of 684. Issue #33.

Each suite writes its log and its junit file to `/artifacts`, a host
directory, so a guest that wedges loses none of it.
"""

from __future__ import annotations

import json

from uml_runner import Machine, Machines, run_test

PER_TEST_TIMEOUT = 600
"""Seconds for one test, against the suite's own default of 120, which is
written for a machine rather than a machine inside one. Measured:
`test_wire_parity[impure]` timed out at 120.016s on an idle host."""

SUITES = [
    ("unit", "tests/unit", [f"--async-test-timeout={PER_TEST_TIMEOUT}"]),
    ("protocol", "nix-daemon-protocol/tests", []),
    ("parity", "tests/parity", [f"--async-test-timeout={PER_TEST_TIMEOUT}"]),
]
"""Name, what to hand pytest, and the flags only that suite takes.

Per suite, because `nix-daemon-protocol/tests` is its own project with
its own `pytest.ini`: it does not know `--async-test-timeout`, and pytest
answers an unknown option with exit 4 before collecting anything.

`tests/parity` was missing for 35 minutes of failure that the guest's own
configuration caused: it took cache.nixos.org from the NixOS default and
had no route to it, so every substituter query waited 15 seconds and
retried five times. `substituters = lib.mkForce [ ]` in the guest
derivation is what put it back -- 2120.92s and 5 failures became 121.20s
and none. Issue #37.

Missing on purpose: `tests/functional`, which wants a daemon it may build
with (issue #29)."""

TIMEOUT = 2400
"""Seconds for one suite. Measured in a QEMU guest on an idle host:
`tests/unit` 27.2s, `nix-daemon-protocol/tests` 4.8s, `tests/parity`
122.2s. `tests/unit` takes 87s under UML. The rest is for a loaded
builder."""

ALLOWED_LEAKS = 0
"""The one leak this check has found belongs to `tests/parity`: a
per-connection worker of a managed daemon, which `daemon.cc` gives its own
session, so no group signal at either end reaches it. Issue #36. Raise this
only with an issue number beside it, never to make a run pass."""

LEAK_MARKERS = ("/tmp/pynixd-", "pynixd")
"""A test store, or pynixd itself. Matched against the command line and
the process name both, because `/proc/<pid>/stat` cuts a name at 15
characters.

Not `nix-daemon`: the guest's own is socket-activated, so it reads `0
before, 1 after` every time and none of it is a leak."""


async def test(vms: Machines) -> None:
    vm = vms.node

    # hostfs hands the host's ownership through, so the directory belongs
    # to whoever started the run and `tester` is somebody else.
    await vm.succeed("chmod 0777 /artifacts")

    # The suites run as `tester`, who does not own the store, so every
    # `nix` call has to reach the daemon. Waited on rather than assumed:
    # without it the failure is 21 errors in `test_drv_parser` reading
    # `creating directory "/nix/store/.links": Permission denied`, which
    # names a directory and not the daemon.
    #
    # The dump goes first, so a unit that never came up leaves the state
    # of every other one behind it.
    (vms.artifacts / "units.txt").write_text(
        "\n".join(f"{u['name']:48} {u['load']:10} {u['active']:10} {u['sub']}" for u in await vm.list_units("*")) + "\n"
    )
    await vm.wait_for_unit("nix-daemon.socket", timeout=30)

    src = vms.settings["src"]
    await vm.succeed(f"cp -r {src} /work && chmod -R u+w /work && chown -R tester /work")
    print(f"[test] the suites run from {src}, copied to /work")

    before = await census(vm)

    failures = []
    for name, path, flags in SUITES:
        # Redirected to /artifacts rather than returned: pytest's output
        # is thousands of lines and the way back is a serial line.
        # `PYNIXD_TEST_LOG_DIR` puts the per-test logs there too --
        # without it a failure names `/tmp/pynixd-logs/...`, which dies
        # with the guest.
        command = (
            f"cd /work && "
            f"PYTHONPATH=/work NIX_BIN=$(command -v nix) "
            f"PYNIXD_TEST_LOG_DIR=/artifacts/{name}-logs "
            f"pytest -p no:cacheprovider --tb=short -q {' '.join(flags)} "
            f"--junitxml=/artifacts/{name}.xml {path} "
            f"> /artifacts/{name}.log 2>&1"
        )
        # `su -` and not `su`: a login shell gives tester its own HOME,
        # and pytest writes there. A non-login su leaves HOME as root's.
        rc, _ = await vm.execute(f"su - tester -c {command!r}", timeout=TIMEOUT, label=f"pytest {name}")
        tail = await vm.succeed(f"tail -n 3 /artifacts/{name}.log")
        print(f"[test] {name}: exit {rc}\n[test]   {tail.strip()}")
        if rc != 0:
            failures.append(name)

    after = await census(vm)

    # By pid, not by count: a count that happens to match hides one
    # process exiting while another leaks.
    was_running = {p["pid"] for p in before["processes"]}
    leaked = [p for p in after["processes"] if p["pid"] not in was_running and _is_a_leak(p)]

    print(f"[test] {before['total']} processes before, {after['total']} after")
    for p in leaked:
        print(f"[test] LEAKED {p['pid']} (parent {p['ppid']}) {p['cmdline'][:120]}")

    # Written whether or not anything leaked: a clean census is what a
    # later one is read against.
    (vms.artifacts / "processes.json").write_text(
        json.dumps({"before": before, "after": after, "leaked": leaked}, indent=2) + "\n"
    )

    assert len(leaked) <= ALLOWED_LEAKS, (
        f"the suites left {len(leaked)} processes holding a test store, and "
        f"{ALLOWED_LEAKS} is what issue #36 accounts for. The whole census is "
        "in processes.json beside this run's logs."
    )
    assert not failures, (
        f"these suites failed: {', '.join(failures)}. Their logs and junit files are in this run's artifacts directory."
    )


def _is_a_leak(process: dict) -> bool:
    return any(marker in process["cmdline"] or marker in process["name"] for marker in LEAK_MARKERS)


async def census(vm: Machine) -> dict:
    """Every process in the guest.

    The whole list and not a count: a count says something is still
    there, the list says which one and what started it.
    """
    procs = await vm.processes()
    return {"total": len(procs), "processes": procs}


run_test(test)
