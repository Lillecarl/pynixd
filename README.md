# pynixd

[![Documentation](https://img.shields.io/badge/docs-lillecarl.github.io-blue)](https://lillecarl.github.io/pynixd/)

> Built by me with a bunch of AI models

A Nix daemon protocol proxy and distributed build cache implemented in Python using AsyncSSH. pynixd acts as an intermediary between Nix clients and remote builders, providing query caching, build deduplication, and intelligent scheduling across multiple build backends.

## nanopynix umbrella

`pynixd` is preparing to be the daemon-service project under the broader
`nanopynix` Python-and-Nix umbrella. It remains a standalone package and Nix
project while the integration shape is decided. The intended ownership boundary
and staged convergence plan are documented in [pynixd in the nanopynix
umbrella](docs/umbrella.md).

## Features

- **Nix Daemon Protocol Proxy**: Implements the Nix daemon wire protocol over SSH, allowing Nix clients to connect and route builds to remote machines
- **Build Caching**: Deduplicates concurrent builds for the same derivation, reducing redundant work
- **Multi-Backend Scheduling**: Distributes builds across SSH-connected builder machines with locality-aware scheduling
- **Local Query Cache**: Serves path queries (IsValidPath, QueryPathInfo, NarFromPath) from a local store, avoiding repeated remote lookups
- **HTTP Binary Cache**: Optional built-in HTTP cache server for serving NARs to substituters
- **Proactive Path Transfer**: Prefetches input paths to backup backends while waiting for primary slots

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│   Client    │────▶│   pynixd     │────▶│   Local     │
│  Connection │     │   Router     │     │   Store     │
└─────────────┘     └──────┬───────┘     └─────────────┘
                           │
                    ┌──────▼───────┐
                    │ Build Queue  │
                    │   Scheduler  │
                    └──────┬───────┘
                           │
         ┌─────────────────┼─────────────────┐
         ▼                 ▼                 ▼
   ┌───────────┐   ┌───────────┐   ┌───────────┐
   │  Backend  │   │  Backend  │   │  Backend  │
   │  (SSH)    │   │  (SSH)    │   │  (SSH)    │
   └───────────┘   └───────────┘   └───────────┘
```

### Components

- **ClientConnection**: Handles Nix protocol exchange with connected clients
- **LocalStore**: Connects to local nix-daemon for queries and cache storage
- **BuildQueue**: Global queue for build deduplication and scheduling
- **Scheduler**: Assigns builds to backends based on locality and slot availability
- **BackendPool**: Manages persistent SSH connections to builder machines
- **OutputPuller**: Pulls build outputs from builders to local store after successful builds

## Installation

```bash
pip install .
```

Or with [uv](https://github.com/astral-sh/uv):

```bash
uv pip install .
```

## Usage

### Configuration

pynixd is configured entirely via environment variables and a backend definition file.

**Backend Definition File** (JSON):

```json
[
  {
    "type": "ssh-subprocess",
    "host": "builder1.example.com",
    "id": "builder1",
    "username": "nix",
    "max_builds": 4,
    "supported_systems": ["x86_64-linux"]
  },
  {
    "type": "ssh-subprocess",
    "host": "builder2.example.com",
    "id": "builder2",
    "username": "nix",
    "max_builds": 2,
    "supported_systems": ["x86_64-linux", "aarch64-linux"]
  }
]
```

**Store Types**:
- `ssh-subprocess`: SSH with `nix-daemon --stdio` (recommended)
- `ssh-socket`: SSH tunnel to remote Unix socket
- `local-socket`: Local daemon via Unix socket
- `local-subprocess`: Local nix-daemon subprocess

### Running pynixd

```bash
export PYNIXD_BACKEND_FILE=/path/to/backends.json
export PYNIXD_HOST=0.0.0.0
export PYNIXD_PORT=2234
export PYNIXD_LOG_LEVEL=INFO

python -m pynixd
```

### Environment Variables

**SSH Server**:
| Variable | Default | Description |
|----------|---------|-------------|
| `PYNIXD_HOST` | `127.0.0.1` | Listen address |
| `PYNIXD_PORT` | `2234` | Listen port |
| `PYNIXD_HOST_KEY` | _(generated)_ | Path to SSH host key |
| `PYNIXD_BACKEND_FILE` | _(required)_ | JSON file with backend definitions |
| `PYNIXD_DEV` | `0` | Dev mode: spawn N local builders |
| `PYNIXD_LOG_LEVEL` | `WARNING` | Log level: DEBUG, INFO, WARNING, ERROR |

**HTTP Binary Cache**:
| Variable | Default | Description |
|----------|---------|-------------|
| `PYNIXD_HTTP_PORT` | `0` | HTTP cache port (0 to disable) |
| `PYNIXD_HTTP_HOST` | `0.0.0.0` | HTTP listen address |
| `PYNIXD_HTTP_USER` | _(none)_ | HTTP basic auth username |
| `PYNIXD_HTTP_PASS` | _(none)_ | HTTP basic auth password |
| `PYNIXD_HTTP_PRIORITY` | `30` | Binary cache priority |

### Development Mode

For local testing without SSH:

```bash
export PYNIXD_DEV=2  # spawn 2 local builders
export PYNIXD_LOG_LEVEL=DEBUG
python -m pynixd
```

### Nix Client Usage

Point Nix at pynixd's SSH socket:

```bash
nix build --builders ssh-ng://localhost?port=2234 /path/to/expr
```

Or via Nix daemon configuration in `nix.conf`:

```
builders = ssh-ng://localhost?port=2234
```

## Dependencies

Versions are managed by nixpkgs:

- Python >= 3.12
- asyncssh
- aiohttp
- aiosqlite >= 0.21
- pyinstrument

[Documentation](https://lillecarl.github.io/pynixd/)

---

*This project is made possible by*

[![Dynamist](.assets/dynamist-logo.png)](https://dynamist.se/)

## License

MIT
