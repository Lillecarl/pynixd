"""Get the guest ready for the suites, and count what runs before them.

The suites run as `tester`, who does not own the store, so every `nix`
call has to reach the guest's daemon. Waited on rather than assumed:
without it the failure is 21 errors in `test_drv_parser` reading
`creating directory "/nix/store/.links": Permission denied`, which names
a directory and not the daemon.
"""

from __future__ import annotations

from uml_runner import Machines


async def test(vms: Machines) -> None:
    vm = vms.node

    # hostfs hands the host's ownership through, so the directory belongs
    # to whoever started the run and `tester` is somebody else.
    await vm.succeed("chmod 0777 /artifacts && mkdir -p /artifacts/junit && chmod 0777 /artifacts/junit")

    # The dump goes first, so a unit that never came up leaves the state
    # of every other one behind it.
    (vms.artifacts / "node" / "units.txt").write_text(
        "\n".join(f"{u['name']:48} {u['load']:10} {u['active']:10} {u['sub']}" for u in await vm.list_units("*")) + "\n"
    )
    await vm.wait_for_unit("nix-daemon.socket", timeout=30)

    src = vms.settings["src"]
    await vm.succeed(f"cp -r {src} /work && chmod -R u+w /work && chown -R tester /work")
    print(f"[test] the suites run from {src}, copied to /work")

    # Every process, not a count: `leaks` compares by pid. It reads this
    # from `vms.shared`, the one thing that crosses from phase to phase.
    vms.shared["before"] = await vm.processes()
