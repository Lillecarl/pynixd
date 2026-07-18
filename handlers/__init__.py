"""Handler package — importing all modules registers them in HANDLER_REGISTRY."""

from . import (
    add_build_log,  # noqa: F401
    add_indirect_root,  # noqa: F401
    add_multiple_to_store,  # noqa: F401
    add_perm_root,  # noqa: F401
    add_temp_root,  # noqa: F401
    add_to_store,  # noqa: F401
    add_to_store_nar,  # noqa: F401
    build_derivation,  # noqa: F401
    collect_garbage,  # noqa: F401
    nar_from_path,  # noqa: F401
    optimise_store,  # noqa: F401
    pynixd_collect_garbage,  # noqa: F401
    set_options,  # noqa: F401
    sign_path_info,  # noqa: F401
    verify_store,  # noqa: F401
)
