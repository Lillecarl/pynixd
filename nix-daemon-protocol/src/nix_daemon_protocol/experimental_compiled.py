"""Experimental import-time compiler for declarative daemon codecs.

This is deliberately opt-in: ``WireModel`` continues to use the small,
inspectable generic implementation in :mod:`nix_daemon_protocol.wire_message`.
``compile_codec`` demonstrates that the declarative schema can instead be
lowered to one concrete coroutine pair per model/protocol-version combination.

The generated functions contain direct attribute access and field calls.  Type
dispatch and version filtering happen while compiling, rather than once per
field in every message.  Custom codecs such as ``WireString`` and ``WireLogs``
remain the source of truth and are called unchanged.
"""

from __future__ import annotations

import ast
import functools
import types
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, get_args, get_origin

from pydantic_core import PydanticUndefined

from .constants import proto_str
from .context import ReadContext, WriteContext
from .exceptions import UnsupportedProtocolVersion
from .logging import deserialization_scope
from .wire_integer import WireUInt64
from .wire_message import WireModel, _wire_fields
from .wire_ops import WireRequest
from .wire_scalar import WireScalar

Writer = Callable[[Any, WriteContext], Awaitable[None]]
Reader = Callable[[ReadContext], Awaitable[Any]]


class CompiledCodec:
    """A generated serializer/deserializer pair for one model and version."""

    def __init__(
        self,
        write: Callable[[WireModel, WriteContext], Awaitable[None]],
        read: Callable[[ReadContext], Awaitable[WireModel]],
        write_source: str,
        read_source: str,
        schema: WireSchema,
    ) -> None:
        self.write = write
        self.read = read
        self.write_source = write_source
        self.read_source = read_source
        self.schema = schema


@dataclass(frozen=True)
class _Primitive:
    method: str


@dataclass(frozen=True)
class _Integer:
    constructor: type[WireUInt64] | None = None


@dataclass(frozen=True)
class _Enum:
    enum: type[IntEnum]


@dataclass(frozen=True)
class _Scalar:
    scalar: type[WireScalar]


@dataclass(frozen=True)
class _OptionalStorePath:
    value: WireNode


@dataclass(frozen=True)
class _Sequence:
    kind: str
    item: WireNode


@dataclass(frozen=True)
class _Mapping:
    key: WireNode
    value: WireNode


@dataclass(frozen=True)
class _Model:
    model: type[WireModel]


@dataclass(frozen=True)
class _WireString:
    model: type[WireModel]
    direct_field: str | None


WireNode = _Primitive | _Integer | _Enum | _Scalar | _OptionalStorePath | _Sequence | _Mapping | _Model | _WireString


@dataclass(frozen=True)
class _Field:
    name: str
    value: WireNode
    predicate: Callable[[WireModel], bool] | None
    serialize: bool
    deserialize: bool


@dataclass(frozen=True)
class WireSchema:
    """Immutable intermediate representation of one versioned wire layout."""

    model: type[WireModel]
    version: int
    fields: tuple[_Field, ...]


def _wire_node(annotation: type) -> WireNode:
    """Translate a resolved annotation to a small, backend-independent IR."""
    from .wire_string import WireString

    if annotation is int:
        return _Integer()
    if isinstance(annotation, type) and issubclass(annotation, WireUInt64):
        return _Integer(annotation)
    if annotation is str:
        return _Primitive("string")
    if annotation is bool:
        return _Primitive("bool")
    if annotation is bytes:
        return _Primitive("bytes")
    if isinstance(annotation, type) and issubclass(annotation, IntEnum):
        return _Enum(annotation)
    if isinstance(annotation, type) and issubclass(annotation, WireScalar):
        return _Scalar(annotation)

    origin = get_origin(annotation)
    arguments = get_args(annotation)
    if origin is types.UnionType:
        non_none = tuple(arg for arg in arguments if arg is not type(None))
        if len(non_none) == 1:
            value = _wire_node(non_none[0])
            if non_none[0].__name__ == "StorePath":
                return _OptionalStorePath(value)
            return value
    if origin is list:
        return _Sequence("list", _wire_node(arguments[0]))
    if origin is set:
        return _Sequence("set", _wire_node(arguments[0]))
    if origin is dict:
        return _Mapping(_wire_node(arguments[0]), _wire_node(arguments[1]))
    if isinstance(annotation, type) and issubclass(annotation, WireString):
        fields = tuple(annotation.model_fields)
        direct_field = fields[0] if annotation.to_str is WireString.to_str and len(fields) == 1 else None
        return _WireString(annotation, direct_field)
    if isinstance(annotation, type) and issubclass(annotation, WireModel):
        return _Model(annotation)
    raise TypeError(f"No wire node for {annotation}")


def _wire_schema(model: type[WireModel], version: int) -> WireSchema:
    return WireSchema(
        model=model,
        version=version,
        fields=tuple(
            _Field(name, _wire_node(annotation), predicate, serialize, deserialize)
            for name, annotation, predicate, serialize, deserialize in _wire_fields(model, version)
        ),
    )


def _name(name: str, context: ast.expr_context | None = None) -> ast.Name:
    return ast.Name(id=name, ctx=context or ast.Load())


def _attribute(value: ast.expr, name: str, context: ast.expr_context | None = None) -> ast.Attribute:
    return ast.Attribute(value=value, attr=name, ctx=context or ast.Load())


def _subscript(value: str, index: int) -> ast.Subscript:
    return ast.Subscript(value=_name(value), slice=ast.Constant(index), ctx=ast.Load())


def _call(function: ast.expr, *args: ast.expr) -> ast.Call:
    return ast.Call(func=function, args=list(args), keywords=[])


def _ctx_method(method: str) -> ast.Attribute:
    return _attribute(_attribute(_name("ctx"), "writer"), f"write_{method}")


def _reader_method(method: str) -> ast.Attribute:
    return _attribute(_attribute(_name("ctx"), "reader"), f"read_{method}")


class _AstLowerer:
    """Lower ``WireSchema`` values to direct Python AST statements."""

    def __init__(self, version: int, direction: str) -> None:
        self.version = version
        self.direction = direction
        self.adapters: list[Writer | Reader | type[WireUInt64] | Callable[[str], WireScalar]] = []
        self.codecs: list[CompiledCodec] = []
        self.enums: list[type[IntEnum]] = []
        self._counter = 0

    def local(self, prefix: str) -> ast.Name:
        """Return a collision-free local variable name."""
        result = f"_{prefix}_{self._counter}"
        self._counter += 1
        return _name(result, ast.Store())

    def _adapter(self, adapter: Writer | Reader | type[WireUInt64] | Callable[[str], WireScalar]) -> ast.Subscript:
        index = len(self.adapters)
        self.adapters.append(adapter)
        return _subscript(f"{self.direction}_adapters", index)

    def _codec(self, codec: CompiledCodec) -> ast.Subscript:
        index = len(self.codecs)
        self.codecs.append(codec)
        return _subscript(f"{self.direction}_codecs", index)

    def write_value(self, node: WireNode, value: ast.expr) -> list[ast.stmt]:
        if isinstance(node, _Integer):
            return [ast.Expr(_call(_ctx_method("uint64"), value))]
        if isinstance(node, _Primitive):
            return [ast.Expr(_call(_ctx_method(node.method), value))]
        if isinstance(node, _Enum):
            return [ast.Expr(_call(_ctx_method("uint64"), _attribute(value, "value")))]
        if isinstance(node, _Scalar):
            return [ast.Expr(_call(_ctx_method("string"), value))]
        if isinstance(node, _OptionalStorePath):
            return [
                ast.If(
                    test=ast.Compare(value, [ast.Is()], [ast.Constant(None)]),
                    body=[ast.Expr(_call(_ctx_method("string"), ast.Constant("")))],
                    orelse=self.write_value(node.value, value),
                ),
            ]
        if isinstance(node, _Sequence):
            item = self.local("item")
            return [
                ast.Expr(_call(_ctx_method("uint64"), _call(_name("len"), value))),
                ast.For(target=item, iter=value, body=self.write_value(node.item, _name(item.id)), orelse=[]),
            ]
        if isinstance(node, _Mapping):
            key = self.local("key")
            item = self.local("item")
            return [
                ast.Expr(_call(_ctx_method("uint64"), _call(_name("len"), value))),
                ast.For(
                    target=ast.Tuple([key, item], ast.Store()),
                    iter=_call(_attribute(value, "items")),
                    body=[*self.write_value(node.key, _name(key.id)), *self.write_value(node.value, _name(item.id))],
                    orelse=[],
                ),
            ]
        if isinstance(node, _WireString):
            string = _attribute(value, node.direct_field) if node.direct_field else _call(_name("str"), value)
            return [ast.Expr(_call(_ctx_method("string"), string))]
        if isinstance(node, _Model):
            if _can_compile(node.model):
                return [
                    ast.Expr(
                        ast.Await(
                            _call(
                                _attribute(self._codec(compile_codec(node.model, self.version)), "write"),
                                value,
                                _name("ctx"),
                            )
                        )
                    )
                ]
            return [
                ast.Expr(ast.Await(_call(self._adapter(_writer_for(node.model, self.version)), value, _name("ctx"))))
            ]
        raise TypeError(f"No writer for {node}")

    def read_value(self, node: WireNode, target: ast.expr) -> list[ast.stmt]:
        # Callers pass a Store-context target for assignment. Rebuild its Load
        # form for uses inside the generated expression tree.
        if not isinstance(target, ast.Name):
            raise TypeError(f"Expected a local read target, got {ast.dump(target)}")
        target_value = _name(target.id)
        if isinstance(node, _Integer):
            raw = ast.Await(_call(_reader_method("uint64")))
            value = raw if node.constructor is None else _call(self._adapter(node.constructor), raw)
            return [ast.Assign([target], value)]
        if isinstance(node, _Primitive):
            args = [_name("str")] if node.method == "string" else []
            return [ast.Assign([target], ast.Await(_call(_reader_method(node.method), *args)))]
        if isinstance(node, _Enum):
            index = len(self.enums)
            self.enums.append(node.enum)
            return [
                ast.Assign([target], _call(_subscript("read_enums", index), ast.Await(_call(_reader_method("uint64")))))
            ]
        if isinstance(node, _Scalar):
            return [
                ast.Assign(
                    [target],
                    _call(
                        self._adapter(node.scalar.from_wire), ast.Await(_call(_reader_method("string"), _name("str")))
                    ),
                )
            ]
        if isinstance(node, _OptionalStorePath):
            return self.read_value(node.value, target)
        if isinstance(node, _Sequence):
            item = self.local("item")
            initial = ast.List([], ast.Load()) if node.kind == "list" else ast.Call(_name("set"), [], [])
            append = "append" if node.kind == "list" else "add"
            return [
                ast.Assign([target], initial),
                ast.For(
                    target=_name("_", ast.Store()),
                    iter=_call(_name("range"), ast.Await(_call(_reader_method("uint64")))),
                    body=[
                        *self.read_value(node.item, _name(item.id, ast.Store())),
                        ast.Expr(_call(_attribute(target_value, append), _name(item.id))),
                    ],
                    orelse=[],
                ),
            ]
        if isinstance(node, _Mapping):
            key = self.local("key")
            item = self.local("item")
            return [
                ast.Assign([target], ast.Dict([], [])),
                ast.For(
                    target=_name("_", ast.Store()),
                    iter=_call(_name("range"), ast.Await(_call(_reader_method("uint64")))),
                    body=[
                        *self.read_value(node.key, _name(key.id, ast.Store())),
                        *self.read_value(node.value, _name(item.id, ast.Store())),
                        ast.Assign([ast.Subscript(target_value, _name(key.id), ast.Store())], _name(item.id)),
                    ],
                    orelse=[],
                ),
            ]
        if isinstance(node, _Model):
            if _can_compile(node.model):
                call = _call(_attribute(self._codec(compile_codec(node.model, self.version)), "read"), _name("ctx"))
            else:
                call = _call(self._adapter(_reader_for(node.model, self.version)), _name("ctx"))
            return [ast.Assign([target], ast.Await(call))]
        raise TypeError(f"No reader for {node}")


def _can_compile(model: type[WireModel]) -> bool:
    """Whether ``model`` has declarative fields plus an understood prelude."""
    return (
        model.to_writer is WireModel.to_writer and model.from_reader.__func__ is WireModel.from_reader.__func__
    ) or issubclass(model, WireRequest)


async def _write_request_prelude(value: WireRequest, ctx: WriteContext) -> None:
    """Write the only non-declarative portion shared by all requests."""
    if ctx.version and ctx.version < value.min_protocol:
        raise UnsupportedProtocolVersion(
            f"{value.name} requires daemon protocol >= {proto_str(value.min_protocol)}, got {proto_str(ctx.version)}",
        )
    ctx.writer.write_uint64(value.op)


@functools.cache
def _writer_for(annotation: type, version: int) -> Writer:
    """Specialize a write closure once for an annotation/version pair."""
    from .wire_string import WireString

    if annotation is int:

        async def write_int(value: int, ctx: WriteContext) -> None:
            ctx.writer.write_uint64(value)

        return write_int
    if annotation is str:

        async def write_str(value: str, ctx: WriteContext) -> None:
            ctx.writer.write_string(value)

        return write_str
    if annotation is bool:

        async def write_bool(value: bool, ctx: WriteContext) -> None:
            ctx.writer.write_bool(value)

        return write_bool
    if annotation is bytes:

        async def write_bytes(value: bytes, ctx: WriteContext) -> None:
            ctx.writer.write_bytes(value)

        return write_bytes
    if isinstance(annotation, type) and issubclass(annotation, IntEnum):

        async def write_enum(value: IntEnum, ctx: WriteContext) -> None:
            ctx.writer.write_uint64(value.value)

        return write_enum

    origin = get_origin(annotation)
    arguments = get_args(annotation)
    if origin is types.UnionType:
        non_none = tuple(arg for arg in arguments if arg is not type(None))
        if len(non_none) == 1:
            writer = _writer_for(non_none[0], version)
            if non_none[0].__name__ == "StorePath":

                async def write_optional_store_path(value: Any, ctx: WriteContext) -> None:
                    if value is None:
                        ctx.writer.write_string("")
                    else:
                        await writer(value, ctx)

                return write_optional_store_path
            return writer
    if origin is list:
        writer = _writer_for(arguments[0], version)

        async def write_list(value: list[Any], ctx: WriteContext) -> None:
            ctx.writer.write_uint64(len(value))
            for item in value:
                await writer(item, ctx)

        return write_list
    if origin is set:
        writer = _writer_for(arguments[0], version)

        async def write_set(value: set[Any], ctx: WriteContext) -> None:
            ctx.writer.write_uint64(len(value))
            for item in value:
                await writer(item, ctx)

        return write_set
    if origin is dict:
        key_writer = _writer_for(arguments[0], version)
        value_writer = _writer_for(arguments[1], version)

        async def write_dict(value: dict[Any, Any], ctx: WriteContext) -> None:
            ctx.writer.write_uint64(len(value))
            for key, item in value.items():
                await key_writer(key, ctx)
                await value_writer(item, ctx)

        return write_dict
    if isinstance(annotation, type) and issubclass(annotation, WireString):

        async def write_wire_string(value: WireString, ctx: WriteContext) -> None:
            ctx.writer.write_string(str(value))

        return write_wire_string
    if isinstance(annotation, type) and issubclass(annotation, WireModel):
        if _can_compile(annotation):
            codec = compile_codec(annotation, version)
            return codec.write

        async def write_custom_model(value: WireModel, ctx: WriteContext) -> None:
            await value.to_writer(ctx)

        return write_custom_model
    raise TypeError(f"No writer for {annotation}")


@functools.cache
def _reader_for(annotation: type, version: int) -> Reader:
    """Specialize a read closure once for an annotation/version pair."""
    from .wire_string import WireString

    if annotation is int:

        async def read_int(ctx: ReadContext) -> int:
            return await ctx.reader.read_uint64()

        return read_int
    if annotation is str:

        async def read_str(ctx: ReadContext) -> str:
            return await ctx.reader.read_string(str)

        return read_str
    if annotation is bool:

        async def read_bool(ctx: ReadContext) -> bool:
            return await ctx.reader.read_bool()

        return read_bool
    if annotation is bytes:

        async def read_bytes(ctx: ReadContext) -> bytes:
            return await ctx.reader.read_bytes()

        return read_bytes
    if isinstance(annotation, type) and issubclass(annotation, IntEnum):

        async def read_enum(ctx: ReadContext) -> IntEnum:
            return annotation(await ctx.reader.read_uint64())

        return read_enum

    origin = get_origin(annotation)
    arguments = get_args(annotation)
    if origin is types.UnionType:
        non_none = tuple(arg for arg in arguments if arg is not type(None))
        if len(non_none) == 1:
            return _reader_for(non_none[0], version)
    if origin is list:
        reader = _reader_for(arguments[0], version)

        async def read_list(ctx: ReadContext) -> list[Any]:
            return [await reader(ctx) for _ in range(await ctx.reader.read_uint64())]

        return read_list
    if origin is set:
        reader = _reader_for(arguments[0], version)

        async def read_set(ctx: ReadContext) -> set[Any]:
            return {await reader(ctx) for _ in range(await ctx.reader.read_uint64())}

        return read_set
    if origin is dict:
        key_reader = _reader_for(arguments[0], version)
        value_reader = _reader_for(arguments[1], version)

        async def read_dict(ctx: ReadContext) -> dict[Any, Any]:
            result = {}
            for _ in range(await ctx.reader.read_uint64()):
                # Assignment evaluates its right-hand side before its target.
                # Keep the daemon's key-then-value wire order explicit.
                key = await key_reader(ctx)
                result[key] = await value_reader(ctx)
            return result

        return read_dict
    if isinstance(annotation, type) and issubclass(annotation, WireString):
        field_names = tuple(annotation.model_fields)
        single_field = field_names[0] if len(field_names) == 1 else None

        async def read_wire_string(ctx: ReadContext) -> WireString:
            # This is WireString.from_reader without its runtime _find_reader
            # lookup. Its compiled parent already owns the outermost failure
            # scope, so nesting a ContextVar scope here would only add cost.
            raw = await ctx.reader.read_string(str)
            if single_field is not None:
                obj = annotation.__new__(annotation)
                object.__setattr__(obj, "__pydantic_extra__", None)
                object.__setattr__(obj, "__pydantic_private__", None)
                object.__setattr__(obj, single_field, raw)
                object.__setattr__(obj, "__pydantic_fields_set__", {single_field})
                return obj

            data = annotation.from_str(raw)
            if not isinstance(data, dict):
                raise TypeError(f"from_str returned {type(data).__name__}, expected dict")
            obj = annotation.__new__(annotation)
            object.__setattr__(obj, "__pydantic_extra__", None)
            object.__setattr__(obj, "__pydantic_private__", None)
            object.__setattr__(obj, "__pydantic_fields_set__", set(data))
            for name, value in data.items():
                object.__setattr__(obj, name, value)
            return obj

        return read_wire_string
    if isinstance(annotation, type) and issubclass(annotation, WireModel):
        if _can_compile(annotation):
            codec = compile_codec(annotation, version)
            return codec.read

        async def read_custom_model(ctx: ReadContext) -> WireModel:
            return await annotation.from_reader(ctx)

        return read_custom_model
    raise TypeError(f"No reader for {annotation}")


@functools.cache
def compile_codec(model: type[WireModel], version: int) -> CompiledCodec:
    """Compile ``model`` for one protocol version using trusted local metadata.

    The emitted source is retained on the result to make the experiment
    inspectable in a REPL and in tests.  It is generated exclusively from
    package model fields; no protocol input is ever evaluated as Python code.
    """
    if not _can_compile(model):
        raise TypeError(f"{model.__name__} has a custom codec and cannot be compiled")

    schema = _wire_schema(model, version)
    fields = schema.fields
    write_predicates = tuple(
        predicate
        for field in fields
        if field.serialize and field.predicate is not None
        for predicate in (field.predicate,)
    )
    read_predicates = tuple(
        predicate
        for field in fields
        if field.deserialize and field.predicate is not None
        for predicate in (field.predicate,)
    )

    write_body: list[ast.stmt] = []
    if issubclass(model, WireRequest):
        write_body.append(ast.Expr(ast.Await(_call(_name("request_prelude"), _name("value"), _name("ctx")))))
    write_emitter = _AstLowerer(version, "write")
    predicate_index = 0
    for field in fields:
        if not field.serialize:
            continue
        value = _attribute(_name("value"), field.name)
        statements = write_emitter.write_value(field.value, value)
        if field.predicate is None:
            write_body.extend(statements)
        else:
            write_body.append(
                ast.If(_call(_subscript("write_predicates", predicate_index), _name("value")), statements, [])
            )
            predicate_index += 1

    read_body: list[ast.stmt] = [
        ast.Assign([_name("obj", ast.Store())], _call(_attribute(_name("model"), "__new__"), _name("model"))),
    ]
    for name, field in model.model_fields.items():
        if field.default is not PydanticUndefined:
            read_body.append(
                ast.Expr(
                    _call(
                        _attribute(_name("object"), "__setattr__"),
                        _name("obj"),
                        ast.Constant(name),
                        ast.Subscript(_name("defaults"), ast.Constant(name), ast.Load()),
                    )
                )
            )
        elif field.default_factory is not None:
            read_body.append(
                ast.Expr(
                    _call(
                        _attribute(_name("object"), "__setattr__"),
                        _name("obj"),
                        ast.Constant(name),
                        _call(ast.Subscript(_name("factories"), ast.Constant(name), ast.Load())),
                    )
                )
            )
    read_body.extend(
        ast.Expr(_call(_attribute(_name("object"), "__setattr__"), _name("obj"), ast.Constant(name), value))
        for name, value in (
            ("__pydantic_fields_set__", _call(_name("set"))),
            ("__pydantic_extra__", ast.Constant(None)),
            ("__pydantic_private__", ast.Constant(None)),
        )
    )
    read_emitter = _AstLowerer(version, "read")
    predicate_index = 0
    for field in fields:
        if not field.deserialize:
            continue
        local = _name(f"_field_{field.name}", ast.Store())
        statements = read_emitter.read_value(field.value, local)
        statements.extend(
            [
                ast.Expr(
                    _call(
                        _attribute(_name("object"), "__setattr__"),
                        _name("obj"),
                        ast.Constant(field.name),
                        _name(local.id),
                    )
                ),
                ast.Expr(
                    _call(
                        _attribute(_attribute(_name("obj"), "__pydantic_fields_set__"), "add"), ast.Constant(field.name)
                    )
                ),
            ],
        )
        if field.predicate is None:
            read_body.extend(statements)
        else:
            read_body.append(
                ast.If(_call(_subscript("read_predicates", predicate_index), _name("obj")), statements, [])
            )
            predicate_index += 1
    read_body.append(ast.Return(_name("obj")))

    write_function = ast.AsyncFunctionDef(
        name="write",
        args=ast.arguments(
            posonlyargs=[], args=[ast.arg("value"), ast.arg("ctx")], kwonlyargs=[], kw_defaults=[], defaults=[]
        ),
        body=write_body or [ast.Pass()],
        decorator_list=[],
        type_params=[],
    )
    read_function = ast.AsyncFunctionDef(
        name="read",
        args=ast.arguments(posonlyargs=[], args=[ast.arg("ctx")], kwonlyargs=[], kw_defaults=[], defaults=[]),
        body=[
            ast.With(
                items=[ast.withitem(_call(_name("deserialization_scope"), _name("ctx"), _name("model")), None)],
                body=read_body,
            )
        ],
        decorator_list=[],
        type_params=[],
    )

    defaults = {
        name: field.default for name, field in model.model_fields.items() if field.default is not PydanticUndefined
    }
    factories = {
        name: field.default_factory for name, field in model.model_fields.items() if field.default_factory is not None
    }
    namespace: dict[str, Any] = {
        "deserialization_scope": deserialization_scope,
        "defaults": defaults,
        "factories": factories,
        "model": model,
        "object": object,
        "read_predicates": read_predicates,
        "read_adapters": tuple(read_emitter.adapters),
        "read_codecs": tuple(read_emitter.codecs),
        "read_enums": tuple(read_emitter.enums),
        "request_prelude": _write_request_prelude,
        "write_adapters": tuple(write_emitter.adapters),
        "write_codecs": tuple(write_emitter.codecs),
        "write_predicates": write_predicates,
    }
    module = ast.fix_missing_locations(ast.Module(body=[write_function, read_function], type_ignores=[]))
    write_source = ast.unparse(ast.Module(body=[write_function], type_ignores=[]))
    read_source = ast.unparse(ast.Module(body=[read_function], type_ignores=[]))
    exec(compile(module, "<nix-daemon-codec>", "exec"), namespace)
    return CompiledCodec(namespace["write"], namespace["read"], write_source, read_source, schema)
