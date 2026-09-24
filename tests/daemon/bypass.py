"""Without pynixd, a user on `daemon` has no daemon: the requests needed it.

The causal half of `prepare`'s check that pynixd holds the socket. With
pynixd stopped and nix-daemon still running behind it, a user's request
must fail; started again, it must work. A `replace` mode that left some
path around pynixd -- a client reading `upstream` directly -- would pass
the earlier phases and fail here.
"""

from __future__ import annotations

from daemon_helpers import as_tester
from uml_runner import Machines

PROBE = "nix-store --query --hash {busybox}"


async def test(vms: Machines) -> None:
    vm = vms.daemon
    probe = PROBE.format(busybox=vms.settings["busybox"])
    await vm.succeed("systemctl stop pynixd.service")
    try:
        state = (await vm.succeed("systemctl is-active nix-daemon.socket")).strip()
        rc, output = await vm.execute(f"su - tester -c {probe!r}")
        print(f"[test] pynixd stopped, nix-daemon.socket {state}: exit {rc}")
        if rc == 0:
            raise AssertionError(f"a user's request succeeded with pynixd stopped: {output!r}")
    finally:
        await vm.succeed("systemctl start pynixd.service")
    await vm.wait_for_unit("pynixd.service")
    print(f"[test] pynixd started again: {(await as_tester(vm, probe)).strip()}")
