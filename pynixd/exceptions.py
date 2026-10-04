"""Shared exceptions for pynixd."""


class PynixdError(Exception):
    """Base for all pynixd errors."""


class InfrastructureError(PynixdError):
    """Transport/connection failure (SSH down, EOF, timeout)."""


class BackendError(PynixdError):
    """Raised when the backend sends STDERR_ERROR.

    The error has already been forwarded to the client.
    """


class ResourceExhaustedError(PynixdError):
    """Raised when system resources are too stressed to proceed (PSI/Load)."""


class ClosingError(PynixdError):
    """An error that ends the session after the client reads it.

    `processConnection` in `src/libstore/daemon.cc` closes the connection
    when an operation throws before `startWork`, since the arguments may be
    unread. An operation that refuses a client there raises this.
    """


class OpNotImplementedError(PynixdError):
    """Raised when an operation is not implemented for a specific executor (e.g. DB)."""


class GCNotPermittedError(PynixdError):
    """Raised when a delete is asked before the liveness mirror is proven.

    The collector plans freely -- a dry-run deletes nothing -- but EXECUTE
    stays refused until the operator sets `gc_allow_execute`, which is the
    signature after sustained zero-divergence between the mirror and what
    Nix reports alive. Fail closed: an unproven mirror must not name
    deletions, and a refused delete must not read as success.
    """
