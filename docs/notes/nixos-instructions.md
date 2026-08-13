# How AI can rebuild my system to iterate on pynixd as a system daemon
## Check logs
```bash
journalctl -u pynixd --since -"$seconds"s
```
Checks the logs of the program since it last started
## Rebuild without pynixd
```bash
time timeout 300 sudo ai-nixos-rebuild # <important>don't pipe away output!! </important>
```
## Rebuild with pynixd
```bash
time timeout 300 sudo ai-nixos-rebuild-pynixd # <important>don't pipe away output!!!</important>
```
## Build with eval store Nix, build store pynixd
```bash
time timeout 300 nix build --eval-store unix:///nix/var/nix/daemon-socket/socket --store unix:///run/pynixd/pynixd.sock --no-link --file ~/Code/croshome oc.system.build.toplevel
```
## Build with eval store pynixd, build store pynixd
```bash
time timeout 300 nnix build --store unix:///run/pynixd/pynixd.sock --no-link --file ~/Code/croshome oc.system.build.toplevel
```
## Build with eval store nix, build store nix
```bash
time timeout 300 nnix build --no-link --file ~/Code/croshome oc.system.build.toplevel
```
## Collect garbage
```bash
nix-collect-garbage
```
## Filter logs
edit pynixd/filters/scheduler_focus.py

# Goal
Make sure pynixd doesn't spend time doing things it doesn't have to do

## How to accomplish
Add relevant "tracing logs"

## rebuilding pynixd
In default.nix there's an impurity which you can turn on or off to rebuild pynixd which causes a lot slower builds for some reason (so it's a good benchmark)
