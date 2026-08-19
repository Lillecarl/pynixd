"""Declarative binary serialization for Nix daemon protocol types.

Pydantic models inherit from ``WireModel`` to auto-generate
to_writer()/from_reader() from type annotations.

Class variables (ClassVar[int]) are skipped — they're protocol constants
like op codes that are written/read by the operation layer, not the message body.
"""

from __future__ import annotations

import asyncio
import functools
import types
from collections.abc import Callable, Iterable  # noqa: TC003
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, ClassVar, get_args, get_origin, get_type_hints

from pydantic import BaseModel, ConfigDict
from pydantic import Field as PydanticField
from pydantic_core import PydanticUndefined

from .context import ReadContext, WriteContext
from .logging import deserialization_scope
from .wire_integer import WireUInt64
from .wire_scalar import WireScalar

# ── Helpers ──


@functools.lru_cache(maxsize=256)
def _find_reader(ann: type, version: int = 0, features: frozenset[str] = frozenset()) -> Any:
    """Look up a reader for a wire type."""
    from .wire_string import WireString  # lazy: break circular import

    # Primitives
    if ann is int:
        return lambda r: r.read_uint64()
    if isinstance(ann, type) and issubclass(ann, WireUInt64):

        async def _read_uint64_model(r):
            return ann(await r.read_uint64())

        return _read_uint64_model
    # IntEnum — read uint64, convert to enum member
    if isinstance(ann, type) and issubclass(ann, IntEnum):

        async def _read_enum(r):
            return ann(await r.read_uint64())

        return _read_enum
    if ann is str:
        return lambda r: r.read_string(str)
    if ann is bool:
        return lambda r: r.read_bool()
    if ann is bytes:
        return lambda r: r.read_bytes()

    origin = get_origin(ann)
    args = get_args(ann)

    # Optional[T] — strip None, delegate to inner
    if origin is types.UnionType:
        non_none = tuple(a for a in args if a is not type(None))
        if len(non_none) == 1:
            inner = _find_reader(non_none[0], version, features)
            if isinstance(non_none[0], type) and issubclass(non_none[0], WireScalar):
                # Nix writes an absent scalar as the empty string, which
                # `_write_value` below answers for `None`. Without this the
                # value comes back as the empty scalar rather than as `None`,
                # so a caller that tests `is None` never takes that branch.
                # The bytes do not move; only the Python value does. Issue #194.
                async def _read_optional_scalar(r: Any) -> Any:
                    value = await inner(r)
                    return None if value == "" else value

                return _read_optional_scalar
            return inner

    # -- list generics --
    if origin is list:
        elem = _find_reader(args[0], version, features)

        async def _read_list(r):
            n = await r.read_uint64()
            return [await elem(r) for _ in range(n)]

        return _read_list

    # -- set generics --
    if origin is set:
        elem = _find_reader(args[0], version, features)

        async def _read_set(r):
            n = await r.read_uint64()
            return {await elem(r) for _ in range(n)}

        return _read_set

    # -- dict generics --
    if origin is dict:
        k_reader = _find_reader(args[0], version, features)
        v_reader = _find_reader(args[1], version, features)

        async def _read_dict(r):
            n = await r.read_uint64()
            d = {}
            for _ in range(n):
                key = await k_reader(r)
                val = await v_reader(r)
                d[key] = val
            return d

        return _read_dict

    # WireString — read one string, construct directly (no model_construct)
    if isinstance(ann, type) and issubclass(ann, WireString):
        n_fields = len(ann.model_fields)
        if n_fields == 1:
            _field_name: str = next(iter(ann.model_fields.keys()))

            async def _read_string(r):
                raw = await r.read_string(str)
                obj = ann.__new__(ann)
                object.__setattr__(obj, "__pydantic_extra__", None)
                object.__setattr__(obj, "__pydantic_private__", None)
                object.__setattr__(obj, _field_name, raw)
                object.__setattr__(obj, "__pydantic_fields_set__", {_field_name})
                return obj
        else:

            async def _read_string(r):
                raw = await r.read_string(str)
                data = ann.from_str(raw)
                if not isinstance(data, dict):
                    raise TypeError(f"from_str returned {type(data).__name__}, expected dict")
                obj = ann.__new__(ann)
                field_names = set(data.keys())
                object.__setattr__(obj, "__pydantic_extra__", None)
                object.__setattr__(obj, "__pydantic_private__", None)
                object.__setattr__(obj, "__pydantic_fields_set__", field_names)
                for k, v in data.items():
                    object.__setattr__(obj, k, v)
                return obj

        return _read_string

    # WireScalar — a typed native string with domain helper methods.
    if isinstance(ann, type) and issubclass(ann, WireScalar):

        async def _read_scalar(r):
            return ann.from_wire(await r.read_string(str))

        return _read_scalar

    # WireModel subclass
    if isinstance(ann, type) and issubclass(ann, WireModel):

        async def _read_nested(r):
            return await ann.from_reader(ReadContext(reader=r, version=version, features=features))

        return _read_nested

    # IntEnum — read uint64, construct via enum
    if isinstance(ann, type) and issubclass(ann, IntEnum):

        async def _read_enum(r):
            return ann(await r.read_uint64())

        return _read_enum

    raise TypeError(f"No reader for {ann}")


async def _write_value(val: Any, ann: type, ctx: WriteContext) -> None:
    """Write a value to the wire using primitives, generics, or nested serialization."""
    from .wire_string import WireString  # lazy: break circular import

    # Primitives
    if ann is int:
        ctx.writer.write_uint64(val)
        return None
    if isinstance(ann, type) and issubclass(ann, WireUInt64):
        ctx.writer.write_uint64(val)
        return None
    if ann is str:
        ctx.writer.write_string(val)
        return None
    if ann is bool:
        ctx.writer.write_bool(val)
        return None
    if ann is bytes:
        ctx.writer.write_bytes(val)
        return None

    origin = get_origin(ann)
    args = get_args(ann)

    # Optional[T] — delegate to inner type (val should never be None on the wire)
    if origin is types.UnionType:
        non_none = tuple(a for a in args if a is not type(None))
        if len(non_none) == 1:
            if val is None and isinstance(non_none[0], type) and issubclass(non_none[0], WireScalar):
                # Nix represents an absent scalar as the empty string, and not
                # as the textual representation of the Python value. This is
                # the write half of the rule that `_find_reader` reads back,
                # and it holds for every scalar rather than for `StorePath`
                # alone. Issue #194.
                ctx.writer.write_string("")
                return None
            return await _write_value(val, non_none[0], ctx)

    # -- list generics --
    if origin is list:
        ctx.writer.write_uint64(len(val))
        for item in val:
            await _write_value(item, args[0], ctx)
        return None

    # -- set generics --
    if origin is set:
        ctx.writer.write_uint64(len(val))
        for item in val:
            await _write_value(item, args[0], ctx)
        return None

    # -- dict generics --
    if origin is dict:
        ctx.writer.write_uint64(len(val))
        for k, v in val.items():
            await _write_value(k, args[0], ctx)
            await _write_value(v, args[1], ctx)
        return None

    # WireString — write str(self) as a single wire string
    if isinstance(ann, type) and issubclass(ann, WireString):
        ctx.writer.write_string(str(val))
        return None

    # WireScalar — write its canonical string value directly.
    if isinstance(ann, type) and issubclass(ann, WireScalar):
        ctx.writer.write_string(val.to_wire())
        return None

    # WireModel subclass
    if isinstance(ann, type) and issubclass(ann, WireModel):
        result = val.to_writer(ctx)
        if asyncio.iscoroutine(result):
            await result
        return None

    # IntEnum — write its int value
    if isinstance(ann, type) and issubclass(ann, IntEnum):
        ctx.writer.write_uint64(val.value)
        return None

    raise TypeError(f"No writer for {ann}")


# ── Version-constrained fields ──


@dataclass(frozen=True)
class VersionMeta:
    """Protocol version and feature constraint for a wire field."""

    min_version: int | None = None
    max_version: int | None = None
    serialize: bool | None = None
    deserialize: bool | None = None
    wire_depends_on: Callable | None = None
    needs_features: frozenset[str] | None = None
    unless_features: frozenset[str] | None = None


def WireField(  # noqa: N802
    default: Any = PydanticUndefined,
    *,
    default_factory: Any = None,
    min_version: int | None = None,
    max_version: int | None = None,
    serialize: bool | None = None,
    deserialize: bool | None = None,
    wire_depends_on: Callable | None = None,
    needs_features: Iterable[str] | None = None,
    unless_features: Iterable[str] | None = None,
    **kwargs: Any,
) -> Any:
    """A Pydantic Field with Nix protocol version and feature requirements.

    Usage::

        times_built: int = WireField(default=0, min_version=proto(1, 29))
        legacy_field: str = WireField(default="", max_version=proto(1, 27))

    **The version number stopped at 1.38, and a new capability is a feature.**
    `worker-protocol.hh:105` of Nix says so. Both sides send a set of names in
    the handshake, and the negotiated set is the intersection, so a version
    number alone no longer says what shape a field has.

    Nix writes that choice as an if/else over the negotiated set, and these
    two arguments are its two halves. `worker-protocol.cc:268` is the
    example: `BuildResult.builtOutputs` is a map of `UnkeyedRealisation` when
    `realisation-with-path-not-hash` is on, and a map of JSON strings when it
    is off::

        built_outputs_new: ... = WireField(needs_features=[FEATURE_REALISATION_WITH_PATH])
        built_outputs_old: ... = WireField(unless_features=[FEATURE_REALISATION_WITH_PATH])

    `needs_features` keeps the field when the negotiated set holds **every**
    name. `unless_features` keeps it when the set holds **none** of them. A
    field with neither is there whatever the peers agreed, which is every
    field of the Nix 2.34 shape. Issue #162.
    """
    if default is not PydanticUndefined:
        kwargs.setdefault("default", default)
    if default_factory is not None:
        kwargs["default_factory"] = default_factory

    field_info = PydanticField(**kwargs)
    field_info.metadata.append(
        VersionMeta(
            min_version,
            max_version,
            serialize,
            deserialize,
            wire_depends_on,
            None if needs_features is None else frozenset(needs_features),
            None if unless_features is None else frozenset(unless_features),
        )
    )
    return field_info


# ── Field collection ──


@functools.lru_cache(maxsize=256)
def _wire_fields(
    cls: type[BaseModel],
    version: int = 0,
    features: frozenset[str] = frozenset(),
) -> list[tuple[str, type, Callable | None, bool, bool]]:
    """Return (name, raw_annotation, wire_depends_on, serialize, deserialize) tuples.

    ClassVar fields default to serialize=False, deserialize=False unless
    ``WireField(serialize=..., deserialize=...)`` overrides them explicitly.

    Fields with ``min_version`` or ``max_version`` constraints are filtered
    against the provided ``version``.  ``version=0`` means no filtering.

    Fields with ``needs_features`` or ``unless_features`` are filtered against
    *features*, which is the set the two peers negotiated. That filter runs
    whatever the version is, because an empty set is a real answer: it is what
    Nix 2.34 offers, and what a peer below 1.38 can say at all.
    """
    hints = get_type_hints(cls, include_extras=True)
    result = []
    for name in cls.model_fields:
        field = cls.model_fields[name]
        version_meta: VersionMeta | None = next((m for m in field.metadata if isinstance(m, VersionMeta)), None)

        # Version-gating (0 means no filtering)
        if version_meta is not None and version:
            if version_meta.min_version is not None and version < version_meta.min_version:
                continue
            if version_meta.max_version is not None and version > version_meta.max_version:
                continue

        # Feature-gating. No `if features:` guard: the empty set decides as
        # much as any other set, and a field behind `unless_features` is
        # exactly the one that the empty set keeps.
        if version_meta is not None:
            if version_meta.needs_features is not None and not version_meta.needs_features <= features:
                continue
            if version_meta.unless_features is not None and version_meta.unless_features & features:
                continue

        ann = hints.get(name)
        if ann is None:
            continue

        is_classvar = get_origin(ann) is ClassVar

        # Resolve serialize / deserialize flags
        if version_meta is not None:
            _serialize = version_meta.serialize if version_meta.serialize is not None else (not is_classvar)
            _deserialize = version_meta.deserialize if version_meta.deserialize is not None else (not is_classvar)
        else:
            _serialize = not is_classvar
            _deserialize = not is_classvar

        wire_depends_on = version_meta.wire_depends_on if version_meta else None
        result.append((name, ann, wire_depends_on, _serialize, _deserialize))
    return result


# ── Base class ──


class WireModel(BaseModel):
    """Pydantic base class with auto-generated Nix daemon protocol serde.

    Usage:
        class FooResponse(WireModel):
            valid: int   # uint64 on the wire
            path: str    # length-prefixed UTF-8
    """

    # **The name of a field builds one of these, and the alias does too.**
    # The wire uses the alias, and Python code uses the name. With the alias
    # alone, `Realisation(out_path=...)` put nothing in the field and raised
    # nothing, so the answer carried `null` where a store path belongs. The
    # rule belongs here, so the next field that gets an alias cannot repeat it.
    model_config = ConfigDict(arbitrary_types_allowed=True, validate_by_alias=True, validate_by_name=True)

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)

    def __hash__(self) -> int:
        return hash(tuple(getattr(self, f) for f in self.__class__.model_fields))

    async def to_writer(self, ctx: WriteContext) -> None:
        """Write all non-ClassVar fields in declaration order."""
        for name, ann, wire_depends_on, serialize, _deserialize in _wire_fields(
            type(self), version=ctx.version, features=ctx.features
        ):
            if not serialize:
                continue
            if wire_depends_on is not None and not wire_depends_on(self):
                continue

            val = getattr(self, name)
            await _write_value(val, ann, ctx)

    @classmethod
    async def from_reader(cls, ctx: ReadContext):
        """Read all non-ClassVar fields in declaration order."""
        with deserialization_scope(ctx, cls):
            obj = cls.__new__(cls)
            # Initialize Pydantic internals (bypassed __init__)
            object.__setattr__(obj, "__pydantic_fields_set__", set())
            object.__setattr__(obj, "__pydantic_extra__", None)
            object.__setattr__(obj, "__pydantic_private__", None)
            # Set defaults for version-gated and conditional fields
            for name, field in cls.model_fields.items():
                if field.default is not PydanticUndefined:
                    object.__setattr__(obj, name, field.default)
                elif field.default_factory is not None:
                    object.__setattr__(obj, name, field.default_factory())  # pyright: ignore[reportCallIssue]

            for name, ann, wire_depends_on, _serialize, deserialize in _wire_fields(
                cls, version=ctx.version, features=ctx.features
            ):
                if not deserialize:
                    continue
                if wire_depends_on is not None and not wire_depends_on(obj):
                    continue

                reader = _find_reader(ann, version=ctx.version, features=ctx.features)
                object.__setattr__(obj, name, await reader(ctx.reader))
                obj.__pydantic_fields_set__.add(name)

            return obj

    def to_json(self, **kwargs) -> str:
        """Serialize to JSON string.

        Uses Pydantic's ``model_dump_json``.
        """
        return self.model_dump_json(**kwargs)

    @classmethod
    def from_json(cls, json_data: str | bytes, **kwargs) -> WireModel:
        """Deserialize from JSON string.

        Uses Pydantic's ``model_validate_json``.
        """
        return cls.model_validate_json(json_data, **kwargs)
