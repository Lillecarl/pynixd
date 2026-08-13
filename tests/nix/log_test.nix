{ system, ts }:

let
  pkgs = import <nixpkgs> { };
in

pkgs.stdenvNoCC.mkDerivation {
  name = "log-test-${ts}";
  dontUnpack = true;
  buildPhase = ''
    for i in $(seq 1 10); do
      echo "$i"
      sleep 1
    done
    mkdir -p $out
    echo "done" > $out/result
  '';
  installPhase = "true";
  _timestamp = ts;
}
