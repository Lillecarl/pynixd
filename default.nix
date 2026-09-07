let
  # nixidae is the umbrella that holds this repository, and it owns the
  # inputs. Inside it, that is the checkout one directory up. Outside it, it
  # is fetched, and this working copy is put in place of the submodule that
  # came down with it. Either way the answer is the same one, so a build here
  # and a build from the umbrella agree.
  #
  # git+https and not github:, because a GitHub tarball carries no submodule
  # and the siblings the umbrella wires are exactly what this is for.
  #
  # `..` from a store path leaves the store root, and Nix refuses that rather
  # than answering false: "'nix' is too short to be a valid store path". So
  # ask only when this checkout is not itself in the store.
  inUmbrella =
    builtins.substring 0 11 (toString ./.) != "/nix/store/"
    && builtins.pathExists ../nix/wire.nix;

  umbrella =
    if inUmbrella then
      import ../nix/wire.nix
    else
      import (
        (builtins.fetchTree (builtins.parseFlakeRef "git+https://github.com/nixidae/nixidae?submodules=1"))
        .outPath
        + "/nix/wire.nix"
      );

  # Set to make a `--file .` build agree with a flake evaluation. It turns
  # off the overrides the umbrella works through, so going out to fetch one
  # would cost a clone and change nothing.
  overridesDisabled =
    let
      value = builtins.getEnv "FLAKE_COMPATISH_DISABLE_OVERRIDES";
    in
    value != "" && value != "0";

  # What this file did before the umbrella: read flake.lock, and prefer a
  # nixpkgs from NIX_PATH over the locked one. The umbrella prefers the same
  # one, so the choice is unchanged and it is now made in a single place.
  own =
    (
      let
        lock = builtins.fromJSON (builtins.readFile ./flake.lock);
        flake-compatish = import (fetchTree lock.nodes.flake-compatish.locked);
      in
      flake-compatish {
        source = ./.;
        overrides = {
          self = ./.;
          nixpkgs =
            let
              result = builtins.tryEval <nixpkgs>;
            in
            if result.success then
              builtins.warn "using nixpkgs from NIX_PATH" result.value
            else
              builtins.warn "using nixpkgs from flake.lock" null;
        };
      }
    ).inputs;
in
{
  inputs ?
    if overridesDisabled then
      own
    else
      umbrella {
        project = "pynixd";
        source = ./.;
      },
  pkgs ? import inputs.nixpkgs { },
}:
let
  inherit (pkgs) lib;

  daemon-protocol = pkgs.python3Packages.callPackage ./nix/nix-daemon-protocol.nix {
    pythonBuilder = pkgs.python3Packages.buildPythonPackage;
  };

  package = pkgs.python3Packages.callPackage ./nix/pynixd.nix {
    pythonBuilder = pkgs.python3Packages.buildPythonApplication;
    nix-daemon-protocol = daemon-protocol;
  };
  library = pkgs.python3Packages.callPackage ./nix/pynixd.nix {
    pythonBuilder = pkgs.python3Packages.buildPythonPackage;
    nix-daemon-protocol = daemon-protocol;
  };

  mkTests =
    {
      name,
      testArgs,
    }:
    pkgs.writeShellApplication {
      inherit name;
      runtimeInputs = [
        (pkgs.python3.withPackages (ps: [
          library
          ps.pytest
          ps.pyinstrument
        ]))
      ];
      text = ''
        export NIX_BIN=${lib.getExe pkgs.nix}
        exec pytest -p no:cacheprovider --timeout=60 ${testArgs} "$@"
      '';
    };

  specifictest = mkTests {
    name = "pynixd-specifictest";
    testArgs = "";
  };
  lint =
    let
      pyinstance = pkgs.python3.withPackages (
        ps:
        [ library ]
        ++ library.dependencies
        ++ [
          ps.pytest
        ]
      );
    in
    pkgs.writeShellApplication {
      name = "pynixd-lint";
      runtimeInputs = [
        pyinstance
        pkgs.pyright
        pkgs.ruff
      ];
      text = ''
        src=${toString ./pynixd}
        echo "=== ruff fmt ==="
        ruff format "$src" ./tests || true
        echo "=== ruff check ==="
        ruff check --fix "$src" ./tests || true
        echo "=== pyright ==="
        pyright --pythonpath ${pyinstance}/bin/python "$src" ./tests || true
      '';
    };
in
package
// {
  inherit
    package
    library
    daemon-protocol
    specifictest
    lint
    pkgs
    ;

  pynixd-docs = pkgs.python3Packages.callPackage ./nix/docs.nix { pynixd = library; };

  shell = pkgs.callPackage ./nix/shell.nix { pynixd = package; };
  nixosModule = import ./nix/nixos/default.nix;

  tests = {
    simple = pkgs.callPackage ./tests/derivations/simple {
      pynixd-lib = library;
    };
    pytest = pkgs.callPackage ./tests/derivations/pytest {
      pynixd-lib = library;
      src = lib.cleanSource ./.;
    };
  };
}
