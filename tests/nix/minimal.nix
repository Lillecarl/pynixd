{ system, ts }:

let
  mkDrv =
    {
      name,
      builder ? "/bin/sh",
      args ? [
        "-c"
        "echo hello > $out"
      ],
      env ? { },
    }:
    derivation (
      {
        inherit
          name
          builder
          args
          system
          ;
        _timestamp = ts;
      }
      // env
    );

  leaf = mkDrv {
    name = "leaf";
    args = [
      "-c"
      "echo leaf > $out"
    ];
  };

  dependent = mkDrv {
    name = "dependent";
    args = [
      "-c"
      "echo ${leaf} > $out"
    ];
  };

in
{
  inherit leaf dependent;
}
