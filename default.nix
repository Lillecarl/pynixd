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
  uml-runner = (import (sources.user-mode-nixos + "/lib.nix") { inherit pkgs; }).runner;

  pyinstance = pkgs.python3.withPackages (
    ps:
    [ library ]
    ++ library.dependencies
    ++ [
      ps.pytest
      # `tests/guest/run.py` imports it, and the type gate reads that file.
      # See nix/shell.nix, which adds it for the same reason.
      uml-runner
    ]
  );

  /*
    The fixer.  It rewrites files, so it is not named like a gate and nothing
    in CI runs it.

    `ruff format` and `ruff check --fix` exit 0 once they have rewritten what
    they found.  A CI step that calls them therefore passes on code that fails
    the check, and the rewrite is thrown away with the runner.  The gates in
    `checks` below call the non-mutating forms for that reason.
  */
  fix = pkgs.writeShellApplication {
    name = "pynixd-fix";
    runtimeInputs = [
      pyinstance
      pkgs.ruff
    ];
    text = ''
      ruff format .
      ruff check --fix .
    '';
  };

  # One derivation per gate, so a failure names which one, and each fails the
  # build rather than reporting into a log nobody reads.
  mkCheck =
    name: deps: text:
    pkgs.runCommand "pynixd-check-${name}" { nativeBuildInputs = deps; } ''
      cp -r ${lib.cleanSource ./.} src
      chmod -R +w src
      cd src
      ${text}
      touch "$out"
    '';

  checks = {
    format = mkCheck "format" [ pkgs.ruff ] "ruff format --check .";
    lint = mkCheck "lint" [ pkgs.ruff ] "ruff check .";
    types = mkCheck "types" [
      pkgs.pyright
      pyinstance
    ] "pyright --pythonpath ${pyinstance}/bin/python .";
  };
in
package
// {
  inherit
    package
    library
    daemon-protocol
    specifictest
    fix
    checks
    pkgs
    ;

  pynixd-docs = pkgs.python3Packages.callPackage ./nix/docs.nix { pynixd = library; };

  shell = pkgs.callPackage ./nix/shell.nix {
    pynixd = package;
    # What `tests/guest/run.py` imports, so the shell's pyright resolves it.
    uml-runner = (import (sources.user-mode-nixos + "/lib.nix") { inherit pkgs; }).runner;
  };
  nixosModule = import ./nix/nixos/default.nix;

  tests = {
    simple = pkgs.callPackage ./tests/derivations/simple {
      pynixd-lib = library;
    };
    pytest = pkgs.callPackage ./tests/derivations/pytest {
      pynixd-lib = library;
      src = lib.cleanSource ./.;
    };

    # The same suites in a guest, where cleanup is a poweroff rather than
    # a promise.
    guest = pkgs.callPackage ./tests/derivations/guest {
      pynixd-lib = library;
      src = lib.cleanSource ./.;
      inherit (sources) user-mode-nixos;
    };
  };
}
