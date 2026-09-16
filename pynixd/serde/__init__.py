"""Deprecated compatibility facade for :mod:`nix_daemon_protocol`.

Only the package-level re-exports below are left, so `from pynixd.serde import
StorePath` still works. Name the real module instead: `nix_daemon_protocol` for
the standard wire models, `pynixd.daemon_extensions` for pynixd's own.

`_derivation_compat` is not a re-export. Importing it is what puts
`effective_required_features` and `output_paths` on `BasicDerivation`, so this
module must stay imported even though nothing reads a name from it.
"""

from nix_daemon_protocol import *  # noqa: F403

from ..daemon_extensions import *  # noqa: F403
from . import _derivation_compat as _derivation_compat
from .auth import Role as Role
from .context import ReadContext as ReadContext, RequestContext as RequestContext, WriteContext as WriteContext
from .protocol import PynixdGCAction as PynixdGCAction
