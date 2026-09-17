{
  lib,
  mkShell,
  pyright,
  ruff,
  sqlite,
  nix,
  # The interpreter, resolved by pyproject.nix. `default.nix` says why it is
  # one environment and not several: the oracle of `tests/differential`
  # reaches the nanopynix bindings, and a closure that deep does not survive
  # being assembled on `PYTHONPATH`.
  devEnv,
  # `pkgs.path`, for `NIX_PATH`. See the shell hook.
  nixpkgsPath,
}:
mkShell {
  packages = [
    devEnv
    pyright
    ruff
    sqlite
  ];
  shellHook = ''
    # The two working copies in front of the environment, and nothing else.
    # `devEnv` installs `pynixd` and `nix_daemon_protocol` as built packages,
    # and an edit must reach the next command without a rebuild.
    export PYTHONPATH="$PWD:$PWD/nix-daemon-protocol/src:$PYTHONPATH"
    export NIX_BIN=${lib.getExe nix}

    # **The nixpkgs of this repository, and not the one the host happens to
    # have.** `tests/nix/drv-probes.nix` imports `<nixpkgs>` at eight places,
    # and the shell used to take whatever `NIX_PATH` the machine set. A NixOS
    # host sets one, a GitHub runner sets none, and 21 tests failed there on
    # `file 'nixpkgs' was not found in the Nix search path` -- a message about
    # the machine and not about pynixd.
    #
    # `tests/derivations/pytest` and `tests/derivations/guest` already pin it
    # this way. This is the third consumer, and the one a person uses.
    export NIX_PATH="nixpkgs=${nixpkgsPath}"
  '';
}
