# Nix Daemon Trusted-User Gating

Source: `src/libstore/daemon.cc` in the Nix repo.

## Ops Requiring `trusted-users`

| Op | Condition | Error message |
|---|---|---|
| `BuildDerivation` | input-addressed (non-CA) derivations | `"you are not privileged to build input-addressed derivations"` |
| `BuildPaths` | `mode == bmRepair` | `"repairing is not allowed because you are not in 'trusted-users'"` |
| `BuildPathsWithResults` | `mode == bmRepair` | same |
| `VerifyStore` | `repair == true` | `"you are not privileged to repair paths"` |
| `AddPermRoot` | always | `"you are not privileged to create perm roots"` |
| `AddBuildLog` | always | `"you are not privileged to add logs"` |
| `AddToStore` / `AddMultipleToStore` / `AddToStoreNar` | `dontCheckSigs == true` | silently downgraded to `false` for untrusted |
| `SetOptions` | restricted settings (build-timeout, trusted substituters, etc.) | silently ignored for untrusted |
| `FindRoots` | always | untrusted only sees permanent roots, not temp roots |

## Ops That Do NOT Require Trusted Status

All read queries (`IsValidPath`, `QueryPathInfo`, `QueryValidPaths`, `QuerySubstitutablePaths`, `QueryReferrers`, `QueryDerivationOutputs`, `QueryMissing`, `QueryRealisation`, etc.), `AddTempRoot`, `AddIndirectRoot`, `BuildPaths`/`BuildPathsWithResults` in normal mode, `BuildDerivation` for CA derivations, `AddToStore`/`AddToStoreNar` with sigs checked, `AddTextToStore`, `EnsurePath`, `NarFromPath`, `OptimiseStore`, `SyncWithGC`, `RegisterDrvOutput`, `CollectGarbage` (except `ignoreLiveness` — rejected for everyone).

## Authentication Flow

1. Daemon creates socket with mode `0666`
2. Client connects → daemon calls `getPeerInfo(remote.get())` → `SO_PEERCRED` gets PID, UID, GID
3. `authPeer(peer)` maps UID against `trusted-users` and `allowed-users` in nix.conf
4. If neither list matches → connection rejected entirely
5. If only `allowed-users` matches → `TrustedFlag = NotTrusted`
6. If `trusted-users` matches → `TrustedFlag = Trusted`
7. The `TrustedFlag` is passed to `performOp()` which gates operations per the table above
