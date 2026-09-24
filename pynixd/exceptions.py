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
