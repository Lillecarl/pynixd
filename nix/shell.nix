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
  '';
}
