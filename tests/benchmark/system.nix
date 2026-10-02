# A minimal NixOS container system, plus a pile of impure derivations.
#
# Two paths, and the system alone only exercises one of them. Evaluating
# nixpkgs asks the daemon about every derivation -- an IsValidPath and an
# AddTempRoot each -- and the realisation substitutes the closure. It does not
# *build* anything, because a minimal system is all in the store.
#
# So a thousand impure derivations ride along, chained under one goal.
# A derivation is impure the moment it reads `builtins.currentTime`: its hash
# moves with the clock, so it is never the path a substituter holds and the
# daemon must build it. Each does nothing, so the cost is the request stream
# and the realisation -- the load a client puts on the front-end that
# evaluating nixpkgs alone does not.
#
# The goal is its own derivation whose inputs are the system and the noise, so
# one `nix build` realises all of it rather than substituting a system whose
# closure is already in the store.
{ hostName ? "rawbench" }:
let
  pkgs = import <nixpkgs> { };
  lib = pkgs.lib;
  count = 1000;

  deps = lib.genList (n: pkgs.runCommand "bench-noise-${hostName}-${toString n}" {
    stamp = toString builtins.currentTime;
  } "touch $out") count;

  # `toString deps` is the store paths as a string, so this drv reads them and
  # cannot be realised until every one of them is.
  top = pkgs.runCommand "bench-top-${hostName}" { } ''
    touch $out
    echo ${toString deps}
  '';

  system = pkgs.nixos (
    { modulesPath, ... }:
    {
      imports = [ (modulesPath + "/profiles/minimal.nix") ];
      boot.isContainer = true;
      networking.hostName = hostName;
      nixpkgs.hostPlatform = "x86_64-linux";
      system.stateVersion = lib.mkDefault "24.11";
    }
  );
in
pkgs.runCommand "bench-goal-${hostName}" { } ''
  touch $out
  echo ${system.config.system.build.toplevel} ${top}
''
