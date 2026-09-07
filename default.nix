{
  # Where every dependency lives, as directories. nix/sources.nix says how
  # this repository finds the umbrella that owns them.
  sources ? import ./nix/sources.nix,
  pkgs ? import sources.nixpkgs {
    config.allowUnfree = true;
  },
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
