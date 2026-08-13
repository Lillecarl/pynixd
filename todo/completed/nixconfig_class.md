# Create a NixConfig class

Create a `NixConfig` class that renders into `nix.conf` compatible format.

Currently test configuration and daemon environment setup uses raw dicts
(`{"extra-experimental-features": "ca-derivations", "substituters": "..."}`)
rendered into `NIX_CONFIG` env vars or `--option` flags by hand.

A `NixConfig` class would:
- Define typed fields for common Nix settings (substituters, experimental-features, require-sigs, etc.)
- Render to `nix.conf` format (`key = value` lines) for `NIX_CONFIG` env var
- Render to `--option key value` args for daemon CLI
- Validate setting names against known Nix config keys