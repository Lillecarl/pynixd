# The suites, in a guest.
#
# `nix build --file . tests.guest` boots one NixOS guest, runs pytest twice
# in it, and powers it off. Nothing of it survives: no store, no daemon, no
# socket, no temporary directory. That is the whole reason it exists -- see
# tests/guest/run.py.
#
# pyright runs over tests/guest/run.py as an input of the test, so a typo
# in the script stops a derivation that takes seconds rather than one that
# boots a guest. `boot.uml.typeCheck` is the switch.
#
# **QEMU, and only QEMU today.** These suites need a real kernel. Under
# User-Mode Linux they panic the guest -- `Kernel panic - not syncing:
# Kernel mode fault` inside `munmap`, measured twice -- and before that
# they took it down through io_uring, which is why every UML guest now
# turns io_uring off (user-mode-nixos's modules/guest.nix). It is not
# reliable either way: one UML run finished `tests/unit` in 87 seconds and
# the next panicked. UML's memory manager is what gives, not pynixd.
#
# `.uml` exists because `mkTest` builds both, not because it works. Do not
# reach for it without reading user-mode-nixos issue #8.
#
# The script is here and not in user-mode-nixos. That repository is a
# library: it supplies `mkTest`, the guest modules and the `uml_runner`
# package, and a test about pynixd belongs beside pynixd.
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
  # rather than what the tree says. See tests/derivations/pytest.
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
  # Beside the suites it runs, not beside this file: it is a test, and
  # tests/derivations holds the Nix that packages them.
  script = ../../guest/run.py;

  /*
    What the script needs that only Nix knows.

    `mkTest` registers the closure of everything here with the guest's Nix
    database, so the source and nixpkgs are valid paths in there rather
    than files Nix goes looking for a substituter for.
  */
  settings = {
    src = "${src}";
    nixpkgs = "${pkgs.path}";
  };

  nodes.node = {
    boot.uml = {
      # `tests/unit` alone peaks well above the 128M a guest gets by
      # default, and an agent that is OOM-killed partway through looks
      # exactly like a hang.
      memory = "3072M";
      # The suites make stores of their own under /tmp, which is the
      # root disk.
      diskSize = 8192;
    };

    environment.systemPackages = [
      pytestEnv
      pkgs.nix
      pkgs.coreutils
      pkgs.util-linux
    ];

    # Nothing here turns io_uring off, and pynixd's uvloop would
    # otherwise take a UML guest down. The guest module does it for every
    # UML guest -- see the note in user-mode-nixos's modules/guest.nix,
    # which this suite is the measurement behind.

    /*
      Not root, which is what the suites are run as everywhere else.

      One test asserts that a store root pynixd cannot write to yields an
      inactive instance, and root can write anywhere -- so as root it is
      the test that fails and not the code. Measured: it is the only
      failure in `tests/unit` in a guest.
    */
    users.users.tester = {
      isNormalUser = true;
      uid = 1000;
    };

    nix.settings = {
      # What `tests/_conftest/constants.py` asks for through NIX_CONFIG.
      # Named here as well, because a test that runs `nix` without going
      # through that fixture reads the guest's own configuration.
      experimental-features = [
        "nix-command"
        "flakes"
        "read-only-local-store"
        "ca-derivations"
        "dynamic-derivations"
        "recursive-nix"
      ];
      # The store belongs to root, so an untrusted user could not build
      # in it. pynixd issue #29 is the same problem in the packaged
      # check, where the build user is not trusted and cannot be made so.
      trusted-users = [
        "root"
        "tester"
      ];
    };

    # `tests/nix/drv-probes.nix` imports <nixpkgs>. Without this the
    # twenty-two tests that read it fail on "file 'nixpkgs' was not found
    # in the Nix search path", which names nothing about pynixd.
    nix.nixPath = [ "nixpkgs=${pkgs.path}" ];
  };
}
