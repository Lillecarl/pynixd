{ pkgs
, pynixd-lib
, src
, system ? builtins.currentSystem
}:

let
  pytestEnv = pkgs.python3.withPackages (ps: [
    pynixd-lib
    ps.pytest
    ps.pytest-timeout
    ps.pyinstrument
  ]);
in
pkgs.runCommand "pynixd-pytest"
  {
    requiredSystemFeatures = [ "recursive-nix" ];
    __noSandbox = true;
    allowSubstitutes = false;
    buildInputs = [
      pkgs.nix
      pkgs.lix
      pkgs.openssh
      pkgs.bash
      pytestEnv
    ];
  }
  ''
    export HOME=$(mktemp -d)

    # Configure inner Nix daemon to disable sandboxing.
    mkdir -p $HOME/nix-config
    cat > $HOME/nix-config/nix.conf <<EOF
    sandbox = false
    experimental-features = nix-command recursive-nix
    EOF
    export NIX_CONF_DIR=$HOME/nix-config

    # Make nixpkgs available for test expressions that use <nixpkgs>
    export NIX_PATH="nixpkgs=${pkgs.path}"

    # Point tests at the Nix/Lix binaries
    export NIX_BIN=${pkgs.nix}/bin/nix
    export LIX_BIN=${pkgs.lix}/bin/nix

    # Copy source to a writable directory
    cp -r ${src} $HOME/src
    chmod -R +w $HOME/src
    cd $HOME/src

    # Ensure 'import tests.*' resolves from the project root
    export PYTHONPATH=$HOME/src

    # Run unit tests and functional tests that work inside a derivation.
    pytest -p no:cacheprovider --timeout=120 --tb=short \
      --ignore=tests/unit/test_drv_parser.py \
      tests/unit/ \
      tests/functional/test_add_to_store_nar.py \
      tests/functional/test_collect_garbage.py \
      tests/functional/test_persistence.py \
      tests/functional/test_scheduler_logic.py

    echo "All tests passed" > $out
  ''
