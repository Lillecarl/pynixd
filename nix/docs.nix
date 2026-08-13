{
  lib,
  stdenvNoCC,
  python,
  pynixd,
  sphinx,
  myst-parser,
  furo,
}:
let
  pythonEnv = python.withPackages (
    _: pynixd.dependencies ++ [
      pynixd
      sphinx
      myst-parser
      furo
    ]
  );
in
stdenvNoCC.mkDerivation {
  pname = "pynixd-docs";
  version = "0";

  src = lib.cleanSource ../.;

  nativeBuildInputs = [ pythonEnv ];

  buildPhase = ''
    runHook preBuild
    PYNIXD_DOCS_OFFLINE=1 sphinx-build -b html docs "$out" -W
    runHook postBuild
  '';

  dontInstall = true;
}
