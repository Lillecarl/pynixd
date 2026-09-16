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
      # `tests/guest/run.py` imports it. Without it here, `pyright .`
      # reports the import as unresolved -- and the script is the one
      # piece of Python in this repository that nothing imports until a
      # guest has booted, so it is the piece a type checker is worth most
      # on. The guest derivation checks it as well, against this same
      # package; this is for editing it.
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
