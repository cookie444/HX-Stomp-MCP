"""Offline codec tests against pinned byte fixtures (no hardware needed)."""

from __future__ import annotations

import pytest

from hx_mcp.protocol import opcodes as op
from hx_mcp.protocol.codec import (
    ChannelHeader,
    F32,
    Frame,
    FrameError,
    StreamReader,
    pack,
    unpack_first,
)
from hx_mcp.session import classify, firmware_version, request_value


# ----------------------------------------------------------------- frames ---


def test_decodes_the_opening_frame(hex_fixture):
    raw = hex_fixture("hello_frame.hex")
    f = Frame.decode(raw)
    assert f.flags == op.FLAG_HANDSHAKE
    assert f.dst == 0x1001
    assert f.src == 0x03EF
    assert len(f.payload) == 12
    assert f.encode() == raw


def test_padded_frame_round_trips_including_padding(hex_fixture):
    # 17-byte payload, 28 bytes on the wire: a naive encoder corrupts it.
    raw = hex_fixture("padded_frame.hex")
    f = Frame.decode(raw)
    assert f.wire_len == 28
    assert f.encode() == raw

    hdr, rest = ChannelHeader.decode(f.payload)
    assert hdr.seq == 2
    assert hdr.msg_type == op.MSG_DATA
    assert hdr.ack == 0x1000
    assert len(rest) == 9


def test_decode_transfer_walks_coalesced_frames(hex_fixture):
    hello = hex_fixture("hello_frame.hex")
    padded = hex_fixture("padded_frame.hex")
    frames = Frame.decode_transfer(hello + padded)
    assert [f.encode() for f in frames] == [hello, padded]


def test_trailing_bytes_are_rejected_by_exact_decode(hex_fixture):
    raw = hex_fixture("hello_frame.hex") + b"\x00\x00\x00\x00"
    with pytest.raises(FrameError):
        Frame.decode(raw)


def test_data_is_a_flag_not_a_value():
    for t in (0x04, 0x0C, 0x14):
        assert ChannelHeader(0, t, 0).has_data()
    for t in (op.MSG_ACK, op.MSG_HELLO, op.MSG_KEEPALIVE):
        assert not ChannelHeader(0, t, 0).has_data()


# ----------------------------------------------------------- messagepack ---


def test_request_round_trips_byte_for_byte(hex_fixture):
    # HX Edit's own field order: transaction id first.
    expected = hex_fixture("select_preset.hex")
    args = {op.K_SETLIST: 0, op.K_PRESET_INDEX: 12}
    assert pack(request_value(1009, op.OP_SELECT_PRESET, args)) == expected


def test_classify_recognises_a_request(hex_fixture):
    value = unpack_first(hex_fixture("select_preset.hex"))
    kind, payload = classify(value)
    assert kind == "request"
    txn, opcode, args = payload
    assert (txn, opcode) == (1009, op.OP_SELECT_PRESET)
    assert args[op.K_PRESET_INDEX] == 12


def test_floats_go_out_as_float32():
    # A bare Python float must be 0xca (float32), not 0xcb (float64).
    assert pack(1.0)[0] == 0xCA
    assert pack(F32(0.5))[0] == 0xCA
    assert len(pack(F32(0.5))) == 5


def test_trailing_nuls_are_stripped_and_tail_ignored():
    body = pack({op.K_NAME: "l6-helix\x00"})
    value = unpack_first(body + b"\x00\x00\x00")  # opaque tail
    assert value[op.K_NAME] == "l6-helix"


# ----------------------------------------------------------- stream layer ---


def _framed(service: int, body: bytes) -> bytes:
    return (
        (0).to_bytes(2, "little")
        + service.to_bytes(2, "little")
        + len(body).to_bytes(4, "little")
        + body
    )


def test_stream_reader_waits_for_a_whole_message():
    body = pack({op.K_TXN: 1000})
    framed = _framed(6, body)
    reader = StreamReader()

    reader.push(framed[:6])
    assert reader.take_messages() == []

    reader.push(framed[6:])
    messages = reader.take_messages()
    assert len(messages) == 1
    assert messages[0].service == 6
    assert messages[0].body[op.K_TXN] == 1000


# ------------------------------------------------------------ preset doc ---


@pytest.mark.parametrize(
    "raw,expected",
    [
        (0x03800000, "3.80"),
        (0x03790000, "3.79"),
        (0x02990000, "2.99"),
        (0x03690000, "3.69"),
    ],
)
def test_firmware_version_is_bcd(raw, expected):
    assert firmware_version({7: {35: raw}}) == expected


def test_firmware_version_missing_returns_none():
    assert firmware_version({}) is None
    assert firmware_version({7: {}}) is None
    assert firmware_version("not a map") is None
