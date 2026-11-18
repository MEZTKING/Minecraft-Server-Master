"""Minimal reader (and writer) for Minecraft's NBT binary format.

NBT ("Named Binary Tag") is the format Minecraft uses for ``level.dat``,
``playerdata/<uuid>.dat`` and the chunks inside region files.

Format summary (Java Edition, big-endian):

    tag      = type_id:u8  [name_len:u16  name:utf8  payload]   (type 0 = End has no name)
    payloads = see ``_read_payload`` below

Files on disk are usually gzip-compressed (``level.dat``, playerdata) and
sometimes zlib-compressed or uncompressed; :func:`load` detects this.

The reader converts tags into plain Python values:

    Byte/Short/Int/Long -> int          Float/Double -> float
    String              -> str          List         -> list
    Compound            -> dict         Byte/Int/Long arrays -> list[int]

Type information is lost on purpose: this project only *reads* game data.
A small writer is included so the test-suite can build realistic fixtures.
"""

from __future__ import annotations

import gzip
import io
import struct
import zlib
from typing import Any, BinaryIO

TAG_END = 0
TAG_BYTE = 1
TAG_SHORT = 2
TAG_INT = 3
TAG_LONG = 4
TAG_FLOAT = 5
TAG_DOUBLE = 6
TAG_BYTE_ARRAY = 7
TAG_STRING = 8
TAG_LIST = 9
TAG_COMPOUND = 10
TAG_INT_ARRAY = 11
TAG_LONG_ARRAY = 12


class NBTError(ValueError):
    """Raised when data is not valid NBT."""


def _read_exact(stream: BinaryIO, n: int) -> bytes:
    data = stream.read(n)
    if len(data) != n:
        raise NBTError(f"unexpected end of data (wanted {n} bytes, got {len(data)})")
    return data


def _read_fmt(stream: BinaryIO, fmt: str) -> Any:
    size = struct.calcsize(fmt)
    return struct.unpack(fmt, _read_exact(stream, size))[0]


def _read_string(stream: BinaryIO) -> str:
    length = _read_fmt(stream, ">H")
    # Minecraft uses "modified UTF-8"; for normal text this is plain UTF-8.
    return _read_exact(stream, length).decode("utf-8", errors="replace")


def _read_payload(stream: BinaryIO, tag_type: int, depth: int = 0) -> Any:
    if depth > 512:
        raise NBTError("NBT nesting too deep")
    if tag_type == TAG_BYTE:
        return _read_fmt(stream, ">b")
    if tag_type == TAG_SHORT:
        return _read_fmt(stream, ">h")
    if tag_type == TAG_INT:
        return _read_fmt(stream, ">i")
    if tag_type == TAG_LONG:
        return _read_fmt(stream, ">q")
    if tag_type == TAG_FLOAT:
        return _read_fmt(stream, ">f")
    if tag_type == TAG_DOUBLE:
        return _read_fmt(stream, ">d")
    if tag_type == TAG_BYTE_ARRAY:
        n = _read_fmt(stream, ">i")
        return list(struct.unpack(f">{n}b", _read_exact(stream, n)))
    if tag_type == TAG_STRING:
        return _read_string(stream)
    if tag_type == TAG_LIST:
        item_type = _read_fmt(stream, ">b")
        n = _read_fmt(stream, ">i")
        return [_read_payload(stream, item_type, depth + 1) for _ in range(max(n, 0))]
    if tag_type == TAG_COMPOUND:
        result: dict[str, Any] = {}
        while True:
            child_type = _read_fmt(stream, ">b")
            if child_type == TAG_END:
                return result
            name = _read_string(stream)
            result[name] = _read_payload(stream, child_type, depth + 1)
    if tag_type == TAG_INT_ARRAY:
        n = _read_fmt(stream, ">i")
        return list(struct.unpack(f">{n}i", _read_exact(stream, 4 * n)))
    if tag_type == TAG_LONG_ARRAY:
        n = _read_fmt(stream, ">i")
        return list(struct.unpack(f">{n}q", _read_exact(stream, 8 * n)))
    raise NBTError(f"unknown tag type {tag_type}")


def decompress(data: bytes) -> bytes:
    """Return raw NBT bytes, undoing gzip or zlib compression if present."""
    if data[:2] == b"\x1f\x8b":
        return gzip.decompress(data)
    if data[:1] == b"\x78":  # zlib header
        try:
            return zlib.decompress(data)
        except zlib.error:
            pass
    return data


def loads(data: bytes) -> dict[str, Any]:
    """Parse NBT bytes (compressed or not) and return the root compound.

    The root tag's own name (almost always ``""``) is discarded.
    """
    raw = decompress(data)
    stream = io.BytesIO(raw)
    root_type = _read_fmt(stream, ">b")
    if root_type != TAG_COMPOUND:
        raise NBTError(f"root tag must be a compound, got type {root_type}")
    _read_string(stream)
    return _read_payload(stream, TAG_COMPOUND)


def load(path: str) -> dict[str, Any]:
    """Read and parse an NBT file from disk."""
    with open(path, "rb") as fh:
        return loads(fh.read())


# ---------------------------------------------------------------------------
# Writer (used by tests to generate fixtures; not needed at runtime)
# ---------------------------------------------------------------------------

class Typed:
    """Wrap a value to force a specific NBT tag type when writing."""

    def __init__(self, tag_type: int, value: Any):
        self.tag_type = tag_type
        self.value = value


def _infer_type(value: Any) -> int:
    if isinstance(value, Typed):
        return value.tag_type
    if isinstance(value, bool):
        return TAG_BYTE
    if isinstance(value, int):
        return TAG_INT if -(2**31) <= value < 2**31 else TAG_LONG
    if isinstance(value, float):
        return TAG_DOUBLE
    if isinstance(value, str):
        return TAG_STRING
    if isinstance(value, list):
        return TAG_LIST
    if isinstance(value, dict):
        return TAG_COMPOUND
    raise NBTError(f"cannot encode {type(value).__name__} as NBT")


def _write_string(out: BinaryIO, s: str) -> None:
    raw = s.encode("utf-8")
    out.write(struct.pack(">H", len(raw)))
    out.write(raw)


def _write_payload(out: BinaryIO, tag_type: int, value: Any) -> None:
    if isinstance(value, Typed):
        value = value.value
    simple = {TAG_BYTE: ">b", TAG_SHORT: ">h", TAG_INT: ">i", TAG_LONG: ">q",
              TAG_FLOAT: ">f", TAG_DOUBLE: ">d"}
    if tag_type in simple:
        out.write(struct.pack(simple[tag_type], value))
    elif tag_type == TAG_STRING:
        _write_string(out, value)
    elif tag_type == TAG_LIST:
        item_type = _infer_type(value[0]) if value else TAG_END
        out.write(struct.pack(">bi", item_type, len(value)))
        for item in value:
            _write_payload(out, item_type, item)
    elif tag_type == TAG_COMPOUND:
        for name, child in value.items():
            child_type = _infer_type(child)
            out.write(struct.pack(">b", child_type))
            _write_string(out, name)
            _write_payload(out, child_type, child)
        out.write(struct.pack(">b", TAG_END))
    elif tag_type in (TAG_BYTE_ARRAY, TAG_INT_ARRAY, TAG_LONG_ARRAY):
        code = {TAG_BYTE_ARRAY: "b", TAG_INT_ARRAY: "i", TAG_LONG_ARRAY: "q"}[tag_type]
        out.write(struct.pack(">i", len(value)))
        out.write(struct.pack(f">{len(value)}{code}", *value))
    else:
        raise NBTError(f"cannot write tag type {tag_type}")


def dumps(root: dict[str, Any], compress: bool = True) -> bytes:
    """Serialise a dict as an NBT root compound (gzip-compressed by default)."""
    out = io.BytesIO()
    out.write(struct.pack(">b", TAG_COMPOUND))
    _write_string(out, "")
    _write_payload(out, TAG_COMPOUND, root)
    data = out.getvalue()
    return gzip.compress(data) if compress else data
