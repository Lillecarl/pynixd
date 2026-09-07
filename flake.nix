# The public surface of this repository, for a consumer who uses flakes.
#
# **This is not how the repository builds.** `default.nix` is, and the nixidae
# umbrella hands it every source. This file exists because flakes have the
# market share: it lets somebody write `inputs.pynixd.url = "github:..."` and
# get a curated set of outputs, rather than nothing.
#
# So it holds no logic. It names what is public and calls `default.nix`, and
# a change to how anything is built happens there.
#
# Two inputs, and neither duplicates the umbrella's pins.
#
#   nixpkgs   The consumer's, and the point of the exercise. It is handed to
#             the umbrella in place of the revision nix/sources.lock names,
#             so `inputs.pynixd.inputs.nixpkgs.follows = "nixpkgs"` does what
#             a flake user expects it to.
#
#   nixidae   Which umbrella, and nothing else. `nix/sources.nix` finds one
#             by an impure fetch, which a flake evaluation cannot do, so the
#             lock beside this file pins it instead. Every other source comes
#             from that revision's own nix/sources.lock.
#
# `flake.lock` here therefore has two nodes and pins nothing twice.
{
  description = "The Nix daemon protocol in Python";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixpkgs-unstable";
    nixidae = {
      url = "github:nixidae/nixidae";
      flake = false;
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      nixidae,
    }:
    let
      inherit (nixpkgs) lib;
      forAllSystems = lib.genAttrs lib.systems.flakeExposed;

      # The umbrella's own set, with the consumer's nixpkgs in place of the
      # one it names.
      sources = import "${nixidae}/nix/wire.nix" {
        overrides.nixpkgs = nixpkgs.outPath;
      };

      each = forAllSystems (
        system:
        import ./. {
          inherit sources;
          pkgs = import nixpkgs {
            inherit system;
            config.allowUnfree = true;
          };
        }
      );
    in
    {
      packages = forAllSystems (system: {
        default = each.${system}.package;
        pynixd = each.${system}.package;
        libpynixd = each.${system}.library;
        nix-daemon-protocol = each.${system}.daemon-protocol;
        pynixd-docs = each.${system}.pynixd-docs;
      });

      devShells = forAllSystems (system: {
        default = each.${system}.shell;
      });

      nixosModules.default = ./nix/nixos;
    };
}
