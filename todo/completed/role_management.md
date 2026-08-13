# TODO: Implement Role-Based Access Control (RBAC)

Implement a role management system to restrict sensitive operations based on the connection type and user identity.

## Roles
*   **`admin`**: Full access, including maintenance operations (GC, Optimise, etc.).
*   **`user`**: Restricted access, can perform builds and queries but not maintenance.

## Mapping Logic
*   **Unix Socket**: Connections over the local Unix socket are implicitly granted the `admin` role.
*   **SSH**: The role must be determined by mapping the SSH username to a role.
    *   Need a configuration mechanism (e.g., environment variable or JSON file) to define `admin` usernames.
    *   Default role for unknown SSH users should be `user`.

## Restricted Operations (Admin Only)
*   `CollectGarbage`
*   `OptimiseStore`
*   `VerifyStore`
*   `AddBuildLog` (investigate if this should be restricted)
*   `SignPathInfo` (extension)

## Verification Criteria
- [ ] SSH users mapped to `admin` can perform restricted operations.
- [ ] SSH users mapped to `user` receive a protocol error when attempting restricted operations.
- [ ] Unix socket connections always have `admin` privileges.
- [ ] Role mapping is configurable without changing code. (Piggyback on PYNIXD_BACKEND_FILE for this)
