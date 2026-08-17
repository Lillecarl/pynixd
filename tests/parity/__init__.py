"""pynixd against Nix, with Nix as the oracle.

Each test here runs the real `nix` and compares what pynixd produces with what
Nix produces for the same input. A recording of the wire is one such
comparison, and a signature over one store path is another.

They need `nix` on the PATH and they skip without it, so they run in the dev
shell and not in the build sandbox of `checks.pynixd`.
"""
