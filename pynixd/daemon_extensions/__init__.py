"""pynixd-specific daemon wire extensions.

These operations intentionally do not belong to the reusable Nix daemon
protocol package: upstream Nix daemons do not define their opcodes.
"""

from .probe_features import ProbeFeaturesRequest as ProbeFeaturesRequest, ProbeFeaturesResponse as ProbeFeaturesResponse
from .probe_systems import ProbeSystemsRequest as ProbeSystemsRequest, ProbeSystemsResponse as ProbeSystemsResponse
from .protocol import PynixdGCAction as PynixdGCAction
from .pynixd_collect_garbage import (
    PynixdCollectGarbageRequest as PynixdCollectGarbageRequest,
    PynixdCollectGarbageResponse as PynixdCollectGarbageResponse,
)
from .query_closure import QueryClosureRequest as QueryClosureRequest, QueryClosureResponse as QueryClosureResponse
from .query_closure_with_info import (
    QueryClosureWithInfoRequest as QueryClosureWithInfoRequest,
    QueryClosureWithInfoResponse as QueryClosureWithInfoResponse,
)
from .query_derivation_output_map_batch import (
    DerivationOutputMapBatchResponse as DerivationOutputMapBatchResponse,
    QueryDerivationOutputMapBatchRequest as QueryDerivationOutputMapBatchRequest,
)
from .query_path_infos import (
    QueryPathInfosRequest as QueryPathInfosRequest,
    QueryPathInfosResponse as QueryPathInfosResponse,
)
from .sign_path_info import SignPathInfoRequest as SignPathInfoRequest, SignPathInfoResponse as SignPathInfoResponse
