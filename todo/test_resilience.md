# TODO: Test Resilience & Error Handling

Implement functional tests for failure scenarios and edge cases in the daemon protocol.

## Scenarios to Test
*   **Backend Disconnection**:
    *   What happens if a backend store disconnects mid-operation?
    *   What happens if a build is in progress when the backend dies?
*   **Interrupted NAR Transfers**:
    *   Verify the "abrupt close" failure signaling for NAR transfers that fail after the `200 OK` header is sent.
*   **Protocol Handshake Mismatch**:
    *   Verify that `pynixd` correctly rejects (or downgrades) clients/backends with incompatible protocol versions.
*   **Invalid Requests**:
    *   Verify that malformed data sent by a client doesn't crash the server.

## Verification Criteria
- [ ] Server remains stable and continues to serve other clients after a failure.
- [ ] Failed operations are reported to the client as clean protocol errors where possible.
- [ ] No resources (connections, file handles) are leaked during failure scenarios.
