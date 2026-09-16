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

  nanopynix = import sources.nanopynix { };

  /*
    The interpreter of the development shell, resolved by pyproject.nix.

    **One set, and not a path of several.**  `nanopynix-testing` is the
    oracle of `tests/differential`, and `nanopynix/settings.py:15` imports
    `nanopynix_bindings`, so a PYTHONPATH assembled from store paths imports
    the packages and then fails on the first attribute.  Only a set that
    resolves the whole closure works, and mixing a package built in one set
    into another set's environment does not resolve either.

    `pythonSetWith` and not `pythonSet.overrideScope`: a set lifts its
    nixpkgs packages once, from the roots it was seeded with, so a project
    this repository owns has no way in afterwards.  `projectRoots` reads each
    `pyproject.toml` beside ours and resolves its dependencies the same way.
    nanopynix documents this as the seam for a consumer, and easykubenix uses
    it for `ekn`.

    Every root here is a real pyproject project, which is why this works at
    all: `uml-runner` carries one at `pkgs/uml-runner`, and it is what
    `tests/guest/run.py` imports.

    **pynixd itself does not depend on any of this, and must not.**  The
    shipped proxy is pure Python and links no C++; `nanopynix-testing`
    carries the bindings, built against one version of Nix.  This set builds
    the shell, and `nix/pynixd.nix` still builds the package.
  */
  devPythonSet = nanopynix.pythonSetWith {
    projectRoots = [
      ./.
      ./nix-daemon-protocol
      (sources.user-mode-nixos + "/pkgs/uml-runner")
    ];
    overlay = pySelf: _pyPrev: {
      pynixd = pySelf.callPackage (mkProject ./.) { };
      nix-daemon-protocol = pySelf.callPackage (mkProject ./nix-daemon-protocol) { };
      uml-runner = pySelf.callPackage (mkProject (sources.user-mode-nixos + "/pkgs/uml-runner")) { };
    };
  };

  mkProject =
    projectRoot:
    nanopynix.ps.mkProject {
      inherit projectRoot;
      inherit (nanopynix.pythonSet) python;
    };

  /*
    What `nix develop` puts on the path.  The two extras come from
    `pyproject.toml`, so the shell and the distribution read one list.
  */
  devEnv = devPythonSet.mkVirtualEnv "pynixd-dev-env" {
    pynixd = [
      "test"
      "docs"
    ];
    nanopynix-testing = [ ];
    uml-runner = [ ];
  };

  pyinstance = pkgs.python3.withPackages (
    ps:
    [ library ]
    ++ library.dependencies
    ++ [
      ps.pytest
      # `tests/guest/run.py` imports it, and the type gate reads that file.
      # `devEnv` gets the same package from its own pyproject root, because
      # the two environments resolve by different machinery: this one is
      # nixpkgs, and that one is pyproject.nix.
      uml-runner
    ]
  );

  # **The type gate does not see nanopynix, on purpose.** It reads `tests/`,
  # and `tests/differential` imports the oracle, so the two imports there
  # carry `pyright: ignore[reportMissingImports]`. Putting `nanopynix-testing`
  # in this environment would tie a gate that must run everywhere to a C++
  # closure built against one version of Nix.

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

  /*
    Nix's own functional suite, against a plain daemon and against pynixd.

    One program per Nix version, and not one program: the scripts come out of
    a Nix's own source, so the client, the scripts and the control daemon have
    to be the same version.  `nix/functional-tests/README.md` gives the
    commands; `streams` is the mode that records both runs and compares the
    wire.

    Below the floor the suite does not describe a daemon this proxy claims to
    serve.  See the protocol matrix in CLAUDE.md.
  */
  supportedNixFloor = "2.34";

  nixFunctionalTests =
    let
      named = lib.filterAttrs (name: _: lib.hasPrefix "nix_2_" name) pkgs.nixVersions;
      # `tryEval`, because a removed version is still an attribute and reading
      # its `version` throws: `error: nix_2_10 has been removed`.  Asking the
      # floor without this filter fails the whole evaluation of this file.
      supported = lib.filterAttrs (
        _: nix:
        let
          version = builtins.tryEval (lib.versions.majorMinor nix.version);
        in
        version.success && lib.versionAtLeast version.value supportedNixFloor
      ) named;
    in
    lib.mapAttrs (
      version: nix:
      pkgs.callPackage ./nix/functional-tests/package.nix {
        inherit nix version;
        pynixd = package;
        wirelogPython = pyinstance;
      }
    ) supported;

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
    nixFunctionalTests
    pkgs
    ;

  pynixd-docs = pkgs.python3Packages.callPackage ./nix/docs.nix { pynixd = library; };

  shell = pkgs.callPackage ./nix/shell.nix { inherit devEnv; };
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
