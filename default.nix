{
  pkgs ?
    let
      inputs =
        (
          let
            lock = builtins.fromJSON (builtins.readFile ./flake.lock);
            flake-compatish = import (fetchTree lock.nodes.flake-compatish.locked);
          in
          flake-compatish {
            source = ./.;
            overrides = {
              self = ./.;
              # use nixpkgs from NIX_PATH if set, else flake. Show notice to user
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
    import inputs.nixpkgs { },
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
