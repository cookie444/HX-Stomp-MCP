"""Session/state layer: channels, sequence numbers, acknowledgements, RPC.

This is a faithful Python port of the reference ``Session`` (crmne/tonepush,
``crates/hx-usb/src/lib.rs``). The interesting code is here, and the design
notes that matter are reproduced in the docstrings because every one of them
cost somebody a wedged pedal:

* **Drain before every request.** A read buffer is only posted while a request
  is in flight, so between operations the device has nowhere to put the
  notifications it emits unasked. Once its outgoing queue is full it stops
  draining the incoming endpoint, and the next write times out with nothing
  visibly wrong. This is the #1 wedge cause; recovery needs the 9V adapter.
* **Acknowledgements are a base plus bytes consumed** (0x1000 + rx_bytes), not
  a bare count. The device paces itself by the difference.
* **A write that timed out poisons the session.** The frame may or may not have
  reached the device, so nothing further goes out.
* **Never re-run the handshake on an open channel.**

Offline-safe: the session layer depends only on the ``Wire`` protocol, so it
runs against a recorded transcript for tests.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from hx_mcp.protocol import opcodes as op
from hx_mcp.protocol.codec import (
    ChannelHeader,
    Frame,
    FrameError,
    StreamError,
    StreamReader,
    pack,
    unpack_first,
    unpack_values,
)
from hx_mcp.protocol.opcodes import ChannelId
from hx_mcp.transport.usb import TransportTimeout, Wire


class HxError(Exception):
    """A protocol or session-level failure."""


class HxTimeout(HxError):
    """The device did not answer a transaction in time."""


class HxDeviceError(HxError):
    """The device refused a request (status 255); ``code`` is the signed error."""

    def __init__(self, code: int):
        self.code = code
        super().__init__(f"the device refused: error {code}")


#: Signatures of a write that may not have reached the device.
_WRITE_TIMED_OUT = "write timed out"


class Channel:
    """Per-channel state: what we have sent, and how much we have received."""

    ACK_BASE = op.ACK_BASE

    def __init__(self) -> None:
        self.seq = 0
        #: Bytes received from the device on this channel.
        self.rx_bytes = 0
        #: How much of rx_bytes the device has been told about.
        self.acked = 0
        self.reader = StreamReader()
        self.txn = op.FIRST_TXN

    def ack(self) -> int:
        return (self.ACK_BASE + self.rx_bytes) & 0xFFFFFFFF

    def next_txn(self) -> int:
        txn = self.txn
        self.txn += 1
        return txn


# ------------------------------------------------------- message classify ---


def classify(value: Any) -> tuple[str, Any]:
    """Classify a decoded MessagePack value into a known RPC shape.

    Returns one of ``("request", (txn, opcode, args))``,
    ``("response", (txn, status, result))``,
    ``("notification", (event, args))`` or ``("other", value)``.
    """
    if isinstance(value, dict):
        txn = value.get(op.K_TXN)
        if txn is not None and op.K_OPCODE in value:
            return "request", (txn, value.get(op.K_OPCODE), value.get(op.K_ARGS))
        if txn is not None and op.K_STATUS in value:
            return "response", (txn, value.get(op.K_STATUS), value.get(op.K_RESULT))
        if value.get(op.K_EVENT) is not None:
            return "notification", (value.get(op.K_EVENT), value.get(op.K_EVENT_ARGS))
    return "other", value


def request_value(txn: int, opcode: int, args: Any) -> dict:
    """Field order matches HX Edit's own (txn first) so bytes stay comparable."""
    return {op.K_TXN: txn, op.K_OPCODE: opcode, op.K_ARGS: args}


class Session:
    """A brought-up editor session over a :class:`Wire`."""

    # Timeouts (seconds), mirroring the reference implementation.
    REPLY = 0.8            # wait for a handshake / service open
    DRAIN_READ = 0.15      # per-read timeout while clearing a backlog
    DRAIN_BUDGET = 3.0     # total budget for clearing a backlog (bounded!)
    WRITE = 2.0            # a single bulk write
    REPLY_BUDGET = 6.0     # wait for a reply before giving up
    REPLY_READ = 0.3       # per-read timeout while waiting for a reply
    BETWEEN_CHUNKS = 0.08  # pause between chunks of a large send
    CHUNK = op.CHUNK

    def __init__(self, wire: Wire) -> None:
        self.wire = wire
        self.channels: dict[int, Channel] = {}
        self.poisoned: Optional[str] = None

    # ---------------------------------------------------------- lifecycle ---

    @classmethod
    def bring_up(cls, wire: Wire) -> "Session":
        session = cls(wire)
        session.handshake()
        # A handshake can complete and the session still be deaf: prove it
        # works before handing it over.
        session.preset_info()
        return session

    def handshake(self) -> None:
        self.drain()
        for cid in op.ALL_CHANNELS:
            for n, service in enumerate(cid.services):
                if n > 0:
                    self.close_service(cid)
                self.open_service(cid, service, fresh=(n == 0))

    def close_service(self, cid: ChannelId) -> None:
        seq, ack = self.tick(cid)
        payload = bytearray()
        ChannelHeader(seq, op.MSG_HELLO, ack).encode_into(payload)
        self.write(Frame.new(cid.device, cid.host, bytes(payload)))
        self.mark_acknowledged(cid)
        self.read_available(self.REPLY)

    def open_service(self, cid: ChannelId, service: int, fresh: bool) -> None:
        existing = self.channels.get(cid.device)
        carried = 0 if fresh or existing is None else existing.rx_bytes
        self.channels[cid.device] = Channel()

        payload = bytearray()
        ChannelHeader(0, op.MSG_HELLO, op.HELLO_FIELD).encode_into(payload)
        payload += op.HELLO_TAIL
        hello = Frame.new(cid.device, cid.host, bytes(payload), flags=op.FLAG_HANDSHAKE)
        self.write(hello)
        self.read_available(self.REPLY)

        ch = self.channels[cid.device]
        self._discard_messages(ch)
        ch.rx_bytes = carried
        # HX Edit's counter jumps 0 -> 2 here; the device stops responding to 1.
        ch.seq = 2

        # The service's opening message body echoes the service number.
        self.send_stream(cid, service, service)
        self.read_available(self.REPLY)
        self._discard_messages(self.channels[cid.device])
        self.ack_channel(cid)

    def close(self) -> None:
        """Drain and acknowledge outstanding bytes before releasing the wire.

        Not a closing handshake: a session that vanishes without acknowledging
        leaves the device pacing against an ever-growing debt, and later
        short-lived sessions inherit it. Draining is enough.
        """
        if self.poisoned is not None:
            return
        give_up = time.monotonic() + 0.9
        quiet = 0
        while quiet < 3 and time.monotonic() < give_up:
            try:
                self.read_once(0.1)
                quiet = 0
                self.ack_pending(None)
            except TransportTimeout:
                quiet += 1
            except Exception:
                break
        self.ack_pending(None)

    # -------------------------------------------------------------- drain ---

    def drain(self) -> None:
        """Empty the endpoint before saying anything.

        A client that exits without closing leaves stale acknowledgements, and
        they survive process restarts, so the next session reads thousands of
        them instead of its own handshake reply. Drain until reads time out (a
        zero-length transfer counts as activity), bounded so we do not hold the
        endpoint under sustained load.
        """
        give_up = time.monotonic() + self.DRAIN_BUDGET
        quiet = 0
        while quiet < 3 and time.monotonic() < give_up:
            try:
                self.read_once(self.DRAIN_READ)
                quiet = 0
            except Exception:
                quiet += 1

    def drain_pending(self, busy: ChannelId) -> None:
        deadline = time.monotonic() + 0.06
        while time.monotonic() < deadline:
            got = self.read_available(0.005)
            if got is None:
                break
        self.ack_pending(busy.device)

    # ---------------------------------------------------------------- I/O ---

    def write(self, frame: Frame) -> None:
        if self.poisoned is not None:
            raise HxError(self.poisoned)
        data = frame.encode()
        try:
            self.wire.send(data)
        except TransportTimeout as exc:
            if str(exc) == _WRITE_TIMED_OUT:
                self.poisoned = (
                    "a write to the device timed out; the frame may or may not "
                    "have arrived. Reconnect before trying again."
                )
            raise

    def read_once(self, timeout: float) -> Optional[Frame]:
        data = self.wire.recv(timeout)
        if not data:
            return None
        frames = Frame.decode_transfer(data)
        for frame in frames:
            self.route(frame)
        return frames[0] if frames else None

    def read_available(self, timeout: float) -> Optional[Frame]:
        try:
            return self.read_once(timeout)
        except TransportTimeout as exc:
            if str(exc) == "read timed out":
                return None
            raise

    def route(self, frame: Frame) -> None:
        decoded = ChannelHeader.decode(frame.payload)
        if decoded is None:
            return
        hdr, rest = decoded
        ch = self.channels.get(frame.src)
        if ch is None:
            return
        if hdr.has_data() and rest:
            ch.rx_bytes = (ch.rx_bytes + len(rest)) & 0xFFFFFFFF
            ch.reader.push(rest)

    # ---------------------------------------------------------- accounting ---

    def tick(self, cid: ChannelId) -> tuple[int, int]:
        ch = self.channels.get(cid.device)
        if ch is None:
            raise HxError(f"channel 0x{cid.device:04x} not open")
        seq = ch.seq
        ch.seq = (ch.seq + 1) & 0xFFFF
        return seq, ch.ack()

    def mark_acknowledged(self, cid: ChannelId) -> None:
        ch = self.channels.get(cid.device)
        if ch is not None:
            ch.acked = ch.rx_bytes

    def ack_channel(self, cid: ChannelId) -> None:
        seq, ack = self.tick(cid)
        payload = bytearray()
        ChannelHeader(seq, op.MSG_ACK, ack).encode_into(payload)
        self.write(Frame.new(cid.device, cid.host, bytes(payload)))
        self.mark_acknowledged(cid)

    def ack_idle_channels(self, busy: ChannelId) -> None:
        self.ack_pending(busy.device)

    def ack_pending(self, except_device: Optional[int]) -> None:
        for cid in op.ALL_CHANNELS:
            if except_device == cid.device:
                continue
            ch = self.channels.get(cid.device)
            if ch is not None and ch.rx_bytes != ch.acked:
                self.ack_channel(cid)

    def keepalive(self) -> None:
        for cid in op.ALL_CHANNELS:
            if cid.device not in self.channels:
                continue
            seq, ack = self.tick(cid)
            payload = bytearray()
            ChannelHeader(seq, op.MSG_KEEPALIVE, ack).encode_into(payload)
            self.write(Frame.new(cid.device, cid.host, bytes(payload)))
            self.mark_acknowledged(cid)

    # --------------------------------------------------------------- send ---

    def send_stream(self, cid: ChannelId, service: int, body: Any) -> None:
        if self.poisoned is not None:
            raise HxError(self.poisoned)
        encoded = pack(body)
        stream = bytearray()
        stream += op.ORIGINATOR_HOST.to_bytes(2, "little")
        stream += service.to_bytes(2, "little")
        stream += len(encoded).to_bytes(4, "little")
        stream += encoded

        chunks = [bytes(stream[i : i + self.CHUNK]) for i in range(0, len(stream), self.CHUNK)]
        started = False
        try:
            for n, chunk in enumerate(chunks):
                seq, ack = self.tick(cid)
                payload = bytearray()
                ChannelHeader(seq, op.MSG_DATA, ack).encode_into(payload)
                payload += chunk
                self.write(Frame.new(cid.device, cid.host, bytes(payload)))
                started = True
                self.mark_acknowledged(cid)
                if n + 1 < len(chunks):
                    # Read between chunks: the device paces us with
                    # acknowledgements, and writing a whole document blind
                    # fills its receive window and stalls the endpoint.
                    self.read_available(self.BETWEEN_CHUNKS)
        except Exception as exc:
            if started:
                self.poisoned = (
                    f"a transfer failed part-way through ({exc}); the device "
                    "needs its 9V adapter pulled before it will accept a new session"
                )
            raise

    # ---------------------------------------------------------- requests ---

    def request(self, cid: ChannelId, opcode: int, args: Any = None) -> Any:
        _, _, result = self.request_raw(cid, opcode, args)
        return result

    def request_full(self, cid: ChannelId, opcode: int, args: Any = None) -> tuple[int, Any]:
        _, status, result = self.request_raw(cid, opcode, args)
        return status, result

    def request_raw(self, cid: ChannelId, opcode: int, args: Any = None) -> tuple[int, int, Any]:
        ch = self.channels.get(cid.device)
        if ch is None:
            raise HxError(f"channel 0x{cid.device:04x} not open")
        txn = ch.next_txn()

        # Take anything the device has been holding before adding to it.
        self.drain_pending(cid)

        self.send_stream(cid, cid.service, request_value(txn, opcode, args))

        deadline = time.monotonic() + self.REPLY_BUDGET
        while time.monotonic() < deadline:
            before = ch.rx_bytes
            self.read_available(self.REPLY_READ)
            got = ch.rx_bytes != before

            ready = self._take_messages(ch)
            for sm in ready:
                kind, payload = classify(sm.body)
                if kind == "response":
                    t, status, result = payload
                    if t == txn:
                        if status == op.STATUS_REFUSED:
                            code = None
                            if isinstance(result, dict):
                                code = result.get(op.K_ERROR_CODE)
                            raise HxDeviceError(code if isinstance(code, int) else 0)
                        return txn, status, result
            if got:
                self.ack_channel(cid)
            self.ack_idle_channels(cid)
        raise HxTimeout(f"timed out waiting for a reply to transaction {txn}")

    def _take_messages(self, ch: Channel) -> list:
        try:
            return ch.reader.take_messages()
        except StreamError as exc:
            raise HxError(f"stream error: {exc}") from exc

    def _discard_messages(self, ch: Channel) -> None:
        try:
            ch.reader.take_messages()
        except StreamError:
            pass

    # -------------------------------------------------------- convenience ---

    def preset_info(self) -> tuple[int, int, str]:
        """Setlist, zero-based index and name of the loaded preset."""
        v = self.request(op.DATA, op.OP_PRESET_INFO, None)
        if not isinstance(v, dict):
            raise HxError("preset info was not a map")
        setlist = v.get(op.K_SETLIST)
        index = v.get(op.K_PRESET_INDEX)
        name = v.get(op.K_NAME)
        if not isinstance(setlist, int) or not isinstance(index, int) or not isinstance(name, str):
            raise HxError("preset info is missing setlist/index/name")
        return setlist, index, name

    def read_preset_blob(self) -> bytes:
        """The loaded preset as raw l6-helix document bytes.

        The device returns this as a MessagePack str; the codec keeps it as
        ``bytes`` because its contents are binary. A str is tolerated and
        re-encoded so a valid-UTF-8 document still round-trips.
        """
        v = self.request(op.DATA, op.OP_READ_PRESET, None)
        if isinstance(v, str):
            v = v.encode("utf-8")
        if not isinstance(v, (bytes, bytearray)):
            raise HxError("preset response was not a blob")
        return bytes(v)

    def setlists(self) -> list[str]:
        result = self.request(op.CONTROL, op.OP_LIST_SETLISTS, None)
        if not isinstance(result, list):
            raise HxError("setlist list was not an array")
        out: list[str] = []
        for entry in result:
            if isinstance(entry, dict) and entry:
                first = next(iter(entry.values()))
                out.append(str(first))
            elif isinstance(entry, str):
                out.append(entry)
            else:
                raise HxError("setlist entry has no readable name")
        return out

    def presets(self, setlist: int = 0) -> list[str]:
        result = self.request(
            op.CONTROL, op.OP_LIST_PRESETS, {op.K_SETLIST: setlist, op.K_ARGS: 2}
        )
        if not isinstance(result, list):
            raise HxError("preset list was not an array")
        out: list[str] = []
        for entry in result:
            if isinstance(entry, dict) and entry:
                details = next(iter(entry.values()))
                name = details.get(op.K_NAME) if isinstance(details, dict) else None
                out.append(str(name))
            else:
                raise HxError("preset list entry has no readable name")
        return out

    # ------------------------------------------------------------- writes ---

    def select_block(self, block: int) -> None:
        """Move the editing cursor to a block (the device's screen follows)."""
        self.request(op.DATA, op.OP_SELECT_BLOCK, {op.K_BLOCK: block, op.K_PATH: 0})

    def set_model(self, block: int, model: int, paired: Optional[int] = None) -> None:
        """Change what a block is. Must be preceded by a select (done here)."""
        self.select_block(block)
        self.request(
            op.DATA,
            op.OP_SET_MODEL,
            {
                op.K_BLOCK: block,
                op.K_MODEL_REF: {
                    op.K_PAIRED: paired is not None,
                    op.K_MODEL: model,
                    op.K_PAIRED_MODEL: paired if paired is not None else -1,
                },
            },
        )

    def set_enabled(self, block: int, enabled: bool) -> None:
        """Switch a block in (True) or out (False) of the signal path."""
        self.request(op.DATA, op.OP_SET_ENABLED, {op.K_BLOCK: block, op.K_ENABLED: enabled})

    def assign_bypass_footswitch(self, block: int, switch0: int) -> None:
        """Put a block's bypass on a footswitch. ``switch0`` is zero-based."""
        self.request(op.DATA, op.OP_ASSIGN_FOOTSWITCH, {op.K_BLOCK: block, op.K_SWITCH: switch0})

    def read_switch(self, switch1: int) -> Any:
        """A footswitch's configuration (one-based in, zero-based out)."""
        return self.request(op.DATA, op.OP_FOOTSWITCH_CONFIG, {op.K_SWITCH: switch1})

    def poll_notifications(self) -> list[tuple[int, Any]]:
        self.read_available(0.02)
        out: list[tuple[int, Any]] = []
        for ch in self.channels.values():
            for sm in self._take_messages(ch):
                kind, payload = classify(sm.body)
                if kind == "notification":
                    out.append(payload)
        return out


# ----------------------------------------------------------- preset doc ---


def parse_preset(blob: bytes) -> tuple[Any, Any, Any]:
    """Decode an l6-helix preset document into (magic, section_table, tone_map).

    The blob is itself MessagePack holding three top-level values. The section
    table is a little-endian u32 offset table into the document; a byte-exact
    encoder must preserve it (M1+).
    """
    try:
        magic, sections, tone = unpack_values(blob, 3)
    except StreamError as exc:
        raise HxError(f"preset blob was not an l6-helix document: {exc}") from exc
    if magic != op.PRESET_MAGIC:
        raise HxError(f"preset magic was {magic!r}, expected {op.PRESET_MAGIC!r}")
    return magic, sections, tone


def firmware_version(tone: Any) -> Optional[str]:
    """Firmware version from a preset's tone map (key 7 -> 35), decoded as BCD.

    The wire value is e.g. 0x03800000; byte 0x80 read as two BCD nibbles is 80,
    giving "3.80". Over MIDI the same field is plain decimal (0x50 == 80).
    """
    if not isinstance(tone, dict):
        return None
    system = tone.get(7)
    if not isinstance(system, dict):
        return None
    raw = system.get(35)
    if not isinstance(raw, int) or raw <= 0:
        return None
    b = raw.to_bytes(4, "big")
    major = b[0]
    minor = (b[1] >> 4) * 10 + (b[1] & 0x0F)
    return f"{major}.{minor:02d}"
