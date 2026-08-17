"""
Nix daemon wire protocol primitives (async).

All integers are little-endian uint64. Strings are length-prefixed with
zero-padding to 8-byte alignment.

Read functions are async (for asyncio.StreamReader / asyncssh.SSHReader).
Write functions are sync (writer.write() buffers; callers await drain()).
"""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable

import asyncssh
from environs import env

from .constants import (
    FEATURE_EXCHANGE_PROTOCOL as FEATURE_EXCHANGE_PROTOCOL,
    MINIMUM_REMOTE_PROTOCOL as MINIMUM_REMOTE_PROTOCOL,
    PROTOCOL_VERSION as PROTOCOL_VERSION,
    STANDARD_FEATURES as STANDARD_FEATURES,
    STDERR_LAST as STDERR_LAST,
    SUPPORTED_STANDARD_FEATURES as SUPPORTED_STANDARD_FEATURES,
    WORKER_MAGIC_1 as WORKER_MAGIC_1,
    WORKER_MAGIC_2 as WORKER_MAGIC_2,
    negotiate_features as negotiate_features,
    proto as proto,
    proto_str as proto_str,
)
from .serde.context import ReadContext
from .serde.logs import LogMessage, drain as drain_log_stream, read_stream as read_log_stream

if TYPE_CHECKING:
    import asyncio
    from collections.abc import AsyncIterator, Iterable


_CHUNK_SIZE = env.int("PYNIXD_CHUNK_SIZE", 1024 * 1024)

_SSH_WINDOW_SIZE = env.int("PYNIXD_SSH_WINDOW", 16 * 1024 * 1024)

_SSH_READ_AHEAD = 16 * 1024  # read-ahead size to amortize asyncssh lock overhead

_UNIX_READ_AHEAD = 16 * 1024  # read-ahead size to amortize syscall overhead

_UINT64_STRUCT = struct.Struct("<Q")


def _nar_pad(n: int) -> int:
    """Return number of padding bytes needed for 8-byte alignment."""
    return (8 - n % 8) % 8


# ── NixReader / NixWriter ──────────────────────────────────────


class NixReader:
    """Wraps AsyncReader with wire protocol methods and dirty checking."""

    def __init__(self, identifier: str = "unknown") -> None:
        self.identifier = identifier

    async def readexactly(self, n: int) -> bytes:
        raise NotImplementedError

    async def read_uint64(self) -> int:
        return _UINT64_STRUCT.unpack(await self.readexactly(8))[0]

    async def read_bool(self) -> bool:
        return bool(await self.read_uint64())

    async def read_optional_uint64(self) -> int | None:
        tag = await self.read_uint64()
        if tag == 0:
            return None
        return await self.read_uint64()

    async def read_bytes(self) -> bytes:
        length = await self.read_uint64()
        data = await self.readexactly(length)
        pad = _nar_pad(length)
        if pad:
            await self.readexactly(pad)
        return data

    async def read_string[T = str](self, tp: Callable[[str], T] = str) -> T:
        return tp((await self.read_bytes()).decode("utf-8"))

    async def read_string_list[T = str](self, tp: Callable[[str], T] = str) -> list[T]:
        count: int = await self.read_uint64()
        return [await self.read_string(tp) for _ in range(count)]

    async def read_string_set[T = str](self, tp: Callable[[str], T] = str) -> set[T]:
        count: int = await self.read_uint64()
        return {await self.read_string(tp) for _ in range(count)}

    async def drain_stderr(self, raise_on_error: bool = True) -> None:
        """Read and discard all stderr messages until STDERR_LAST."""
        await drain_log_stream(ReadContext(reader=self, version=PROTOCOL_VERSION), raise_on_error=raise_on_error)

    def read_stderr(self) -> AsyncIterator[LogMessage]:
        """Return an AsyncIterator that yields stderr messages until STDERR_LAST."""
        return read_log_stream(ReadContext(reader=self, version=PROTOCOL_VERSION))

    async def is_dirty(self) -> bool:
        return self._transport_is_dirty()

    def _transport_is_dirty(self) -> bool:
        return False


class SSHNixReader(NixReader):
    def __init__(self, reader: asyncssh.SSHReader, identifier: str = "unknown") -> None:
        super().__init__(identifier=identifier)
        self.reader = reader

    async def readexactly(self, n: int) -> bytes:
        try:
            return await self.reader.readexactly(n)
        except asyncssh.misc.ConnectionLost:
            raise EOFError("SSH connection lost") from None

    def _transport_is_dirty(self) -> bool:
        if hasattr(self.reader, "get_read_buffer_size"):
            return self.reader.get_read_buffer_size() > 0  # type: ignore[reportAttributeAccessIssue]
        # Fallback to private access if API changed or is missing
        # _recv_buf is a dict of list of bytes
        buf = self.reader._session._recv_buf.get(
            self.reader._datatype,
            [],
        )
        return any(isinstance(chunk, (bytes, bytearray)) and chunk for chunk in buf)


class UnixNixReader(NixReader):
    def __init__(
        self,
        reader: asyncio.StreamReader,
        identifier: str = "unknown",
    ) -> None:
        super().__init__(identifier=identifier)
        self.reader = reader

    async def readexactly(self, n: int) -> bytes:
        return await self.reader.readexactly(n)

    def _transport_is_dirty(self) -> bool:
        return bool(self.reader._buffer)  # type: ignore[attr-defined]


class StrCoercible(Protocol):
    """Anything that can be converted to ``str`` via ``str()``."""

    def __str__(self) -> str: ...


class NixWriter:
    """Wraps AsyncWriter with wire protocol methods."""

    def __init__(self, identifier: str = "unknown") -> None:
        self.identifier = identifier

    def write(self, data: bytes) -> None:
        self._write_to_transport(data)

    async def drain(self) -> None:
        await self._drain_transport()

    async def is_dirty(self) -> bool:
        return self._transport_is_dirty()

    def _write_to_transport(self, data: bytes) -> None:
        raise NotImplementedError

    async def _drain_transport(self) -> None:
        raise NotImplementedError

    def _transport_is_dirty(self) -> bool:
        return False

    async def close(self) -> None:
        """Close the underlying transport. Override in subclasses."""

    def write_uint64(self, val: int) -> None:
        self.write(_UINT64_STRUCT.pack(val))

    def write_bool(self, v: bool) -> None:
        self.write_uint64(1 if v else 0)

    def write_uint64s(self, vals: list[int]) -> None:
        if not vals:
            return
        self.write(struct.pack(f"<{len(vals)}Q", *vals))

    def write_optional_uint64(self, val: int | None) -> None:
        if val is None:
            self.write_uint64(0)
        else:
            self.write_uint64(1)
            self.write_uint64(val)

    def write_bytes(self, data: bytes) -> None:
        self.write_uint64(len(data))
        self.write(data)
        pad: int = _nar_pad(len(data))
        if pad:
            self.write(b"\0" * pad)

    def write_string(self, s: StrCoercible) -> None:
        self.write_bytes(str(s).encode("utf-8"))

    def write_string_list(self, items: Iterable[StrCoercible]) -> None:
        items_list = list(items)
        self.write_uint64(len(items_list))
        for item in items_list:
            self.write_string(item)

    def write_string_set(self, items: Iterable[StrCoercible]) -> None:
        self.write_string_list(items)

    def framed(self) -> FramedWriter:
        """Create a FramedWriter that writes framed data to this writer."""
        return FramedWriter(self)


class BytesWriter(NixWriter):
    """A NixWriter that writes to a bytearray."""

    def __init__(self, identifier: str = "memory") -> None:
        super().__init__(identifier=identifier)
        self._buf = bytearray()

    def write(self, data: bytes) -> None:
        self._buf.extend(data)

    async def drain(self) -> None:
        pass

    def get_bytes(self) -> bytes:
        return bytes(self._buf)

    def tell(self) -> int:
        return len(self._buf)


class BytesReader(NixReader):
    """A NixReader that reads from an in-memory bytes buffer.

    Mirrors BytesWriter for roundtrip testing: write with BytesWriter,
    then read back with BytesReader.
    """

    def __init__(self, data: bytes, identifier: str = "memory") -> None:
        super().__init__(identifier=identifier)
        self._data = data
        self._pos = 0

    async def readexactly(self, n: int) -> bytes:
        if self._pos + n > len(self._data):
            have = len(self._data) - self._pos
            raise EOFError(
                f"BytesReader: need {n} bytes, have {have} remaining (total {len(self._data)}, at offset {self._pos})",
            )
        result = self._data[self._pos : self._pos + n]
        self._pos += n
        return result


class SSHNixWriter(NixWriter):
    def __init__(self, writer: asyncssh.SSHWriter, identifier: str = "unknown") -> None:
        super().__init__(identifier=identifier)
        self.writer = writer

    def _write_to_transport(self, data: bytes) -> None:
        self.writer.write(data)

    async def _drain_transport(self) -> None:
        await self.writer.drain()

    def _transport_is_dirty(self) -> bool:
        return self.writer.channel.get_write_buffer_size() > 0

    async def close(self) -> None:
        await self.drain()
        self.writer.close()


class UnixNixWriter(NixWriter):
    def __init__(
        self,
        writer: asyncio.StreamWriter,
        identifier: str = "unknown",
    ) -> None:
        super().__init__(identifier=identifier)
        self.writer = writer

    def _write_to_transport(self, data: bytes) -> None:
        self.writer.write(data)

    async def _drain_transport(self) -> None:
        await self.writer.drain()

    def _transport_is_dirty(self) -> bool:
        return self.writer.transport.get_write_buffer_size() > 0

    async def close(self) -> None:
        await self.drain()
        self.writer.close()
        await self.writer.wait_closed()


async def stream_parse_nar(
    src: NixReader,
    dst: NixWriter,
    capture: bool = False,
) -> bytes | None:
    """Copy a NAR archive from src to dst by parsing the NAR structure.

    The NAR format is a sequence of length-prefixed, 8-byte-padded tokens.
    We track "(" / ")" depth to know when the NAR ends.
    The token after "contents" is binary file data and must not be interpreted.

    If capture=True, returns the raw NAR bytes.
    """
    chunks: list[bytes] | None = [] if capture else None

    async def _fwd_token() -> bytes:
        """Read and forward one token, returning its raw data."""
        length: int = await src.read_uint64()
        dst.write_uint64(length)
        if chunks is not None:
            chunks.append(struct.pack("<Q", length))

        data: bytes = b""
        if length > 0:
            data = await src.readexactly(length)
            dst.write(data)
            if chunks is not None:
                chunks.append(data)

        pad: int = _nar_pad(length)
        if pad:
            pad_data: bytes = await src.readexactly(pad)
            dst.write(pad_data)
            if chunks is not None:
                chunks.append(pad_data)

        return data

    depth: int = 0
    after_contents: bool = False

    while True:
        raw: bytes = await _fwd_token()

        # Token after "contents" is binary file data — skip interpretation
        if after_contents:
            after_contents = False
            continue

        try:
            tok: str = raw.decode("ascii")
        except (UnicodeDecodeError, ValueError):
            continue

        if tok == "(":
            depth += 1
        elif tok == ")":
            depth -= 1
            if depth == 0:
                break
        elif tok == "contents":
            after_contents = True

    if chunks is not None:
        return b"".join(chunks)
    return None


async def forward_framed(src: NixReader, dst: NixWriter) -> None:
    """Forward framed data (chunks terminated by size=0) from src to dst.

    Streams the data without buffering the entire payload in memory.
    Each chunk is: uint64 length + raw data, terminated by length=0.
    """
    while True:
        size = await src.read_uint64()
        if size == 0:
            dst.write_uint64(0)
            break
        data = await src.readexactly(size)
        dst.write_uint64(size)
        dst.write(data)
    await dst.drain()


class FramedReader(NixReader):
    """Reassembles framed chunks into a logical byte stream.

    Reads [uint64 size][data]... [uint64 0] from a DaemonReader and
    presents a readexactly() interface. Inherits from NixReader so it
    can be passed directly to code expecting a NixReader.

    This allows parsing structured data (PathInfo, NAR tokens, etc.)
    from inside framed payloads without buffering the entire payload.
    """

    def __init__(self, src: NixReader) -> None:
        super().__init__()
        self._src = src
        self._buf = bytearray()
        self._pos = 0
        self._eof = False

    def _compact(self) -> None:
        if self._pos > 0:
            del self._buf[: self._pos]
            self._pos = 0

    async def readexactly(self, n: int) -> bytes:
        while (len(self._buf) - self._pos) < n:
            if self._eof:
                have = len(self._buf) - self._pos
                raise EOFError(f"Framed stream ended, need {n} bytes, have {have}")
            size = await self._src.read_uint64()
            if size == 0:
                self._eof = True
                have = len(self._buf) - self._pos
                if have < n:
                    raise EOFError(f"Framed stream ended, need {n} bytes, have {have}")
                break
            data = await self._src.readexactly(size)
            self._buf.extend(data)
        result = bytes(self._buf[self._pos : self._pos + n])
        self._pos += n
        if self._pos > 64 * 1024:
            self._compact()
        return result

    async def ensure_eof(self) -> None:
        """Read until the end of the framed stream (size=0).

        Discards any remaining data in the current framed stream.
        This is important to ensure the underlying reader is positioned
        correctly for the next operation.
        """
        if self._eof:
            # We already saw the 0 frame, but there might be unread data in _buf.
            # However, for protocol sync, seeing the 0 frame from _src is what matters.
            return

        while True:
            size = await self._src.read_uint64()
            if size == 0:
                self._eof = True
                break
            await self._src.readexactly(size)

    async def is_dirty(self) -> bool:
        if self._pos < len(self._buf):
            return True
        return await self._src.is_dirty()

    @property
    def at_eof(self) -> bool:
        return self._eof and self._pos == len(self._buf)


class FramedWriter(NixWriter):
    """Buffers writes and emits framed chunks to an underlying writer.

    Framed format: [uint64 size][data]... [uint64 0] (terminator).
    Call finalize() when done to flush remaining data and write terminator.
    """

    def __init__(
        self,
        dst: NixWriter,
    ) -> None:
        super().__init__(identifier=dst.identifier)
        self._dst = dst

    def write_bytes(self, data: bytes) -> None:
        self._dst.write(data)

    def write(self, data: bytes) -> None:
        self._dst.write_uint64(len(data))
        self._dst.write(data)

    async def finalize(self) -> None:
        self._dst.write_uint64(0)  # terminator
        await self._dst.drain()


async def stream_nar(
    src: NixReader,
    dst: NixWriter,
    chunk_size: int = _CHUNK_SIZE,
) -> None:
    """Copy a NAR archive from src to dst, streaming large tokens in chunks.

    Like copy_nar but bounded memory: large file data tokens (> chunk_size)
    are read and written in chunk_size pieces instead of buffered fully.
    """
    depth: int = 0
    after_contents: bool = False

    while True:
        # Read token header (8-byte LE length prefix)
        length: int = await src.read_uint64()
        pad: int = _nar_pad(length)

        # Always write header
        dst.write_uint64(length)

        if length <= chunk_size:
            # Small token: read all at once
            data: bytes = await src.readexactly(length) if length else b""
            dst.write(data)
            if pad:
                pad_data: bytes = await src.readexactly(pad)
                dst.write(pad_data)

            if after_contents:
                after_contents = False
            else:
                try:
                    tok: str = data.decode("ascii") if data else ""
                except UnicodeDecodeError:
                    tok = ""
                if tok == "(":
                    depth += 1
                elif tok == ")":
                    depth -= 1
                    if depth == 0:
                        break
                elif tok == "contents":
                    after_contents = True
        else:
            # Large token: stream data in chunk_size pieces
            remaining = length
            while remaining > 0:
                to_read = min(remaining, chunk_size)
                chunk = await src.readexactly(to_read)
                dst.write(chunk)
                await dst.drain()
                remaining -= to_read
            if pad:
                pad_data = await src.readexactly(pad)
                dst.write(pad_data)
            # Large tokens are always file data (after "contents")
            after_contents = False


async def pipe_raw_to_framed(
    src: NixReader,
    dst: NixWriter,
    size: int,
    chunk_size: int = _CHUNK_SIZE,
) -> None:
    """Read size bytes from src and write as framed data to dst.

    No NAR parsing — just reads raw bytes and wraps them in frames.
    Use this when the total byte count is known (e.g. from nar_size).
    """
    remaining = size
    while remaining > 0:
        to_read = min(remaining, chunk_size)
        chunk = await src.readexactly(to_read)
        dst.write_uint64(len(chunk))
        dst.write(chunk)
        remaining -= to_read
    dst.write_uint64(0)  # framing terminator
    await dst.drain()


async def pipe_raw_to_framed_writer(
    src: NixReader,
    fw: FramedWriter,
    size: int,
    chunk_size: int = _CHUNK_SIZE,
) -> None:
    """Read size bytes from src and write through a FramedWriter.

    Like pipe_raw_to_framed but writes into an existing FramedWriter
    (for AddMultipleToStore where multiple NARs share one framed stream).
    Does NOT call finalize — caller is responsible for that.
    """
    remaining = size
    while remaining > 0:
        to_read = min(remaining, chunk_size)
        chunk = await src.readexactly(to_read)
        fw.write(chunk)
        remaining -= to_read
