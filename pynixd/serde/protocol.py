"""Compatibility exports for daemon protocol enums plus pynixd extensions."""

from nix_daemon_protocol.protocol import (
    ActivityType as ActivityType,
    FieldType as FieldType,
    FileIngestionMethod as FileIngestionMethod,
    GCAction as GCAction,
    OptTrusted as OptTrusted,
    ResultType as ResultType,
    Verbosity as Verbosity,
)

from ..daemon_extensions.protocol import PynixdGCAction as PynixdGCAction
