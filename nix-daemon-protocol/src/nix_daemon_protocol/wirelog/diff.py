"""Compare two recordings, and name the first operation that differs.

Run A puts a client against `nix-daemon`. Run B puts the same client against
pynixd, which puts it against the same `nix-daemon`. If pynixd keeps its
contract, the two recordings agree.

## What may differ, and why

pynixd is not a copy of `nix-daemon`, and four differences are on purpose.
Each one is an entry of `EXEMPTIONS`, each entry states its reason, and the
report says which entry it applied. A difference that no entry covers is a
finding.

**The comparison does not read the log messages, and that is a gap.** pynixd
writes lines of its own -- "pynixd: starting build on local at ..." -- and it
writes them at its own times, so a comparison of the text would report a
difference for every build. A log that only one side wrote is still a real
change for a person who reads the output, so this is worth doing later, with
the timestamps and the "pynixd: " lines taken out first. `Operation.logs`
holds the text, and it waits for that work.

Issue #175.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .decode import Handshake, Operation, Session


@dataclass(frozen=True)
class Exemption:
    """A field that may differ between the two runs, and the reason."""

    field: str
    reason: str


EXEMPTIONS: tuple[Exemption, ...] = (
    Exemption(
        field="handshake.nix_version",
        reason=(
            "pynixd states its own name and version, and not the version of Nix. "
            "`DaemonProxy.handshake` writes NIX_VERSION, which is `pynixd-0.1.0`."
        ),
    ),
    Exemption(
        field="handshake.server_version",
        reason=(
            "pynixd presents the protocol version that it supports, and not the one of "
            "its local store. That is what lets a client negotiate 1.38 features against "
            "an older store, and `DaemonProxy.handshake` says so."
        ),
    ),
    Exemption(
        field="handshake.server_features",
        reason=(
            "pynixd adds a `feature_matrix:` entry for each system that a build store "
            "can serve. A plain daemon has no builders and states none of them."
        ),
    ),
    Exemption(
        field="handshake.trusted",
        reason=(
            "pynixd answers TRUSTED to a client of its Unix socket, because that client "
            "reached a socket that pynixd owns. A daemon decides from the user."
        ),
    ),
)

EXEMPT_FIELDS: frozenset[str] = frozenset(item.field for item in EXEMPTIONS)


@dataclass(frozen=True)
class Difference:
    """One thing that run B did and run A did not."""

    where: str
    """`handshake.trusted`, or `operation 12 QueryPathInfo response`."""

    control: str
    candidate: str

    def __str__(self) -> str:
        return f"{self.where}\n    daemon: {self.control}\n    pynixd: {self.candidate}"


def _brief(value: bytes, limit: int = 96) -> str:
    if len(value) <= limit:
        return repr(value)
    return f"{value[:limit]!r}... ({len(value)} bytes)"


def _compare_handshake(control: Handshake | None, candidate: Handshake | None) -> list[Difference]:
    if control is None or candidate is None:
        if control is candidate:
            return []
        return [Difference("handshake", str(control is not None), str(candidate is not None))]

    found: list[Difference] = []
    for name in (
        "server_version",
        "client_version",
        "negotiated",
        "client_features",
        "server_features",
        "nix_version",
        "trusted",
    ):
        if f"handshake.{name}" in EXEMPT_FIELDS:
            continue
        one = getattr(control, name)
        two = getattr(candidate, name)
        if one != two:
            found.append(Difference(f"handshake.{name}", repr(one), repr(two)))
    return found


def _compare_operation(control: Operation, candidate: Operation) -> list[Difference]:
    where = f"operation {control.index} {control.name}"
    found: list[Difference] = []

    if control.op != candidate.op:
        return [
            Difference(
                f"operation {control.index}", f"{control.name} ({control.op})", f"{candidate.name} ({candidate.op})"
            )
        ]
    if control.request_body != candidate.request_body:
        # The client sends the request, so a difference here means the client
        # took a different path, and an earlier answer of pynixd caused it.
        found.append(Difference(f"{where} request", _brief(control.request_body), _brief(candidate.request_body)))
    if control.response_payload != candidate.response_payload:
        found.append(
            Difference(f"{where} response", _brief(control.response_payload), _brief(candidate.response_payload))
        )
    if (control.error is None) != (candidate.error is None):
        found.append(Difference(f"{where} error", repr(control.error), repr(candidate.error)))
    return found


def compare(control: Session, candidate: Session) -> list[Difference]:
    """Every difference between two recordings of one workload."""
    found: list[Difference] = []

    for session, role in ((control, "daemon"), (candidate, "pynixd")):
        if session.problem is not None:
            found.append(Difference(f"{role} recording did not decode", str(session.source), session.problem))

    found.extend(_compare_handshake(control.handshake, candidate.handshake))

    shared = min(len(control.operations), len(candidate.operations))
    for index in range(shared):
        found.extend(_compare_operation(control.operations[index], candidate.operations[index]))

    if len(control.operations) != len(candidate.operations):
        # The client stopped early against one of the two, which is what a
        # difference in an answer looks like from the outside.
        extra_control = [op.name for op in control.operations[shared:]]
        extra_candidate = [op.name for op in candidate.operations[shared:]]
        found.append(
            Difference(
                f"the client sent {len(control.operations)} operations to the daemon "
                f"and {len(candidate.operations)} to pynixd",
                repr(extra_control),
                repr(extra_candidate),
            )
        )

    return found


def report(differences: list[Difference]) -> str:
    """The differences as text, and the exemptions that the reader should know."""
    if not differences:
        return "the two recordings agree"

    lines = [f"=== {len(differences)} DIFFERENCES ==="]
    lines.extend(str(item) for item in differences)
    lines.append("")
    lines.append("These fields are exempt, and each one is on purpose:")
    lines.extend(f"  {item.field}: {item.reason}" for item in EXEMPTIONS)
    return "\n".join(lines)
