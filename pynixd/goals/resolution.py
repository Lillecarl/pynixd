"""Derivation resolution for deferred and dynamic derivations.

When a deferred derivation depends on a CA derivation, the BuildDerivation
wire protocol sends a BasicDerivation (no inputDrvs) which the daemon cannot
resolve. This module implements the Nix `tryResolve` + `rewriteDerivation`
algorithm to resolve deferred derivations before sending them to the daemon.

For dynamic derivations (DrvWithVersion), wrapper derivations contain
DownstreamPlaceholder references for nested dynamic outputs (drv^out^out).
These must be resolved to actual store paths after the trampoline build
completes, using the unknownDerivation placeholder variant.

The resolution flow:
1. Compute DownstreamPlaceholder for each input derivation output
2. Build a placeholder -> actual_path rewrite map
3. Apply rewrites to builder, args, and env
4. Move inputDrv/dynamicInputDrv outputs into inputSrcs
5. Compute hashDerivationModulo on the resolved derivation (masked)
6. Derive output paths via makeOutputPath
7. Convert Deferred outputs to InputAddressed
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import structlog

from ..drv_parser import ChildMapNode, _aterm_escape
from ..serde import BasicDerivation, DerivationOutput, StorePath as SerdeStorePath
from ..store_path import StorePath
from ..utils import nix32_encode

if TYPE_CHECKING:
    from ..drv_parser import Derivation

log = structlog.get_logger(__name__)

STORE_DIR = "/nix/store"


def _output_path_name(drv_name: str, output_name: str) -> str:
    """Return the store path basename for a derivation output."""
    if output_name == "out":
        return drv_name
    return f"{drv_name}-{output_name}"


def _nix_store_path_name(store_path_str: str) -> str:
    """Extract the name portion (after the first dash) from a store path."""
    basename = store_path_str.rsplit("/", 1)[-1]
    first_dash = basename.find("-")
    if first_dash == -1:
        return basename
    return basename[first_dash + 1 :]


def _nix_drv_name(drv_path: StorePath) -> str:
    """Return the derivation name without the .drv extension."""
    name_with_ext = _nix_store_path_name(str(drv_path))
    if name_with_ext.endswith(".drv"):
        return name_with_ext[:-4]
    return name_with_ext


def downstream_placeholder(drv_path: StorePath, output_name: str) -> str:
    """Compute the DownstreamPlaceholder hash for a given derivation output."""
    hash_part = str(drv_path).rsplit("/", 1)[-1].split("-", 1)[0]
    drv_name = _nix_drv_name(drv_path)
    clear_text = f"nix-upstream-output:{hash_part}:{_output_path_name(drv_name, output_name)}"
    h = hashlib.sha256(clear_text.encode()).digest()
    return "/" + nix32_encode(h)


def downstream_placeholder_unknown_derivation(
    parent_placeholder_hash: bytes,
    output_name: str,
) -> str:
    """DownstreamPlaceholder::unknownDerivation — for nested dynamic outputs.

    When a derivation output is itself a .drv (dynamic derivation), and
    we reference an output of that inner .drv, we compute the placeholder
    by hashing the parent placeholder (compressed) with the output name.

    Corresponds to Nix's DownstreamPlaceholder::unknownDerivation().
    """
    return "/" + nix32_encode(
        downstream_placeholder_unknown_derivation_raw(
            parent_placeholder_hash,
            output_name,
        ),
    )


def downstream_placeholder_unknown_derivation_raw(
    parent_placeholder_hash: bytes,
    output_name: str,
) -> bytes:
    """Raw hash for DownstreamPlaceholder::unknownDerivation."""
    compressed = _compress_hash(parent_placeholder_hash, 20)
    clear_text = f"nix-computed-output:{nix32_encode(compressed)}:{output_name}"
    return hashlib.sha256(clear_text.encode()).digest()


def _compress_hash(data: bytes, new_size: int) -> bytes:
    """XOR-fold *data* down to *new_size* bytes (Nix hash compression)."""
    result = bytearray(new_size)
    for i in range(len(data)):
        result[i % new_size] ^= data[i]
    return bytes(result)


def _make_store_path(
    type_str: str,
    hash_modulo: bytes,
    name: str,
    store_dir: str = STORE_DIR,
) -> str:
    """Build a store path string from a type prefix, content hash, and name (Nix makeStorePath)."""
    hash_str = "sha256:" + hash_modulo.hex()
    s = f"{type_str}:{hash_str}:{store_dir}:{name}"
    digest = hashlib.sha256(s.encode()).digest()
    compressed = _compress_hash(digest, 20)
    return f"{store_dir}/{nix32_encode(compressed)}-{name}"


def _make_output_path(
    output_id: str,
    hash_modulo: bytes,
    drv_name: str,
    store_dir: str = STORE_DIR,
) -> str:
    """Derive an output store path for a given output ID (Nix makeOutputPath)."""
    name = _output_path_name(drv_name, output_id)
    return _make_store_path(f"output:{output_id}", hash_modulo, name, store_dir)


def _unparse_basic_derivation(drv: BasicDerivation, mask_outputs: bool = True) -> str:
    """Serialize a BasicDerivation to ATerm format, optionally masking output paths."""
    parts: list[str] = ["Derive("]

    out_parts: list[str] = []
    for name, o in sorted(drv.outputs.items()):
        path = "" if mask_outputs else o.path
        out_parts.append(
            f'("{name}","{_aterm_escape(path)}","{_aterm_escape(o.method)}","{_aterm_escape(o.hash_digest)}")',
        )
    parts.append(f"[{','.join(out_parts)}],")

    parts.append("[],")

    srcs = ",".join(f'"{_aterm_escape(str(p))}"' for p in sorted(str(p) for p in drv.input_srcs))
    parts.append(f"[{srcs}],")

    parts.append(f'"{_aterm_escape(drv.platform)}",')
    parts.append(f'"{_aterm_escape(drv.builder)}",')

    args = ",".join(f'"{_aterm_escape(a)}"' for a in drv.args)
    parts.append(f"[{args}],")

    env_parts: list[str] = []
    for k, v in sorted(drv.env.items()):
        env_parts.append(f'("{_aterm_escape(k)}","{_aterm_escape(v)}")')
    parts.append(f"[{','.join(env_parts)}]")

    parts.append(")")
    return "".join(parts)


def _unparse_derivation_for_hash(
    drv: Derivation,
    input_drv_hashes: dict[str, list[str]] | None = None,
) -> str:
    """Serialize a Derivation to ATerm for hashDerivationModulo.

    Like Derivation.serialize() but replaces input_drvs keys with the
    given hex hash strings (matching what Nix's hashDerivationModulo
    produces after substituting input drv references with their modulo
    hashes).

    Args:
        drv: The derivation to serialize.
        input_drv_hashes: {hex_hash: [output_names]} replacement for
            input_drvs.  When None, the original input_drvs are used
            with their store-path keys (same as serialize()).
    """
    parts: list[str] = ["Derive("]

    out_parts = [
        f'("{_aterm_escape(o.name)}","{_aterm_escape(o.path)}",'
        f'"{_aterm_escape(o.hash_algo)}","{_aterm_escape(o.hash_value)}")'
        for o in sorted(drv.outputs, key=lambda x: x.name)
    ]
    parts.append(f"[{','.join(out_parts)}],")

    if input_drv_hashes is not None:
        drv_parts = [
            f'("{_aterm_escape(key)}",[{",".join(f'"{_aterm_escape(o)}"' for o in outs)}])'
            for key, outs in sorted(input_drv_hashes.items(), key=lambda x: x[0])
        ]
    else:
        drv_parts = [
            f'("{_aterm_escape(str(drv_path))}",[{",".join(f'"{_aterm_escape(o)}"' for o in outputs)}])'
            for drv_path, outputs in sorted(drv.input_drvs.items(), key=lambda x: str(x[0]))
        ]
    parts.append(f"[{','.join(drv_parts)}],")

    srcs = ",".join(f'"{_aterm_escape(str(p))}"' for p in sorted(str(p) for p in drv.input_srcs))
    parts.append(f"[{srcs}],")

    parts.append(f'"{_aterm_escape(drv.platform)}",')
    parts.append(f'"{_aterm_escape(drv.builder)}",')

    args = ",".join(f'"{_aterm_escape(a)}"' for a in drv.args)
    parts.append(f"[{args}],")

    env_parts = [f'("{_aterm_escape(k)}","{_aterm_escape(v)}")' for k, v in sorted(drv.env.items())]
    parts.append(f"[{','.join(env_parts)}]")

    parts.append(")")
    return "".join(parts)


def _hash_derivation_modulo(
    drv: BasicDerivation,
    mask_outputs: bool = True,
) -> dict[str, bytes]:
    """Compute the hashDerivationModulo for a BasicDerivation (one hash per output)."""
    aterm = _unparse_basic_derivation(drv, mask_outputs=mask_outputs)
    h = hashlib.sha256(aterm.encode()).digest()
    return dict.fromkeys(drv.outputs, h)


def _resolve_deferred_outputs(
    resolved: BasicDerivation,
    drv_name: str,
) -> BasicDerivation:
    """Convert Deferred outputs to InputAddressed and compute output paths.

    Given a resolved BasicDerivation, computes hashDerivationModulo and
    replaces any Deferred outputs (``("", "", "")``) with concrete
    InputAddressed paths derived from the hash.
    """
    hash_modulo = _hash_derivation_modulo(resolved, mask_outputs=True)

    new_outputs: dict[str, DerivationOutput] = {}
    for name, o in resolved.outputs.items():
        if o.path == "" and o.method == "" and o.hash_digest == "":
            h = hash_modulo[name]
            out_path = _make_output_path(name, h, drv_name)
            new_outputs[name] = DerivationOutput(
                path=out_path,
                method="",
                hash_digest="",
            )
            resolved.env[name] = out_path
        else:
            new_outputs[name] = o

    resolved.outputs = new_outputs
    return resolved


def _rewrite_strings(s: str, rewrites: dict[str, str]) -> str:
    """Apply a placeholder-to-actual rewrite map to a string."""
    for old, new in rewrites.items():
        if old == new:
            continue
        s = s.replace(old, new)
    return s


def resolve_derivation(
    drv: Derivation,
    drv_path: StorePath,
    resolved_output_paths: dict[tuple[StorePath | str, ...], StorePath],
) -> BasicDerivation:
    """Resolve a deferred derivation by substituting placeholders with actual paths.

    Args:
        drv: The parsed derivation (with inputDrv info)
        drv_path: The .drv store path (for computing placeholders and output names)
        resolved_output_paths: {(input_drv_path, output_name): actual_store_path}
            for each input derivation output.

    Returns:
        A resolved BasicDerivation with filled-in output paths.
        The wire BasicDerivation has no inputDrvs, placeholders rewritten,
        and Deferred outputs converted to InputAddressed.
    """
    drv_name = _nix_drv_name(drv_path)

    rewrites: dict[str, str] = {}
    new_input_srcs = {SerdeStorePath(path=str(path)) for path in drv.input_srcs}  # pyright: ignore[reportUnhashable]

    for input_drv_path, output_names in drv.input_drvs.items():
        for output_name in output_names:
            placeholder = downstream_placeholder(input_drv_path, output_name)
            actual_path = resolved_output_paths.get((StorePath(input_drv_path), output_name))
            if actual_path is None:
                raise ValueError(f"No resolved path for {input_drv_path}!{output_name}")
            rewrites[placeholder] = str(actual_path)
            new_input_srcs.add(SerdeStorePath(path=str(actual_path)))

    resolved = BasicDerivation(
        outputs={
            o.name: DerivationOutput(
                path=o.path,
                method=o.hash_algo,
                hash_digest=o.hash_value,
            )
            for o in drv.outputs
        },
        input_srcs=new_input_srcs,
        platform=drv.platform,
        builder=_rewrite_strings(drv.builder, rewrites),
        args=[_rewrite_strings(a, rewrites) for a in drv.args],
        env={k: _rewrite_strings(v, rewrites) for k, v in drv.env.items()},
        is_dynamic=drv.is_dynamic,
    )

    return _resolve_deferred_outputs(resolved, drv_name)


def _collect_leaf_outputs(node: ChildMapNode) -> list[str]:
    """Collect all leaf output names from a ChildMapNode tree."""
    if node.outputs:
        return list(node.outputs)
    for child in node.children.values():
        leaf = _collect_leaf_outputs(child)
        if leaf:
            return leaf
    return []


# Type alias: (drv_path, output_name) -> resolved store path
type DynamicPathMap = dict[tuple[StorePath | str, ...], StorePath]


def _resolve_dynamic_node(
    drv_path: StorePath,
    node: ChildMapNode,
    path_map: DynamicPathMap,
    parent_hash: bytes | None,
    rewrites: dict[str, str],
    new_input_srcs: set[SerdeStorePath],
) -> None:
    """Recursively resolve placeholders for a ChildMapNode tree.

    Walks the tree depth-first, computing placeholders at each level.
    At the root (parent_hash is None), the placeholder is
    ``downstream_placeholder`` (level-1). Deeper levels use
    ``downstream_placeholder_unknown_derivation``.

    All non-root placeholders resolve to the same leaf output path
    (looked up as ``(drv_path, leaf_output_name)``), because the
    DynamicBuildGoal chain collapses intermediate levels into the
    final output.
    """
    hash_part = str(drv_path).rsplit("/", 1)[-1].split("-", 1)[0]
    dyn_drv_name = _nix_drv_name(drv_path)

    for child_name, child_node in node.children.items():
        # Compute this level's hash and placeholder
        if parent_hash is None:
            # Level 1: unknownCaOutput placeholder
            clear = f"nix-upstream-output:{hash_part}:{_output_path_name(dyn_drv_name, child_name)}"
            this_hash = hashlib.sha256(clear.encode()).digest()
            placeholder_prefix = "/" + nix32_encode(this_hash)
        else:
            # Level 2+: unknownDerivation placeholder
            this_hash = downstream_placeholder_unknown_derivation_raw(
                parent_hash,
                child_name,
            )
            placeholder_prefix = downstream_placeholder_unknown_derivation(
                parent_hash,
                child_name,
            )

        # Resolve leaf outputs reachable via this child
        # All leaf outputs at any depth resolve to the final output path
        for leaf_out in _collect_leaf_outputs(child_node):
            placeholder = downstream_placeholder(drv_path, leaf_out) if parent_hash is None else placeholder_prefix
            actual_path = path_map.get((drv_path, leaf_out))
            if actual_path is not None:
                log.debug(
                    "resolve_dyn_rewrite",
                    drv_path=str(drv_path),
                    leaf_out=leaf_out,
                    placeholder=placeholder,
                    actual_path=str(actual_path),
                )
                rewrites[placeholder] = str(actual_path)
                new_input_srcs.add(SerdeStorePath(path=str(actual_path)))
            else:
                log.debug(
                    "resolve_dyn_no_path",
                    drv_path=str(drv_path),
                    leaf_out=leaf_out,
                    path_key=f"({drv_path}, {leaf_out})",
                )

        # Recurse into child for deeper levels
        _resolve_dynamic_node(
            drv_path,
            child_node,
            path_map,
            this_hash,
            rewrites,
            new_input_srcs,
        )

    # Handle flat outputs at this level (leaf or root with no children)
    if node.outputs and not node.children:
        for leaf_out in node.outputs:
            if parent_hash is None:
                placeholder = downstream_placeholder(drv_path, leaf_out)
            else:
                placeholder = downstream_placeholder_unknown_derivation(
                    parent_hash,
                    leaf_out,
                )
            actual_path = path_map.get((drv_path, leaf_out))
            log.debug(
                "resolve_dyn_flat",
                drv_path=str(drv_path),
                leaf_out=leaf_out,
                placeholder=placeholder,
                path_found=actual_path is not None,
                parent_hash=parent_hash is not None,
            )
            if actual_path is not None:
                rewrites[placeholder] = str(actual_path)
                new_input_srcs.add(SerdeStorePath(path=str(actual_path)))


def resolve_dynamic_derivation(
    drv: Derivation,
    drv_path: StorePath,
    dynamic_output_paths: DynamicPathMap,
) -> BasicDerivation:
    """Resolve a dynamic (DrvWithVersion) wrapper derivation.

    Like resolve_derivation but handles dynamic_input_drvs which encode
    nested output references (drv^outer^inner). Each nested reference
    produces a DownstreamPlaceholder computed via unknownCaOutput then
    unknownDerivation chain.

    Args:
        drv: The parsed derivation (with dynamic_input_drvs as ChildMapNode)
        drv_path: The .drv store path (for computing output names)
        dynamic_output_paths: {(drv_path, *chain): actual_path}
            Maps each chain element to its resolved store path.
            E.g., {(producer,): drv_path, (producer, "out"): target_path}

    Returns:
        A resolved BasicDerivation with placeholders rewritten and
        Deferred outputs converted to InputAddressed.
    """
    drv_name = _nix_drv_name(drv_path)

    rewrites: dict[str, str] = {}
    new_input_srcs = {SerdeStorePath(path=str(path)) for path in drv.input_srcs}  # pyright: ignore[reportUnhashable]

    # Handle regular input_drvs (same as resolve_derivation)
    for input_drv_path, output_names in drv.input_drvs.items():
        for output_name in output_names:
            placeholder = downstream_placeholder(input_drv_path, output_name)
            actual_path = dynamic_output_paths.get((input_drv_path, output_name))
            if actual_path is None:
                raise ValueError(f"No resolved path for {input_drv_path}!{output_name}")
            rewrites[placeholder] = str(actual_path)
            new_input_srcs.add(SerdeStorePath(path=str(actual_path)))

    # Handle dynamic_input_drvs: recursive ChildMapNode
    for dyn_drv_path, node in drv.dynamic_input_drvs.items():
        _resolve_dynamic_node(
            dyn_drv_path,
            node,
            dynamic_output_paths,
            parent_hash=None,
            rewrites=rewrites,
            new_input_srcs=new_input_srcs,
        )

    log.debug(
        "resolve_dyn_final",
        rewrites_count=len(rewrites),
        rewrites={k[:50]: v for k, v in rewrites.items()},
    )

    resolved = BasicDerivation(
        outputs={
            o.name: DerivationOutput(
                path=o.path,
                method=o.hash_algo,
                hash_digest=o.hash_value,
            )
            for o in drv.outputs
        },
        input_srcs=new_input_srcs,
        platform=drv.platform,
        builder=_rewrite_strings(drv.builder, rewrites),
        args=[_rewrite_strings(a, rewrites) for a in drv.args],
        env={k: _rewrite_strings(v, rewrites) for k, v in drv.env.items()},
        is_dynamic=drv.is_dynamic,
    )

    return _resolve_deferred_outputs(resolved, drv_name)
