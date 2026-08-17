"""The output path that a realisation names, and not the derivation.

`Store::queryPartialDerivationOutputMap` answers the path of each output of a
derivation. For an input-addressed output the derivation names that path. For
a deferred, floating or impure output the derivation names nothing, and the
store answers instead: a realisation maps
`DrvOutput{staticOutputHashes(drv)[name], name}` to the path that the build
made.

**A derivation that names no output path is not therefore unbuilt.** Two goals
of pynixd read the derivation alone and drew that conclusion, and each one was
wrong in its own way:

- `EnsureDerivedPathGoal` built the derivation again. Issue #185.
- `QueryMissingPlanGoal` answered `willBuild`, and `nix-daemon` answers an
  empty set, because `misc.cc:217` reads the same map and finds every output
  valid.

Issue #175.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from ..drv_hash import output_hashes
from ..serde import DrvOutput, QueryRealisationRequest

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ..drv_parser import Derivation
    from ..serde import Realisation
    from ..store.base import Store

log = structlog.get_logger(__name__)


async def realisations_of(
    parsed: Derivation,
    wanted: Iterable[str],
    store: Store,
) -> dict[str, Realisation] | None:
    """The realisation of each wanted output, keyed by its `DrvOutput` id.

    The id is `sha256:<digest>!<output name>`, which is the string form that
    `DrvOutput::to_string` writes and the wire carries.

    Answers `None` when any wanted output has no realisation, and also when
    the closure gives no hash. A dynamic input derivation does that, and so
    does an input derivation that the store does not hold. The caller then
    knows nothing about the outputs, and it must not read the empty answer as
    "no output is realised".

    This does not say that the path is valid. A realisation stays in the store
    after a garbage collection removes the path it names, so the caller checks
    the path as well.
    """
    names = sorted(wanted)
    if not names:
        return None
    hashes = await output_hashes(parsed, store.read_derivation)
    if hashes is None:
        return None

    answer: dict[str, Realisation] = {}
    for output_name in names:
        digest = hashes.get(output_name)
        if digest is None:
            return None
        key = f"sha256:{digest}!{output_name}"
        response = await store.execute(QueryRealisationRequest(drv_output=DrvOutput(key)))
        realisation = next(iter(response.realisations), None)
        if realisation is None or realisation.out_path is None:
            log.debug("realisation_missing", drv_output=key)
            return None
        answer[key] = realisation
    return answer
