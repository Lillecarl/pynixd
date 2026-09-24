# The suites, in a guest.
#
# `nix build --file . tests.guest` boots one NixOS guest per suite, runs
# the suites at once as phases of their own, counts each guest's
# processes before and after, and powers them off. Nothing of it survives
# -- see tests/guest/leaks.py for why that is the point.
#
# **QEMU, and only QEMU.** Under User-Mode Linux these suites panic the
# guest: `Kernel panic - not syncing: Kernel mode fault` inside `munmap`,
# measured twice. It is UML's memory manager that gives, not pynixd, and it
# is intermittent -- one UML run finished `tests/unit` in 87 seconds and the
# next panicked. Read user-mode-nixos issue #8 before reaching for it.
{
  pkgs,
  lib ? pkgs.lib,
  pynixd-lib,
  src,
  user-mode-nixos,
}:

let
  uml = import (user-mode-nixos + "/lib.nix") { inherit pkgs lib; };

  # The same set the packaged check builds, so the guest runs what ships
  # rather than what the tree says.
  pytestEnv = pkgs.python3.withPackages (ps: [
    pynixd-lib
    ps.pytest
    ps.pytest-timeout
    ps.pyinstrument
  ]);

  perTestTimeout = "--async-test-timeout=600";

  /*
    One phase each, generated below from this list.

    Per suite, because `nix-daemon-protocol/tests` is its own project with
    its own `pytest.ini`: it does not know `--async-test-timeout`, and
    pytest answers an unknown option with exit 4 before collecting
    anything. The 600 seconds is against the suite's own 120, which is
    written for a machine rather than a machine inside one: measured,
    `test_wire_parity[impure]` timed out at 120.016s on an idle host.

    Each suite needs only `prepare`, so a failure in one skips none of the
    others -- they are independent, and a run that stopped at the first
    would hide the second.

    Missing on purpose: `tests/functional`, which wants a daemon it may
    build with (issue #29).
  */
  suites = {
    unit = {
      path = "tests/unit";
      flags = [ perTestTimeout ];
    };
    protocol = {
      path = "nix-daemon-protocol/tests";
      flags = [ ];
    };
    # Missing for 35 minutes of failure that the guest's own configuration
    # caused: it took cache.nixos.org from the NixOS default and had no
    # route to it. `substituters = lib.mkForce [ ]` below is what put it
    # back -- 2120.92s and 5 failures became 121.20s and none. Issue #37.
    parity = {
      path = "tests/parity";
      flags = [ perTestTimeout ];
    };
  };
in
uml.mkSession {
  name = "pynixd";
  backend = "qemu";

  /*
    What the scripts need that only Nix knows.

    `mkSession` registers the closure of everything here with the guest's
    Nix database, so these are valid paths in there rather than files Nix
    goes looking for a substituter for.
  */
  settings = {
    src = "${src}";
    nixpkgs = "${pkgs.path}";
    inherit suites;
  };

  phases = {
    prepare = {
      script = ../../guest/prepare.py;
      after = [ "boot" ];
    };
    leaks = {
      script = ../../guest/leaks.py;
      after = lib.attrNames suites;
      always = true;
      description = "no process outlived its suite";
    };
  }
  // lib.mapAttrs (name: suite: {
    script = ../../guest/suite.py;
    after = [ "prepare" ];
    # A guest each, so the suites run at once: the session starts phases
    # whose guests do not overlap together. `prepare` and `leaks` name no
    # guest, so they hold all three.
    nodes = [ name ];
    description = suite.path;
  }) suites;

  # One guest per suite, named after it, so events.jsonl says
  # `machine=unit` and a suite's files land in artifacts/unit/.
  nodes = lib.genAttrs (lib.attrNames suites) (_: {
    boot.uml = {
      # `tests/unit` peaks well above the default 128M, and an agent that
      # is OOM-killed partway through looks exactly like a hang.
      memory = "3072M";
      # The suites make stores of their own under /tmp, on the root disk.
      diskSize = 8192;
    };

    environment.systemPackages = [
      pytestEnv
      pkgs.nix
      pkgs.coreutils
      pkgs.util-linux
    ];

    /*
      Not root, which is what the suites run as everywhere else.

      One test asserts that a store root pynixd cannot write to yields an
      inactive instance, and root can write anywhere -- so as root it is
      the test that fails and not the code. Measured: the only failure in
      `tests/unit` in a guest.
    */
    users.users.tester = {
      isNormalUser = true;
      uid = 1000;
    };

    nix.settings = {
      # What `tests/_conftest/constants.py` asks for through NIX_CONFIG.
      # Named here too, because a test that runs `nix` without that
      # fixture reads the guest's own configuration.
      experimental-features = [
        "nix-command"
        "flakes"
        "read-only-local-store"
        "ca-derivations"
        "dynamic-derivations"
        "recursive-nix"
      ];
      # The store belongs to root, so an untrusted user could not build in
      # it. Issue #29 is the same problem in the packaged check, where the
      # build user cannot be made trusted.
      trusted-users = [
        "root"
        "tester"
      ];
      # The guest has no route out, and NixOS defaults this to
      # `https://cache.nixos.org/`. So every substituter query resolved
      # nowhere, waited 15 seconds and retried five times.
      #
      # Measured: `tests/parity` took 2120.92s in the guest and 18.21s on
      # the host, and four of its five failures were recordings that agreed
      # on every wire answer and disagreed only on which operation the retry
      # warnings landed under. Issue #37.
      #
      # `[substitute]` is the one case that needs a cache. It makes its own
      # and names it with `--substituters`, so an empty list here does not
      # reach it -- that case passed in the run that measured this.
      #
      # `mkForce`, because `nix.settings` holds lists and the module system
      # merges lists by concatenation. A plain `[ ]` adds nothing and
      # removes nothing: the rendered `/etc/nix/nix.conf` still read
      # `substituters = https://cache.nixos.org/`, measured on the etc
      # derivation before this line said `mkForce`.
      substituters = lib.mkForce [ ];
    };

    # `tests/nix/drv-probes.nix` imports <nixpkgs>. Without this, the 22
    # tests that read it fail on "file 'nixpkgs' was not found in the Nix
    # search path", which names nothing about pynixd.
    nix.nixPath = [ "nixpkgs=${pkgs.path}" ];
  });
}
