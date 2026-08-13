{
  lib,
  pythonBuilder,
  hatchling,
  pydantic,
}:
pythonBuilder (finalAttrs: {
  pname = "nix-daemon-protocol";
  version = "0.1.0";
  pyproject = true;

  src = lib.cleanSource ../nix-daemon-protocol;

  build-system = [ hatchling ];
  dependencies = [ pydantic ];

  meta = {
    description = "Declarative Python codecs for the Nix daemon wire protocol";
  };
})
