"""BuildResult and related types — build outcome on the Nix daemon wire."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import IntEnum

from .constants import FEATURE_REALISATION_WITH_PATH, proto
from .opt_microseconds import OptMicroseconds
from .realisation import Realisation
from .store_path import StorePath
from .unkeyed_realisation import UnkeyedRealisation
from .wire_message import WireField, WireModel

# The highest status byte a Nix client can decode. `buildResultStatusTable` in
# `src/libstore/common-protocol.cc` holds 15 entries, and the reader rejects any
# index at or above that size. `BuildResult.wire_status` is what keeps this
# project inside it.
MAX_WIRE_STATUS = 14


class BuildResultStatus(IntEnum):
    """Build result status codes from nix daemon protocol."""

    BUILT = 0
    SUBSTITUTED = 1
    ALREADY_VALID = 2
    RESOLVES_TO_ALREADY_VALID = 13

    PERMANENT_FAILURE = 3
    INPUT_REJECTED = 4
    OUTPUT_REJECTED = 5
    TRANSIENT_FAILURE = 6
    CACHED_FAILURE = 7
    TIMED_OUT = 8
    MISC_FAILURE = 9
    DEPENDENCY_FAILED = 10
    LOG_LIMIT_EXCEEDED = 11
    NOT_DETERMINISTIC = 12
    NO_SUBSTITUTERS = 14

    HASH_MISMATCH = 101
    UNKNOWN = 102

    @property
    def is_success(self) -> bool:
        return self in {
            self.BUILT,
            self.SUBSTITUTED,
            self.ALREADY_VALID,
            self.RESOLVES_TO_ALREADY_VALID,
        }

    @property
    def is_failure(self) -> bool:
        return not self.is_success


class BuildMode(IntEnum):
    """Build mode flags from nix daemon protocol."""

    NORMAL = 0
    REPAIR = 1
    CHECK = 2


@dataclass
class BuiltOutput:
    """A built output - either plain path or content-addressed (CA) format."""

    out_path: StorePath = field(default_factory=lambda: StorePath(path=""))
    ca: str = ""
    hash: str = ""
    hash_algo: str = ""
    nar_hash: str = ""
    nar_size: int = 0
    reference: str = ""

    @classmethod
    def from_string(cls, s: str) -> BuiltOutput:
        if not s:
            return cls()
        try:
            data = json.loads(s)
            if isinstance(data, dict):
                return cls(
                    out_path=StorePath(path=data.get("outPath", "")),
                    ca=data.get("ca", ""),
                    hash=data.get("hash", ""),
                    hash_algo=data.get("hashAlgo", ""),
                    nar_hash=data.get("narHash", ""),
                    nar_size=data.get("narSize", 0),
                    reference=data.get("reference", ""),
                )
        except (json.JSONDecodeError, TypeError):
            pass
        return cls(out_path=StorePath(path=s))

    def to_string(self) -> str:
        if self.ca or self.hash or self.nar_hash:
            data: dict[str, str | int] = {"outPath": str(self.out_path)}
            if self.ca:
                data["ca"] = self.ca
            if self.hash:
                data["hash"] = self.hash
            if self.hash_algo:
                data["hashAlgo"] = self.hash_algo
            if self.nar_hash:
                data["narHash"] = self.nar_hash
            if self.nar_size:
                data["narSize"] = self.nar_size
            if self.reference:
                data["reference"] = self.reference
            return json.dumps(data)
        return str(self.out_path)


class BuildResult(WireModel):
    """Nix daemon protocol BuildResult.

    Fields present based on protocol version:
    - All versions: status, error_msg
    - >= 1.29: times_built, is_non_deterministic, start_time, stop_time
    - >= 1.37: cpu_user, cpu_system
    - >= 1.28: built_outputs (dict[str, str])
    """

    status: int = 0
    error_msg: str = ""

    # Protocol 1.29 fields
    times_built: int | None = WireField(default=None, min_version=proto(1, 29))
    is_non_deterministic: int | None = WireField(default=None, min_version=proto(1, 29))
    start_time: int | None = WireField(default=None, min_version=proto(1, 29))
    stop_time: int | None = WireField(default=None, min_version=proto(1, 29))

    # Protocol 1.37 fields
    cpu_user: OptMicroseconds = WireField(default_factory=OptMicroseconds, min_version=proto(1, 37))
    cpu_system: OptMicroseconds = WireField(default_factory=OptMicroseconds, min_version=proto(1, 37))

    # Protocol 1.28 fields
    #
    # **`builtOutputs` of Nix is two fields, and a feature picks which one.**
    # `worker-protocol.cc:268` writes it as an if/else. With
    # `realisation-with-path-not-hash` it is a map of output name to
    # `UnkeyedRealisation`; without it, and from 1.28, it is a map of
    # `"<drvHash>!<output>"` to a JSON `Realisation`. Both live at 1.38, so a
    # version alone cannot separate them. Issue #162.
    built_outputs: dict[str, Realisation] | None = WireField(
        default=None,
        min_version=proto(1, 28),
        unless_features=[FEATURE_REALISATION_WITH_PATH],
    )
    built_outputs_by_name: dict[str, UnkeyedRealisation] | None = WireField(
        default=None,
        needs_features=[FEATURE_REALISATION_WITH_PATH],
    )
    """The map that the feature shape carries, keyed by the output name alone.

    The key of `built_outputs` is a whole `DrvOutput`; this one names the
    output of the derivation that the answer is already about, so it needs no
    derivation in the key. `SUPPORTED_STANDARD_FEATURES` is empty, so nothing
    fills this in yet.
    """

    def wire_status(self) -> int:
        """The status byte to send, which is not always the one held.

        A hash mismatch is a kind of output rejection, which is the mapping Nix
        chose and the one a client expects. Everything else out of range
        becomes a plain failure: the detail belongs in `error_msg`, which has
        no such limit, and a status a client cannot read carries no detail at
        all.
        """
        if self.status == BuildResultStatus.HASH_MISMATCH:
            return int(BuildResultStatus.OUTPUT_REJECTED)
        if 0 <= self.status <= MAX_WIRE_STATUS:
            return self.status
        return int(BuildResultStatus.MISC_FAILURE)

    def for_the_wire(self) -> BuildResult:
        """This result, with a status a client can decode.

        Call this before sending a result to a client. It is a method and not a
        `to_writer` override on purpose: `experimental_compiled` refuses to
        compile a model that overrides its codec (`_can_compile`), and
        `BuildResult` is the model its own tests compile as the example. A
        correctness fix that quietly turned off the fast path for the hottest
        model on the wire would be a poor trade, so the knowledge of what is
        wire-safe lives here and the caller decides when to apply it.
        """
        wire_status = self.wire_status()
        if wire_status == self.status:
            return self
        return self.model_copy(update={"status": wire_status})
