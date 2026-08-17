"""Compare two recordings, and name the first operation that differs.

Run A puts a client against `nix-daemon`. Run B puts the same client against
pynixd, which puts it against the same `nix-daemon`. If pynixd keeps its
contract, the two recordings agree.

## What may differ, and why

pynixd is not a copy of `nix-daemon`, and each difference that is on purpose
is an entry of `EXEMPTIONS`. Each entry states its reason, and the report says
which entry it applied. A difference that no entry covers is a finding.

**The comparison reads the log messages as well as the answers.** A person
reads those lines, so a line that only one of the two wrote is a change even
when every byte of the answer agrees. Two kinds of line are exempt, and
`EXEMPTIONS` gives the reason for each: a line that pynixd writes about
itself, and the "removing stale temporary roots file" line, whose pid differs
between any two runs.

The comparison reads `STDERR_NEXT` alone. The activities -- `STDERR_START_ACTIVITY`,
`STDERR_RESULT` and the rest -- carry the progress bar of `nix build`, and the
decoder drops them. That is the gap that remains.

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
        field="response.*.registration_time",
        reason=(
            "The registration time of a store path is the second in which the store "
            "took the path. The control run and the candidate run add the same path at "
            "two times, so this differs between any two runs and says nothing about "
            "pynixd. `UnkeyedValidPathInfo.registration_time` holds it."
        ),
    ),
    Exemption(
        field="handshake.trusted",
        reason=(
            "pynixd answers TRUSTED to a client of its Unix socket, because that client "
            "reached a socket that pynixd owns. A daemon decides from the user."
        ),
    ),
    Exemption(
        field="logs.pynixd_note",
        reason=(
            "A log line that starts with `pynixd: ` is a note of pynixd about itself, "
            "and the daemon writes none. Section 3 of `pynixd/CLAUDE.md` asks for these: "
            "a cached answer says so, and a build says where it goes."
        ),
    ),
    Exemption(
        field="logs.stale_temp_root",
        reason=(
            "`removing stale temporary roots file` names the pid of a worker that ended. "
            "The two runs have two process histories, so the pid and the number of such "
            "lines differ between any two runs and say nothing about pynixd."
        ),
    ),
)

EXEMPT_FIELDS: frozenset[str] = frozenset(item.field for item in EXEMPTIONS)

# The name of a field of a response, whatever model holds it. `response.*.`
# is the prefix that `EXEMPTIONS` writes, and the part after it is the name.
RESPONSE_PREFIX = "response.*."
EXEMPT_RESPONSE_LEAVES: frozenset[str] = frozenset(
    item.field.removeprefix(RESPONSE_PREFIX) for item in EXEMPTIONS if item.field.startswith(RESPONSE_PREFIX)
)


def _is_exempt_leaf(name: str) -> bool:
    """True for a field of a response that `EXEMPTIONS` covers by its name."""
    return name.rsplit(".", 1)[-1] in EXEMPT_RESPONSE_LEAVES


@dataclass(frozen=True)
class Difference:
    """One thing that run B did and run A did not."""

    where: str
    """`handshake.trusted`, or `operation 12 QueryPathInfo response`."""

    control: str
    candidate: str

    note: str = ""
    """Where in the two values the difference is, for a pair of byte strings."""

    def __str__(self) -> str:
        head = f"{self.where}\n" if not self.note else f"{self.where}\n    {self.note}\n"
        return f"{head}    daemon: {self.control}\n    pynixd: {self.candidate}"


# How much of a byte string to show around the byte that differs.
BEFORE = 16
AFTER = 48


def _first_difference(control: bytes, candidate: bytes) -> int:
    """The index of the first byte that the two do not share."""
    shared = min(len(control), len(candidate))
    for index in range(shared):
        if control[index] != candidate[index]:
            return index
    return shared


def _window(value: bytes, at: int) -> str:
    """`value` around byte `at`, with an ellipsis for what is left out."""
    start = max(0, at - BEFORE)
    end = min(len(value), at + AFTER)
    head = "..." if start else ""
    tail = "..." if end < len(value) else ""
    return f"{head}{value[start:end]!r}{tail}"


def _pair(control: bytes, candidate: bytes) -> tuple[str, str, str]:
    """Two byte strings, shown around the first byte where they differ.

    A whole answer is too long to read and its first bytes are usually equal,
    so a plain prefix of each one shows two lines that look the same. This
    names the byte and shows that byte.
    """
    at = _first_difference(control, candidate)
    note = f"they differ at byte {at}; the daemon sent {len(control)} bytes and pynixd sent {len(candidate)}"
    return _window(control, at), _window(candidate, at), note


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


def _compare_response(where: str, control: Operation, candidate: Operation) -> list[Difference]:
    """The answers differ. Name the fields when a model covers the answer."""
    one_fields = control.response_fields
    two_fields = candidate.response_fields
    if one_fields is None or two_fields is None:
        one, two, note = _pair(control.response_payload, candidate.response_payload)
        return [Difference(f"{where} response", one, two, note)]

    found: list[Difference] = []
    for name in sorted(set(one_fields) | set(two_fields)):
        if _is_exempt_leaf(name):
            continue
        one_value = one_fields.get(name, "(absent)")
        two_value = two_fields.get(name, "(absent)")
        if one_value != two_value:
            found.append(Difference(f"{where} response.{name}", one_value, two_value))
    return found


PYNIXD_NOTE = "pynixd: "
STALE_TEMP_ROOT = "removing stale temporary roots file"


def _readable_logs(logs: tuple[str, ...]) -> tuple[str, ...]:
    """The log lines that a comparison reads, with the exempt ones taken out.

    Two rules, and `EXEMPTIONS` holds the reason for each. Both rules apply to
    both sides: a daemon writes neither kind of line, so dropping the line
    from the control side as well changes nothing and keeps the rule one rule.
    """
    return tuple(line for line in logs if not line.startswith(PYNIXD_NOTE) and STALE_TEMP_ROOT not in line)


def _compare_logs(where: str, control: Operation, candidate: Operation) -> list[Difference]:
    """The log messages of one operation, line by line.

    `nix-daemon` writes these as `STDERR_NEXT`, and a person reads them. A
    line that only one of the two wrote is a change to that person even when
    every byte of the answer agrees, so the comparison reads them.
    """
    one = _readable_logs(control.logs)
    two = _readable_logs(candidate.logs)
    if one == two:
        return []

    only_control = [line for line in one if line not in two]
    only_candidate = [line for line in two if line not in one]
    if not only_control and not only_candidate:
        return [Difference(f"{where} logs (a different order)", repr(one), repr(two))]
    return [Difference(f"{where} logs", repr(only_control), repr(only_candidate))]


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
        one, two, note = _pair(control.request_body, candidate.request_body)
        found.append(Difference(f"{where} request", one, two, note))
    if control.response_payload != candidate.response_payload:
        found.extend(_compare_response(where, control, candidate))
    if (control.error is None) != (candidate.error is None):
        found.append(Difference(f"{where} error", repr(control.error), repr(candidate.error)))
    found.extend(_compare_logs(where, control, candidate))
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
    """The differences as text."""
    if not differences:
        return "the two recordings agree"

    lines = [f"=== {len(differences)} DIFFERENCES ==="]
    lines.extend(str(item) for item in differences)
    return "\n".join(lines)


def exemptions() -> str:
    """The fields that the comparison passes over, and the reason for each.

    Printed once for a run, and not once for each connection. A reader needs
    this to read the report, and a copy of it above every connection makes the
    report harder to read rather than easier.
    """
    lines = ["These fields are exempt, and each one is on purpose:"]
    lines.extend(f"  {item.field}: {item.reason}" for item in EXEMPTIONS)
    return "\n".join(lines)
