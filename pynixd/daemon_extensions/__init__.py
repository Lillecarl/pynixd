"""pynixd-specific daemon wire extensions.

These operations intentionally do not belong to the reusable Nix daemon
protocol package: upstream Nix daemons do not define their opcodes.
"""

from .probe_features import ProbeFeaturesRequest as ProbeFeaturesRequest
from .probe_features import ProbeFeaturesResponse as ProbeFeaturesResponse
from .probe_systems import ProbeSystemsRequest as ProbeSystemsRequest
from .probe_systems import ProbeSystemsResponse as ProbeSystemsResponse
from .protocol import PynixdGCAction as PynixdGCAction
from .pynixd_collect_garbage import PynixdCollectGarbageRequest as PynixdCollectGarbageRequest
from .pynixd_collect_garbage import PynixdCollectGarbageResponse as PynixdCollectGarbageResponse
from .query_closure import QueryClosureRequest as QueryClosureRequest
from .query_closure import QueryClosureResponse as QueryClosureResponse
from .query_closure_with_info import QueryClosureWithInfoRequest as QueryClosureWithInfoRequest
from .query_closure_with_info import QueryClosureWithInfoResponse as QueryClosureWithInfoResponse
from .query_derivation_output_map_batch import DerivationOutputMapBatchResponse as DerivationOutputMapBatchResponse
from .query_derivation_output_map_batch import (
    QueryDerivationOutputMapBatchRequest as QueryDerivationOutputMapBatchRequest,
)
from .query_path_infos import QueryPathInfosRequest as QueryPathInfosRequest
from .query_path_infos import QueryPathInfosResponse as QueryPathInfosResponse
from .sign_path_info import SignPathInfoRequest as SignPathInfoRequest
from .sign_path_info import SignPathInfoResponse as SignPathInfoResponse
