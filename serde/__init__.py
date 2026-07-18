"""Deprecated compatibility facade for :mod:`nix_daemon_protocol`.

New reusable code should import standard daemon wire models from
``nix_daemon_protocol``. pynixd-specific extension operations remain here for
the duration of the migration.
"""

import importlib
import sys

from nix_daemon_protocol import *  # noqa: F403

from ..daemon_extensions import *  # noqa: F403
from . import _derivation_compat as _derivation_compat
from .auth import Role as Role
from .context import ReadContext as ReadContext
from .context import RequestContext as RequestContext
from .context import WriteContext as WriteContext
from .protocol import PynixdGCAction as PynixdGCAction

_CORE_MODULES = (
    "add_build_log",
    "add_indirect_root",
    "add_multiple_to_store",
    "add_perm_root",
    "add_signatures",
    "add_temp_root",
    "add_to_store",
    "add_to_store_nar",
    "aliases",
    "basic_derivation",
    "build_derivation",
    "build_paths",
    "build_paths_with_results",
    "build_result",
    "collect_garbage",
    "content_address",
    "derivation_output",
    "derived_path",
    "drv_output",
    "ensure_path",
    "find_roots",
    "ids",
    "is_valid_path",
    "keyed_build_result",
    "logs",
    "nar_from_path",
    "nar_hash",
    "opt_microseconds",
    "optimise_store",
    "path_info",
    "query_all_valid_paths",
    "query_derivation_output_map",
    "query_missing",
    "query_path_from_hash_part",
    "query_path_info",
    "query_realisation",
    "query_referrers",
    "query_substitutable_paths",
    "query_valid_derivers",
    "query_valid_paths",
    "realisation",
    "register_drv_output",
    "set_options",
    "signature",
    "store_path",
    "valid_path_info",
    "verify_store",
    "wire_message",
    "wire_ops",
    "wire_string",
    "wire_time",
)
_EXTENSION_MODULES = (
    "probe_features",
    "probe_systems",
    "pynixd_collect_garbage",
    "query_closure",
    "query_closure_with_info",
    "query_derivation_output_map_batch",
    "query_path_infos",
    "sign_path_info",
)

for _name in _CORE_MODULES:
    sys.modules[f"{__name__}.{_name}"] = importlib.import_module(f"nix_daemon_protocol.{_name}")
for _name in _EXTENSION_MODULES:
    sys.modules[f"{__name__}.{_name}"] = importlib.import_module(f"pynixd.daemon_extensions.{_name}")
