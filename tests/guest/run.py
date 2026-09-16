#!/usr/bin/env python3
"""pynixd's suites, in a guest that is thrown away when they finish.

**Why a guest and not a derivation.** These suites start nix daemons,
pynixd daemons, ssh servers and http servers, and they build into stores
they make themselves. A build sandbox cleans up files and nothing else:
a process that outlives its test keeps running on the machine that ran
it, and a store left behind is a store somebody garbage-collects later.
A guest is a kernel and a disk image, and poweroff ends both. Measured
while writing this: a UML guest leaked by an earlier session had been
using a whole core for thirty-one hours.

**Why that makes the leak worth counting.** The cleanup being free is
what makes the question answerable. Nothing here has to survive a leak,
so the census below can be exact -- every process in the guest, before
and after -- and it costs one round trip.

The three suites run as three processes on purpose. `tests/unit` and
`nix-daemon-protocol/tests` interfere: four of the protocol tests pass
alone and fail beside the pynixd suites, and one process for both is how
57 failures once hid behind a green run of 684. pynixd issue #33.

Each suite writes its log and its junit file to `/artifacts`, which is a
host directory -- see user-mode-nixos's AGENTS.md. So the evidence is on
the host as it is written, and a guest that wedges loses none of it.
"""

from __future__ import annotations

import json

from uml_runner import Machine, Machines, run_test

PER_TEST_TIMEOUT = 600
"""Seconds for one test, against the suite's own default of 120.

A guest is a machine inside a machine, and the default is written for the
outer one. Measured: `test_wire_parity[impure]` runs four nested `nix
build` invocations of an impure derivation, twice so that the two daemons
can be compared, and timed out at 120.016s on an idle host."""

SUITES = [
    ("unit", "tests/unit", [f"--async-test-timeout={PER_TEST_TIMEOUT}"]),
    ("protocol", "nix-daemon-protocol/tests", []),
]
"""Name, what to hand pytest, and the flags only that suite takes.

The flags are per suite because `nix-daemon-protocol/tests` is its own
project with its own `pytest.ini` and its own conftest: it does not know
`--async-test-timeout`, and pytest answers an unknown option with exit 4
before it collects anything.

`tests/parity` is not here, and neither is `tests/functional`:

- parity fails in a guest in a way it does not on a machine, and takes 35
  minutes to say so. `store_dir()` memoises the first read into a module
  global, so whether a test sees its own `NIX_STORE_DIR` depends on what
  ran before it -- and the count of failures moved between two runs of
  the same code. Issue #37.
- functional wants a daemon it is allowed to build with. Issue #29."""

TIMEOUT = 2400
"""Seconds for one suite. `tests/unit` takes 11 in a QEMU guest and 87
under UML, measured on an idle host; the rest is for a loaded builder,
where a guest is a process competing with every other build."""

ALLOWED_LEAKS = 0
"""How many leaked processes the suites above are allowed to leave.

None. The one leak this check has found belongs to `tests/parity`, which
is issue #36 and which the suites above no longer run -- so raise this
only with an issue number beside it, never to make a run pass."""

LEAK_MARKERS = ("/tmp/pynixd-", "pynixd")
"""What a process must not still be holding when the suites are done.

A test store, or pynixd itself. Matched against the command line and the
process name both, because `/proc/<pid>/stat` cuts a name at 15
characters.

**Not `nix-daemon`.** The guest's own daemon is socket-activated, so it
is not running before the first `nix` call and is running after -- `0
before, 1 after` every time, and none of it a leak. A process still
naming a store under `/tmp/pynixd-` is a different thing: that store
belongs to a test that has finished."""


async def test(vms: Machines) -> None:
    vm = vms.node

    # hostfs hands the host's ownership straight through, so the
    # directory belongs to whoever started the run and the guest's test
    # user is somebody else. Root in the guest can still open it.
    await vm.succeed("chmod 0777 /artifacts")

    # The suites run as `tester`, who does not own the store, so every
    # `nix` call in them has to reach the daemon. Waited on rather than
    # assumed: without it the failure is 21 errors inside
    # `test_drv_parser` saying `creating directory "/nix/store/.links":
    # Permission denied`, which names a directory and not the daemon.
    # Written before anything is waited on, so a unit that never came up
    # leaves the state of every other one behind it.
    (vms.artifacts / "units.txt").write_text(
        "\n".join(
            f"{u['name']:48} {u['load']:10} {u['active']:10} {u['sub']}"
            for u in await vm.list_units("*")
        )
        + "\n"
    )
    await vm.wait_for_unit("nix-daemon.socket", timeout=30)

    src = vms.settings["src"]
    await vm.succeed(f"cp -r {src} /work && chmod -R u+w /work && chown -R tester /work")
    print(f"[test] the suites run from {src}, copied to /work")

    before = await census(vm)

    failures = []
    for name, path, flags in SUITES:
        # Redirected to /artifacts rather than returned: pytest's output
        # is thousands of lines, and the way back from the guest is a
        # serial line.
        # `PYNIXD_TEST_LOG_DIR` puts the suite's own per-test logs in
        # /artifacts as well. Without it a failure reports
        # `logs: /tmp/pynixd-logs/...` and nothing else, and that path
        # dies with the guest -- so the one run that needed reading would
        # leave a summary naming a file that is gone.
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
        rc, _ = await vm.execute(
            f"su - tester -c {command!r}", timeout=TIMEOUT, label=f"pytest {name}"
        )
        tail = await vm.succeed(f"tail -n 3 /artifacts/{name}.log")
        print(f"[test] {name}: exit {rc}\n[test]   {tail.strip()}")
        if rc != 0:
            failures.append(name)

    after = await census(vm)

    # By pid, not by count: a count that happens to match hides one
    # process exiting while another leaks.
    was_running = {p["pid"] for p in before["processes"]}
    leaked = [
        p
        for p in after["processes"]
        if p["pid"] not in was_running and _is_a_leak(p)
    ]

    print(f"[test] {before['total']} processes before, {after['total']} after")
    for p in leaked:
        print(f"[test] LEAKED {p['pid']} (parent {p['ppid']}) {p['cmdline'][:120]}")

    # Written whether or not anything leaked: the census of a clean run is
    # what a later one is read against.
    (vms.artifacts / "processes.json").write_text(
        json.dumps({"before": before, "after": after, "leaked": leaked}, indent=2) + "\n"
    )

    assert len(leaked) <= ALLOWED_LEAKS, (
        f"the suites left {len(leaked)} processes holding a test store, and "
        f"{ALLOWED_LEAKS} is what issue #36 accounts for. The whole census is "
        "in processes.json beside this run's logs."
    )
    assert not failures, (
        f"these suites failed: {', '.join(failures)}. "
        "Their logs and junit files are in this run's artifacts directory."
    )


def _is_a_leak(process: dict) -> bool:
    return any(
        marker in process["cmdline"] or marker in process["name"]
        for marker in LEAK_MARKERS
    )


async def census(vm: Machine) -> dict:
    """Every process in the guest.

    The whole list and not a count: a count says something is still there
    and the list says which one and what started it, which is the
    difference between a bug report and a rerun.
    """
    procs = await vm.processes()
    return {"total": len(procs), "processes": procs}


run_test(test)
