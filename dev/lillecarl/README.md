# Lillecarl Dev Environment

The `ai-nixos-rebuild*` commands have `sudo` NOPASSWD exemptions configured in `croshome/nixos/ai-rebuild.nix` — they are safe to run without password prompts.

```bash
# NixOS rebuild (deploys pynixd from source)
sudo ai-nixos-rebuild

# NixOS rebuild with pynixd daemon
sudo ai-nixos-rebuild-pynixd

# View pynixd systemd unit logs
journalctl --unit pynixd --lines 200

# Deploy nix-csi (nixkube) to lab Kubernetes and run test builds
nix run --file ~/Code/nix-csi/dev/lillecarl testClusterRun
```
