# A minimal NixOS container system, plus a pile of impure derivations.
#
# Two paths, and the system alone only exercises one of them. Evaluating
# nixpkgs asks the daemon about every derivation -- an IsValidPath and an
# AddTempRoot each -- and the realisation substitutes the closure. It does not
# *build* anything, because a minimal system is all in the store.
#
# So ten thousand impure derivations ride along in `system.extraDependencies`.
# A derivation is impure the moment it reads `builtins.currentTime`: its hash
# moves with the clock, so it is never the path a substituter holds and the
# daemon must build it. Each does nothing, so the cost is the request stream
# and the realisation -- the load a client puts on the front-end that
# evaluating nixpkgs alone does not.
{ hostName ? "rawbench" }:
let
  nixpkgs = import <nixpkgs> { };
  lib = nixpkgs.lib;

  noise =
    n:
    nixpkgs.runCommand "bench-noise-${hostName}-${toString n}" {
      stamp = toString builtins.currentTime;
    } "touch $out";

  system = nixpkgs.nixos (
    { modulesPath, ... }:
    {
      imports = [ (modulesPath + "/profiles/minimal.nix") ];
      boot.isContainer = true;
      networking.hostName = hostName;
      nixpkgs.hostPlatform = "x86_64-linux";
      system.stateVersion = lib.mkDefault "24.11";
      system.extraDependencies = map noise (lib.range 1 10000);
    }
  );
in
system.config.system.build.toplevel
