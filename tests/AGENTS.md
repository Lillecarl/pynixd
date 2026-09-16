# Test Conventions

## Running the suites in a guest

```sh
nix build --file . tests.guest            # QEMU, needs /dev/kvm
nix build --file . tests.guest.uml        # a process, on any machine
nix build --file . tests.guest.typecheck  # pyright over the script, seconds
```

One NixOS guest, three pytest processes, poweroff. Nothing survives it: no
store under `/tmp`, no daemon, no socket. That is what it is for — these
suites start daemons and build into stores they make, and a build sandbox
cleans up files and not processes.

**The test derivation never fails.** `tests.guest` reads a marker that
`tests.guest.attempt` wrote, and names that path in its build log. So a
failed run keeps its logs, its junit files and its process census. Read
them there rather than running it again.

Two things a test in there must respect:

- Write evidence to `/artifacts`, which is a host directory. Anything
  under `/tmp` in the guest dies with the guest. `$PYNIXD_TEST_LOG_DIR`
  is what points the suite's own per-test logs at it.
- The suites run as `tester`, who does not own the store, so every `nix`
  call goes through the guest's daemon — the same shape as a developer's
  machine, and not the same as running as root.

The run counts every process in the guest before and after, and fails on a
daemon that outlived its suite. That check found issue #36 on its first
run.

`tests/guest/run.py` is the script and `tests/derivations/guest/` packages
it. The script lives here rather than in user-mode-nixos: that repository
is the library, and a test about pynixd belongs beside pynixd.

## `@pytest.mark.asyncio`

Do **NOT** add `@pytest.mark.asyncio` markers to any test function. The conftest.py hook `pytest_collection_modifyitems` already auto-detects `async def` test functions and wraps them with `asyncio.timeout`. Adding the explicit marker is redundant.
