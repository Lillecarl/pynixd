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
