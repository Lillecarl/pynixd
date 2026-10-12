/**
  `pynixd-agent`, the host sampler pynixd copies to a builder and runs over
  SSH. Issue #98.

  Call it with `pkgsStatic.callPackage`: the binary then has no store
  references, so its closure is one path on a builder of any distribution.
*/
{ stdenv }:
stdenv.mkDerivation {
  pname = "pynixd-agent";
  version = "1";
  src = ../agent;
  dontConfigure = true;
  buildPhase = ''
    runHook preBuild
    $CC -O2 -Wall -Wextra -Werror -std=c11 -o pynixd-agent pynixd-agent.c
    runHook postBuild
  '';
  installPhase = ''
    runHook preInstall
    install -Dm755 pynixd-agent $out/bin/pynixd-agent
    runHook postInstall
  '';
  meta.mainProgram = "pynixd-agent";
}
