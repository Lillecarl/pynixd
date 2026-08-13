{
  pkgs,
  system,
  ts,
}:

let
  mkDrv =
    {
      name,
      deps ? [ ],
      sleepSecs ? 0,
      text ? "",
    }:
    pkgs.stdenvNoCC.mkDerivation (
      {
        name = "${name}-${ts}";
        buildInputs = deps;
        dontUnpack = true;
        _timestamp = ts;
        buildPhase = ''
          echo "Building ${name} (sleeping ${toString sleepSecs}s)..."
          sleep ${toString sleepSecs}
          mkdir -p $out/bin
          cat > $out/bin/${name} << SCRIPT
          #!/bin/sh
          echo "${name}: ${text} (built at ${ts})"
          SCRIPT
          chmod +x $out/bin/${name}
        '';
        installPhase = "true";
      }
      // (if builtins.getEnv "PYNIXD_LOCALBUILD" == "1" then { pynixd_fast = "1"; } else { })
    );

  mkParallel =
    {
      leaves,
      sleepSecs,
      parId ? "",
    }:
    let
      prefix = if parId == "" then "" else "${parId}-";
      parLeaves = builtins.genList (
        i:
        mkDrv {
          name = "${prefix}par-${toString i}";
          inherit sleepSecs;
          text = "parallel ${prefix}${toString i} ${ts}";
        }
      ) leaves;
    in
    mkDrv {
      name = "${prefix}par-root";
      deps = parLeaves;
      sleepSecs = 0;
      text = "parallel root ${ts}";
    };

  a0 = mkDrv {
    name = "leaf-a0";
    sleepSecs = 2;
    text = "leaf a0";
  };
  a1 = mkDrv {
    name = "leaf-a1";
    sleepSecs = 1;
    text = "leaf a1";
  };
  a2 = mkDrv {
    name = "leaf-a2";
    sleepSecs = 3;
    text = "leaf a2";
  };
  a3 = mkDrv {
    name = "leaf-a3";
    sleepSecs = 1;
    text = "leaf a3";
  };

  b0 = mkDrv {
    name = "mid-b0";
    deps = [ a0 ];
    sleepSecs = 1;
    text = "depends a0";
  };
  b1 = mkDrv {
    name = "mid-b1";
    deps = [
      a0
      a1
    ];
    sleepSecs = 1;
    text = "depends a0+a1";
  };
  b2 = mkDrv {
    name = "mid-b2";
    deps = [
      a1
      a2
    ];
    sleepSecs = 2;
    text = "depends a1+a2";
  };
  b3 = mkDrv {
    name = "mid-b3";
    deps = [ a2 ];
    sleepSecs = 1;
    text = "depends a2";
  };
  b4 = mkDrv {
    name = "mid-b4";
    deps = [ a3 ];
    sleepSecs = 1;
    text = "depends a3";
  };
  b5 = mkDrv {
    name = "mid-b5";
    deps = [
      a2
      a3
    ];
    sleepSecs = 1;
    text = "depends a2+a3";
  };

  c0 = mkDrv {
    name = "top-c0";
    deps = [
      b0
      b1
      b2
    ];
    sleepSecs = 1;
    text = "fan-in b0+b1+b2";
  };
  c1 = mkDrv {
    name = "top-c1";
    deps = [
      b3
      b4
    ];
    sleepSecs = 1;
    text = "fan-in b3+b4";
  };
  c2 = mkDrv {
    name = "top-c2";
    deps = [
      b4
      b5
    ];
    sleepSecs = 1;
    text = "fan-in b4+b5";
  };

  root = mkDrv {
    name = "root";
    deps = [
      c0
      c1
      c2
    ];
    sleepSecs = 1;
    text = "root";
  };

  parCount =
    let
      v = builtins.getEnv "PYNIXD_PAR_COUNT";
    in
    if v == "" then 100 else builtins.fromJSON v;
  parId =
    let
      v = builtins.getEnv "PYNIXD_PAR_ID";
    in
    if v == "" then "" else v;
  parSleep =
    let
      v = builtins.getEnv "PYNIXD_PAR_SLEEP";
    in
    if v == "" then 2 else builtins.fromJSON v;

in
{
  simple = pkgs.writeShellApplication {
    name = "test-${ts}";
    text = "hello ${ts}";
  };

  dag = root;

  parallel = mkParallel {
    leaves = parCount;
    sleepSecs = parSleep;
    inherit parId;
  };

  bench = mkParallel {
    leaves = parCount;
    sleepSecs = 0;
    inherit parId;
  };

  bench-100mb = mkParallel {
    leaves = 100;
    sleepSecs = 0;
    parId = "bench-100mb";
  };

  big = pkgs.stdenvNoCC.mkDerivation {
    name = "big-${ts}";
    dontUnpack = true;
    buildPhase = ''
      mkdir -p $out
      dd if=/dev/zero of=$out/big-file bs=1M count=10
    '';
    installPhase = "true";
  };
}
