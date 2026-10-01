# The performance benchmarks, in a guest.
#
# `nix run --file . tests.benchmark.driver -- --out <abs>` boots one container
# guest and runs the decode benchmark in it. A benchmark is not a gate: it
# prints numbers and passes unless the run itself fails. Kept out of
# `tests.guest` so the validation suites stay pass/fail and fast.
{
  pkgs,
  lib ? pkgs.lib,
  src,
  vivarium,
  # The dev shell's environment, so the benchmark imports what the suite would
  # and `nix_daemon_protocol` needs no second package set.
  devEnv,
}:

let
  vivariumLib = import (vivarium + "/lib.nix") { inherit pkgs lib; };
in
vivariumLib.mkTest (
  { config, ... }:
  {
    name = "pynixd-benchmark";

    knobs.backend = {
      env = "PYNIXD_GUEST_BACKEND";
      default = "container";
      description = "container, qemu, or uml";
    };
    backend = config.resolved.backend.value;

    settings = {
      src = "${src}";
    };

    phases = {
      decode = {
        script = ../../benchmark/run.py;
        after = [ "boot" ];
        nodes = [ "benchmark" ];
        description = "wire decode throughput, in a guest";
      };
    };

    nodes = {
      benchmark = {
        vivarium = {
          # The stream holds about a million messages; the 128M default
          # OOM-kills the agent partway through and reads as a hang.
          memory = "3072M";
          diskSize = 4096;
        };
        environment.systemPackages = [
          devEnv
          pkgs.coreutils
        ];
        # The benchmark uses no network and the guest has no route out.
        nix.settings.substituters = lib.mkForce [ ];
      };
    };
  }
)
