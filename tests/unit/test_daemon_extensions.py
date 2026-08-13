"""Every operation that pynixd adds to the wire registry declares itself.

`WIRE_REGISTRY` in `nix_daemon_protocol` is one dictionary for the process, and
`WireRequest.__init_subclass__` fills it. An import of `pynixd` therefore adds
each operation of `pynixd/daemon_extensions/` to the registry of the protocol
package. `is_extension` is what tells the two kinds apart.

`ProbeFeaturesRequest` did not set it, and `ProbeSystemsRequest` beside it did.
Two things followed:

- `get_extension_features()` left `ProbeFeatures` out of the set that pynixd
  advertises.
- `DaemonProxy` reads `request.is_extension` to decide what to do when the
  local store cannot run an operation. It re-raised `OpNotImplementedError`
  for `ProbeFeatures` instead of asking the other stores.

Neither one had a test. `test_operation_manifest` found the flag by accident,
and only in a run that held both suites in one process.
"""

from __future__ import annotations

import pynixd  # noqa: F401 -- the import is what registers each extension
from nix_daemon_protocol.operations import STANDARD_OPERATIONS
from nix_daemon_protocol.wire_ops import WIRE_REGISTRY

_STANDARD_CODES = frozenset(operation.code for operation in STANDARD_OPERATIONS)


def test_every_registered_operation_outside_the_manifest_is_an_extension() -> None:
    """The complement of `test_operation_manifest`, and the half that failed."""
    undeclared = sorted(
        (code, request.name) for code, request in WIRE_REGISTRY.items() if code not in _STANDARD_CODES and not request.is_extension
    )
    assert not undeclared, (
        f"these operations are outside STANDARD_OPERATIONS and do not set `is_extension`: {undeclared}. "
        "Add `is_extension: ClassVar[bool] = True` to each one. Until you do, pynixd does not advertise it "
        "and DaemonProxy treats a request for it as a standard operation."
    )


def test_no_extension_claims_a_standard_operation_code() -> None:
    """An extension on a standard code would shadow the standard request."""
    stolen = sorted(
        (code, request.name) for code, request in WIRE_REGISTRY.items() if code in _STANDARD_CODES and request.is_extension
    )
    assert not stolen, f"these extensions use a code of the standard manifest: {stolen}"


def test_pynixd_advertises_each_operation_it_adds() -> None:
    """`get_extension_features()` is the set that reaches a client.

    Compared against the operation **codes** outside the manifest, and not
    against `is_extension`. `get_extension_features()` reads that same flag,
    so a comparison against it holds whatever the flag says.
    """
    added = {request.name for code, request in WIRE_REGISTRY.items() if code not in _STANDARD_CODES}
    assert pynixd.protocol.get_extension_features() == added
