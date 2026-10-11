"""Layer 1-3 codec: USB frames, channel headers, and the MessagePack stream.

Three layers stack inside one 512-byte bulk transfer on endpoint 0x81:

1. **USB frame** - an 8-byte header plus a padded payload::

       0..3  payload length, u24 LE (excluding this 8-byte header)
       3     flags (0x18 data, 0x28 handshake)
       4..6  destination node, u16 LE
       6..8  source node, u16 LE
       8..   payload, padded to a 4-byte boundary

   One transfer may coalesce several frames; :meth:`Frame.decode_transfer`
   walks them all.

2. **Channel header** - the first 8 bytes of a frame payload::

       0..2  sequence, u16 BE
       2..4  message type, u16 BE (0x02 hello, 0x04 data, 0x08 ack, 0x10 keepalive)
       4..8  acknowledgement, u32 LE (0x1000 + bytes consumed)

3. **Stream prefix + MessagePack body** - carried by data messages::

       0..2  originator, u16 LE (1 host, 0 device)
       2..4  service id, u16 LE
       4..8  body length, u32 LE
       8..   MessagePack body

   A message may straddle any number of frames, so :class:`StreamReader`
   buffers raw stream bytes until a whole length-prefixed message is present.

The MessagePack encoder here is hand-rolled rather than delegated to the
``msgpack`` package so that **floats go out as float32** (0xca) the way the
device sends and expects them, and so that map key order is preserved. A
future variant will need to preserve integer/blob tag *widths* for byte-exact
preset round trips - see ``README`` (M1+).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any

import msgpack

from . import opcodes as op


def pad4(n: int) -> int:
    return (n + 3) & ~3


# ---------------------------------------------------------------- errors ---


class CodecError(Exception):
    """A malformed frame, channel header or stream message."""


class FrameError(CodecError):
    pass


class StreamError(CodecError):
    pass


# ------------------------------------------------------------- USB frames ---


@dataclass
class Frame:
    flags: int
    dst: int
    src: int
    payload: bytes = b""
    #: Bytes between the declared payload and the 4-byte-aligned transfer
    #: boundary. Host frames use zeroes; real device frames sometimes expose
    #: opaque non-zero bytes here, so decode/encode retains them.
    padding: bytes = b""

    @classmethod
    def new(cls, dst: int, src: int, payload: bytes, flags: int = op.FLAG_DATA) -> "Frame":
        pad = pad4(8 + len(payload)) - (8 + len(payload))
        return cls(flags=flags, dst=dst, src=src, payload=bytes(payload), padding=b"\x00" * pad)

    def encode(self) -> bytes:
        n = len(self.payload)
        if n > 0x00FF_FFFF:
            raise FrameError(f"frame payload of {n} bytes does not fit its 24-bit length")
        out = bytearray()
        out += n.to_bytes(3, "little")
        out.append(self.flags)
        out += self.dst.to_bytes(2, "little")
        out += self.src.to_bytes(2, "little")
        out += self.payload
        pad = pad4(8 + n) - (8 + n)
        if len(self.padding) == pad:
            out += self.padding
        else:
            out += b"\x00" * pad
        return bytes(out)

    @property
    def wire_len(self) -> int:
        return pad4(8 + len(self.payload))

    @classmethod
    def _decode_prefix(cls, buf: bytes, offset: int) -> tuple["Frame", int]:
        if len(buf) - offset < 8:
            raise FrameError(f"frame of {len(buf) - offset} bytes is shorter than the header")
        base = offset
        n = int.from_bytes(buf[base : base + 3], "little")
        if base + 8 + n > len(buf):
            raise FrameError(
                f"frame declares {n} payload bytes but {len(buf) - base - 8} are present"
            )
        frame_end = base + 8 + n
        expected = base + pad4(8 + n)
        if len(buf) < expected:
            raise FrameError(
                f"one padded frame should occupy {expected - base} bytes, "
                f"but the input has {len(buf) - base}"
            )
        frame = cls(
            flags=buf[base + 3],
            dst=int.from_bytes(buf[base + 4 : base + 6], "little"),
            src=int.from_bytes(buf[base + 6 : base + 8], "little"),
            payload=bytes(buf[base + 8 : frame_end]),
            padding=bytes(buf[frame_end:expected]),
        )
        return frame, expected - base

    @classmethod
    def decode(cls, buf: bytes) -> "Frame":
        frame, consumed = cls._decode_prefix(buf, 0)
        if consumed != len(buf):
            raise FrameError(f"one frame should occupy {consumed} bytes, input has {len(buf)}")
        return frame

    @classmethod
    def decode_transfer(cls, buf: bytes) -> list["Frame"]:
        """Decode every frame coalesced into one USB bulk transfer."""
        frames: list[Frame] = []
        offset = 0
        while offset < len(buf):
            frame, consumed = cls._decode_prefix(buf, offset)
            frames.append(frame)
            offset += consumed
        return frames


# -------------------------------------------------------- channel headers ---


@dataclass
class ChannelHeader:
    seq: int
    msg_type: int
    ack: int

    SIZE = 8

    def has_data(self) -> bool:
        return bool(self.msg_type & op.MSG_DATA)

    def encode_into(self, out: bytearray) -> None:
        out += self.seq.to_bytes(2, "big")
        out += self.msg_type.to_bytes(2, "big")
        out += self.ack.to_bytes(4, "little")

    @classmethod
    def decode(cls, buf: bytes) -> tuple["ChannelHeader", bytes] | None:
        if len(buf) < cls.SIZE:
            return None
        return (
            cls(
                seq=int.from_bytes(buf[0:2], "big"),
                msg_type=int.from_bytes(buf[2:4], "big"),
                ack=int.from_bytes(buf[4:8], "little"),
            ),
            bytes(buf[cls.SIZE :]),
        )


# ------------------------------------------------- MessagePack (custom) ---


def _pack_uint(n: int) -> bytes:
    if n <= 0x7F:
        return bytes([n])
    if n <= 0xFF:
        return b"\xcc" + bytes([n])
    if n <= 0xFFFF:
        return b"\xcd" + struct.pack(">H", n)
    if n <= 0xFFFF_FFFF:
        return b"\xce" + struct.pack(">I", n)
    return b"\xcf" + struct.pack(">Q", n)


def _pack_int(n: int) -> bytes:
    if n >= 0:
        return _pack_uint(n)
    if n >= -32:
        return bytes([n & 0xFF])
    if n >= -128:
        return b"\xd0" + struct.pack(">b", n)
    if n >= -32768:
        return b"\xd1" + struct.pack(">h", n)
    if n >= -2_147_483_648:
        return b"\xd2" + struct.pack(">i", n)
    return b"\xd3" + struct.pack(">q", n)


def _pack_str(s: str) -> bytes:
    b = s.encode("utf-8")
    n = len(b)
    if n <= 31:
        return bytes([0xA0 | n]) + b
    if n <= 0xFF:
        return b"\xd9" + bytes([n]) + b
    if n <= 0xFFFF:
        return b"\xda" + struct.pack(">H", n) + b
    return b"\xdb" + struct.pack(">I", n) + b


def _pack_bin(b: bytes) -> bytes:
    n = len(b)
    if n <= 0xFF:
        return b"\xc4" + bytes([n]) + b
    if n <= 0xFFFF:
        return b"\xc5" + struct.pack(">H", n) + b
    return b"\xc6" + struct.pack(">I", n) + b


def _pack_array_header(n: int) -> bytes:
    if n <= 15:
        return bytes([0x90 | n])
    if n <= 0xFFFF:
        return b"\xdc" + struct.pack(">H", n)
    return b"\xdd" + struct.pack(">I", n)


def _pack_map_header(n: int) -> bytes:
    if n <= 15:
        return bytes([0x80 | n])
    if n <= 0xFFFF:
        return b"\xde" + struct.pack(">H", n)
    return b"\xdf" + struct.pack(">I", n)


class F32(float):
    """A float that must be encoded as MessagePack float32 (0xca).

    The device stores and sends parameter values as float32; a Python float
    would otherwise be encoded as float64 (0xcb)."""


def pack(value: Any) -> bytes:
    """Encode one value to MessagePack, preserving map order and trapping F32."""
    out = bytearray()
    _pack_into(value, out)
    return bytes(out)


def _pack_into(value: Any, out: bytearray) -> None:
    if value is None:
        out.append(0xC0)
    elif value is True:
        out.append(0xC3)
    elif value is False:
        out.append(0xC2)
    elif isinstance(value, int):
        out += _pack_int(value)
    elif isinstance(value, F32):
        out += b"\xca" + struct.pack(">f", float(value))
    elif isinstance(value, float):
        # Bare Python floats are emitted as float32 too: the device never
        # sends float64 and parameter writes are float32 everywhere.
        out += b"\xca" + struct.pack(">f", value)
    elif isinstance(value, str):
        out += _pack_str(value)
    elif isinstance(value, (bytes, bytearray)):
        out += _pack_bin(bytes(value))
    elif isinstance(value, (list, tuple)):
        out += _pack_array_header(len(value))
        for item in value:
            _pack_into(item, out)
    elif isinstance(value, dict):
        out += _pack_map_header(len(value))
        for k, v in value.items():
            _pack_into(k, out)
            _pack_into(v, out)
    else:  # pragma: no cover - defensive
        raise TypeError(f"cannot MessagePack-encode {type(value).__name__}")


def _looks_like_text(b: bytes) -> bool:
    try:
        b.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return all(c in (0, 9, 10, 13) or c >= 0x20 for c in b)


def _normalize(value: Any) -> Any:
    """Decode with ``raw=True``, then turn text-like byte strings back to ``str``.

    Line 6 uses the MessagePack **str** type for both text (names) *and* binary
    (the preset document, IR samples), so decoding it as UTF-8 up front makes
    the device's own ``l6-helix`` blob fail with 'invalid start byte'. We keep
    everything raw and only decode to text when the bytes are valid UTF-8 and
    printable; binary blobs (full of control bytes) stay ``bytes``. Line 6's
    strings are C strings, so trailing NULs are stripped from text.
    """
    if isinstance(value, (bytes, bytearray)):
        b = bytes(value)
        if b and _looks_like_text(b):
            return b.rstrip(b"\x00").decode("utf-8")
        return b
    if isinstance(value, list):
        return [_normalize(v) for v in value]
    if isinstance(value, dict):
        return {_normalize(k): _normalize(v) for k, v in value.items()}
    return value


def _unpacker() -> "msgpack.Unpacker":
    # raw=True: do not force UTF-8 on MessagePack str/bytes values.
    return msgpack.Unpacker(raw=True, use_list=True, strict_map_key=False)


def unpack_values(data: bytes, count: int = 1) -> list:
    """Decode the first ``count`` MessagePack values, ignoring any trailing bytes.

    Real device replies carry an opaque tail inside the declared body on
    roughly every other fresh session; the first value is the payload and the
    rest is not a second value. A preset document is itself three values
    (magic, section table, tone map).
    """
    up = _unpacker()
    up.feed(data)
    out = []
    for _ in range(count):
        try:
            out.append(_normalize(up.unpack()))
        except Exception as exc:  # msgpack raises a variety of decode errors
            raise StreamError(f"stream message is not MessagePack: {exc}") from exc
    return out


def unpack_first(data: bytes) -> Any:
    return unpack_values(data, 1)[0]


# ------------------------------------------------------------- stream ---


@dataclass
class StreamMessage:
    flags: int
    service: int
    body: Any


class StreamReader:
    """Reassembles a channel's byte stream into whole length-prefixed messages."""

    MAX_STREAM_MESSAGE = 16 * 1024 * 1024

    def __init__(self) -> None:
        self._buf = bytearray()
        self._overflowed = False

    def push(self, data: bytes) -> None:
        if len(self._buf) + len(data) > self.MAX_STREAM_MESSAGE + 8:
            self._buf.clear()
            self._overflowed = True
        elif not self._overflowed:
            self._buf += data

    def take_messages(self) -> list[StreamMessage]:
        if self._overflowed:
            self._overflowed = False
            self._buf.clear()
            raise StreamError("stream buffer exceeded its safety limit")

        out: list[StreamMessage] = []
        pos = 0
        first_error: Exception | None = None
        while pos + 8 <= len(self._buf):
            flags = int.from_bytes(self._buf[pos : pos + 2], "little")
            service = int.from_bytes(self._buf[pos + 2 : pos + 4], "little")
            length = int.from_bytes(self._buf[pos + 4 : pos + 8], "little")
            header_end = pos + 8
            body_end = header_end + length
            if length > self.MAX_STREAM_MESSAGE:
                first_error = first_error or StreamError(f"stream message declares {length} bytes")
                pos = len(self._buf)
                break
            if len(self._buf) < body_end:
                break  # wait for more bytes
            body_bytes = bytes(self._buf[header_end:body_end])
            try:
                body = unpack_first(body_bytes)
                out.append(StreamMessage(flags=flags, service=service, body=body))
            except StreamError as exc:
                first_error = first_error or exc
            pos = body_end
        del self._buf[:pos]
        if first_error is not None and not out:
            raise first_error
        return out
