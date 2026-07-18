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
from collections.abc import Callable  # noqa: TC003
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, ClassVar, get_args, get_origin, get_type_hints

from pydantic import BaseModel, ConfigDict
from pydantic import Field as PydanticField
from pydantic_core import PydanticUndefined

from .context import ReadContext, WriteContext

# ── Helpers ──


@functools.lru_cache(maxsize=256)
def _find_reader(ann: type, version: int = 0) -> Any:
    """Look up a reader for a wire type."""
    from .wire_string import WireString  # lazy: break circular import

    # Primitives
    if ann is int:
        return lambda r: r.read_uint64()
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
            return _find_reader(non_none[0], version)

    # -- list generics --
    if origin is list:
        elem = _find_reader(args[0], version)

        async def _read_list(r):
            n = await r.read_uint64()
            return [await elem(r) for _ in range(n)]

        return _read_list

    # -- set generics --
    if origin is set:
        elem = _find_reader(args[0], version)

        async def _read_set(r):
            n = await r.read_uint64()
            return {await elem(r) for _ in range(n)}

        return _read_set

    # -- dict generics --
    if origin is dict:
        k_reader = _find_reader(args[0], version)
        v_reader = _find_reader(args[1], version)

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

    # WireModel subclass
    if isinstance(ann, type) and issubclass(ann, WireModel):

        async def _read_nested(r):
            return await ann.from_reader(ReadContext(reader=r, version=version))

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
            if val is None and non_none[0].__name__ == "StorePath":
                # Nix represents an absent deriver as an empty store-path
                # string, not the Python value's textual representation.
                ctx.writer.write_string("")
                return
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
    """Protocol version constraint for a wire field."""

    min_version: int | None = None
    max_version: int | None = None
    serialize: bool | None = None
    deserialize: bool | None = None
    wire_depends_on: Callable | None = None


def WireField(  # noqa: N802
    default: Any = PydanticUndefined,
    *,
    default_factory: Any = None,
    min_version: int | None = None,
    max_version: int | None = None,
    serialize: bool | None = None,
    deserialize: bool | None = None,
    wire_depends_on: Callable | None = None,
    **kwargs: Any,
) -> Any:
    """A Pydantic Field with Nix protocol version requirements.

    Usage::

        times_built: int = WireField(default=0, min_version=proto(1, 29))
        legacy_field: str = WireField(default="", max_version=proto(1, 27))
    """
    if default is not PydanticUndefined:
        kwargs.setdefault("default", default)
    if default_factory is not None:
        kwargs["default_factory"] = default_factory

    field_info = PydanticField(**kwargs)
    field_info.metadata.append(VersionMeta(min_version, max_version, serialize, deserialize, wire_depends_on))
    return field_info


# ── Field collection ──


@functools.lru_cache(maxsize=256)
def _wire_fields(cls: type[BaseModel], version: int = 0) -> list[tuple[str, type, Callable | None, bool, bool]]:
    """Return (name, raw_annotation, wire_depends_on, serialize, deserialize) tuples.

    ClassVar fields default to serialize=False, deserialize=False unless
    ``WireField(serialize=..., deserialize=...)`` overrides them explicitly.

    Fields with ``min_version`` or ``max_version`` constraints are filtered
    against the provided ``version``.  ``version=0`` means no filtering.
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

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)

    def __hash__(self) -> int:
        return hash(tuple(getattr(self, f) for f in self.__class__.model_fields))

    async def to_writer(self, ctx: WriteContext) -> None:
        """Write all non-ClassVar fields in declaration order."""
        for name, ann, wire_depends_on, serialize, _deserialize in _wire_fields(type(self), version=ctx.version):
            if not serialize:
                continue
            if wire_depends_on is not None and not wire_depends_on(self):
                continue

            val = getattr(self, name)
            await _write_value(val, ann, ctx)

    @classmethod
    async def from_reader(cls, ctx: ReadContext):
        """Read all non-ClassVar fields in declaration order."""
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

        for name, ann, wire_depends_on, _serialize, deserialize in _wire_fields(cls, version=ctx.version):
            if not deserialize:
                continue
            if wire_depends_on is not None and not wire_depends_on(obj):
                continue

            reader = _find_reader(ann, version=ctx.version)
            object.__setattr__(obj, name, await reader(ctx.reader))
            obj.__pydantic_fields_set__.add(name)

        return obj

    def to_json(self, **kwargs) -> str:
        """Serialize to JSON string.

        Uses Pydantic's ``model_dump_json``.
        """
        return self.model_dump_json(**kwargs)

    @classmethod
    def from_json(cls, json_data: str, **kwargs) -> WireModel:
        """Deserialize from JSON string.

        Uses Pydantic's ``model_validate_json``.
        """
        return cls.model_validate_json(json_data, **kwargs)
