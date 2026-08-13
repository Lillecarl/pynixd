{ system, ts }:

let
  mkFeatureDrv =
    {
      name,
      feature,
      builder ? "/bin/sh",
      args ? [
        "-c"
        "echo ${feature} > $out"
      ],
    }:
    derivation {
      inherit
        name
        builder
        args
        system
        ;
      _timestamp = ts;
      requiredSystemFeatures = [ feature ];
    };

in
{
  nixos-test = mkFeatureDrv {
    name = "nixos-test";
    feature = "nixos-test";
  };

  benchmark = mkFeatureDrv {
    name = "benchmark";
    feature = "benchmark";
  };

  big-parallel = mkFeatureDrv {
    name = "big-parallel";
    feature = "big-parallel";
  };

  uid-range = mkFeatureDrv {
    name = "uid-range";
    feature = "uid-range";
  };

  kvm = mkFeatureDrv {
    name = "kvm";
    feature = "kvm";
    args = [
      "-c"
      "test -w /dev/kvm && echo kvm > $out || { echo 'kvm: /dev/kvm not writable' >&2; exit 1; }"
    ];
  };

  apple-virt = mkFeatureDrv {
    name = "apple-virt";
    feature = "apple-virt";
  };

  ca-derivations = mkFeatureDrv {
    name = "ca-derivations";
    feature = "ca-derivations";
  };

  recursive-nix = mkFeatureDrv {
    name = "recursive-nix";
    feature = "recursive-nix";
  };
}
