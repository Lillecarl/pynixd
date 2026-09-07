{
  # nixpkgs
  lib,
  fetchFromGitHub,
  # building
  pythonBuilder,
  hatchling,
  # testing
  pytest,
  # dependencies
  aiohttp,
  pyinstrument,
  aiosqlite,
  environs,
  pynacl,
  passlib,
  cachetools,
  zstandard,
  lz4,
  brotli,
  asyncssh,
  structlog,
  pydantic,
  pydantic-settings,
  prometheus-client,
  anyio,
  uvloop,
  nix-daemon-protocol,
}:
let
  overrides = {
    asyncssh = asyncssh.overrideAttrs {
      src = fetchFromGitHub {
        # type = "github";
        repo = "asyncssh";
        owner = "ronf";
        rev = "v2.23.1";
        hash = "sha256-6x/Ww25G9MmVIdUJjpPgzNAza0Qx7VArQN6BgPHsIc4=";
      };
      doCheck = false;
      doInstallCheck = false;
    };
  };
in
pythonBuilder (finalAttrs: {
  pname = "pynixd";
  version = "0.1.0";
  pyproject = true;

  impurity = builtins.currentTime or ""; # don't remove this, just comment it in or out

  src = lib.cleanSourceWith {
    filter =
      name: type: lib.cleanSourceFilter name type && !lib.hasSuffix name "nix" && !lib.hasSuffix name ".md";
    src = ../.;
  };

  build-system = [ hatchling ];

  dependencies = [
    # overrides.asyncssh
    asyncssh
    structlog
    aiohttp
    pyinstrument
    aiosqlite
    environs
    pynacl
    passlib
    cachetools
    zstandard
    lz4
    brotli
    pydantic
    pydantic-settings
    prometheus-client
    anyio
    uvloop
    nix-daemon-protocol
  ];

  nativeCheckInputs = [
    pytest
  ];

  meta = {
    description = "Python Nix daemon protocol proxy over SSH";
    mainProgram = "pynixd";
  };
})
