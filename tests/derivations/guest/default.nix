# The suites, in a guest.
#
# `nix build --file . tests.guest` boots one NixOS guest, runs pytest twice
# in it, and powers it off. Nothing of it survives -- see tests/guest/run.py
# for why that is the point.
#
# **QEMU, and only QEMU.** Under User-Mode Linux these suites panic the
# guest: `Kernel panic - not syncing: Kernel mode fault` inside `munmap`,
# measured twice. It is UML's memory manager that gives, not pynixd, and it
# is intermittent -- one UML run finished `tests/unit` in 87 seconds and the
# next panicked. `.uml` exists because `mkTest` builds both, not because it
# works. Read user-mode-nixos issue #8 before reaching for it.
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
in
uml.mkTest {
  name = "pynixd";
  backend = "qemu";
  script = ../../guest/run.py;

  /*
    What the script needs that only Nix knows.

    `mkTest` registers the closure of everything here with the guest's Nix
    database, so these are valid paths in there rather than files Nix goes
    looking for a substituter for.
  */
  settings = {
    src = "${src}";
    nixpkgs = "${pkgs.path}";
  };

  nodes.node = {
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
  };
}
