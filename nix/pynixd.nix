{
  # nixpkgs
  lib,
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
pythonBuilder (finalAttrs: {
  pname = "pynixd";
  version = "0.1.0";
  pyproject = true;

  # A deliberate impurity, for measuring a rebuild without editing the source.
  # Commented out, not deleted: comment it back in when you need it again.
  #
  # Leave it in and the derivation hashes differently on every evaluation. That
  # makes the package unsubstitutable in principle rather than merely uncached
  # -- no `cachix push` can ever satisfy it, because by the time you push, the
  # next evaluation asks for a different path. It also leaks into every
  # consumer: nixkube's `cacheEnv` holds `pynixd-nixkube`, so an easykubenix
  # render that includes it hashes differently between runs, which breaks a
  # byte-identical render gate and makes `ekn deploy` commit on every run with
  # nothing changed.
  # impurity = builtins.currentTime or "";

  # `lib.hasSuffix` takes the suffix first, and these two read the other way
  # round until 2026-09-17. `hasSuffix name "nix"` asks whether the string
  # "nix" ends with the name of the file, which is true for a file called
  # exactly `nix` and for nothing else. So every `.nix` and every `.md` file
  # was in `src`, and an edit to one of them rebuilt this package and
  # everything downstream of it. Measured on the commit that removed the dead
  # asyncssh override: the derivation hash moved for an edit that changed no
  # input of the build.
  src = lib.cleanSourceWith {
    filter =
      name: type:
      lib.cleanSourceFilter name type && !lib.hasSuffix ".nix" name && !lib.hasSuffix ".md" name;
    src = ../.;
  };

  build-system = [ hatchling ];

  dependencies = [
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
