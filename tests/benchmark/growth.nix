# Workload for the growth benchmark: one impure build that shouts.
#
# 50,000 log lines exercise the whole log path (`post_log`, fan-out, replay,
# client delivery); a 1 MiB output exercises transfer and volume accounting.
# `GROWTH_ITER` comes from the environment under `--impure`, so every
# iteration builds a fresh output path at the same cost shape instead of
# substituting the last one.
{ }:
let
  pkgs = import <nixpkgs> { };
  iter = builtins.getEnv "GROWTH_ITER";
in
pkgs.runCommand "growth-build-${iter}" { } ''
  mkdir -p $out
  printf "%s" "${iter}" > $out/iter
  awk 'BEGIN { for (i = 0; i < 50000; i++) print "growth log line " i " xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" > "/dev/stderr" }'
  head -c 1048576 /dev/urandom > $out/blob
''
