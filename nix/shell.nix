{
  lib,
  mkShell,
  just,
  pyright,
  ruff,
  pyupgrade,
  sqlite,
  python3,
  pynixd,
  nix,
  uml-runner,
}:
let
  python = python3.withPackages (
    ps:
    pynixd.dependencies
    ++ [
      ps.pytest
      ps.sphinx
      ps.myst-parser
      ps.furo
      # `tests/guest/run.py` imports it, and nothing else in this
      # repository does until a guest has booted. The guest derivation
      # checks the script against this same package; this is for editing
      # it, where `pyright .` would otherwise call the import unresolved.
      uml-runner
    ]
  );
in
mkShell {
  packages = [
    python
    just
    pyright
    ruff
    pyupgrade
    sqlite
  ];
  shellHook = ''
    export PYTHONPATH="$PWD:$PWD/nix-daemon-protocol/src:${python}/${python.sitePackages}:$PYTHONPATH"
    export NIX_BIN=${lib.getExe nix}
  '';
}
