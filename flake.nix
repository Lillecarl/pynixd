{
  inputs = {
    flake-compatish.url = "github:lillecarl/flake-compatish";
    nixpkgs.url = "github:nixos/nixpkgs/nixpkgs-unstable";
  };
  outputs =
    inputs:
    let
      lib = inputs.nixpkgs.lib;
      forEachSystem = lib.genAttrs lib.systems.flakeExposed;
      eachPkgs = forEachSystem (system: import inputs.nixpkgs { inherit system; });
      eachDefNix = forEachSystem (system: import ./. { pkgs = eachPkgs.${system}; });
    in
    {
      packages = forEachSystem (
        system:
        let
          defNix = eachDefNix.${system};
        in
        {
          default = defNix.package;
          pynixd = defNix.package;
          libpynixd = defNix.library;
          nix-daemon-protocol = defNix.daemon-protocol;
          pynixd-docs = defNix.pynixd-docs;
        }
      );
      devShells = forEachSystem (
        system:
        let
          defNix = eachDefNix.${system};
        in
        {
          default = defNix.shell;
        }
      );
      legacyPackages = forEachSystem (system: eachPkgs.${system});
      nixosModules.default = ./nix/nixos;
    };
}
