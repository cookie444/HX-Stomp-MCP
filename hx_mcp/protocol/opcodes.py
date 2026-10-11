"""Opcode, key, channel and framing constants for the HX vendor protocol.

These are **protocol facts** reconstructed by public reverse engineering
(crmne/tonepush ``PROTOCOL.md`` and the ``hx-proto`` / ``hx-usb`` crates), not
Line 6 source or data. Model/parameter *names and ranges* are deliberately NOT
here - those are Line 6 proprietary data and are imported at runtime from the
user's own HX Edit install (see :mod:`hx_mcp.catalog`).

Confidence markers from the source notes are preserved in comments:
[confirmed] observed on the wire, [inferred]/[open] hypothesis or unresolved.
"""

# ------------------------------------------------------------------ device ---

VENDOR_ID = 0x0E41

#: Known product ids. 0x4246 is this project's target (HX Stomp).
PRODUCT_IDS = {
    0x4246: "HX Stomp",
    0x4253: "HX Stomp XL",
    0x4248: "Helix Floor",
    0x4245: "HX Effects",
}

DEFAULT_PRODUCT_ID = 0x4246

#: The vendor control interface (USB Class 0xFF/00). Services ride here.
INTERFACE = 0
#: Bulk OUT / IN endpoints on interface 0 (512-byte max packet).
EP_OUT = 0x01
EP_IN = 0x81
EP_MAX_PACKET = 512

# --------------------------------------------------------- USB frame flags ---

#: Normal traffic (byte 3 of the 8-byte frame header).
FLAG_DATA = 0x18
#: Channel handshake, sent when a channel is first opened.
FLAG_HANDSHAKE = 0x28

# ------------------------------------------------- channel message types ---

#: Bare acknowledgement, no data.
MSG_ACK = 0x08
#: Flag set on any message carrying stream data. It is a *flag*: data arrives
#: as 0x04 alone, 0x0c piggybacking an ack, 0x14 alongside a keep-alive.
MSG_DATA = 0x04
#: Channel handshake.
MSG_HELLO = 0x02
#: Periodic keep-alive, roughly once a second per channel.
MSG_KEEPALIVE = 0x10

# ------------------------------------------------------- transport tuning ---

#: The ack field is a base plus the bytes consumed, NOT a bare byte count.
#: Sending the count alone makes the device stop responding.
ACK_BASE = 0x1000
#: Bytes of stream data per frame. The device chunks its own large transfers at
#: 256 and refuses a single oversized frame.
CHUNK = 256
#: Transaction ids start here per channel and count up.
FIRST_TXN = 1000

#: Stream-prefix originator tag: 1 host -> device, 0 device -> host.
ORIGINATOR_HOST = 1
ORIGINATOR_DEVICE = 0

#: MessagePack body declared here on a handshake; meaning undecoded.
HELLO_FIELD = 0x21000100
#: Trailing 4 bytes of the handshake payload, replayed verbatim from HX Edit.
HELLO_TAIL = bytes([0x00, 0x10, 0x00, 0x00])

# --------------------------------------------------------------- keys ---
# The protocol uses bare integer keys; naming them is the difference between
# readable code and a wall of magic numbers.

K_OPCODE = 100
K_ARGS = 101
K_TXN = 102
K_STATUS = 103
K_RESULT = 104
K_ERROR_CODE = 111
K_IN_EFFECT = 63
K_EVENT = 105
K_EVENT_ARGS = 106

K_SETLIST = 107
K_PRESET_INDEX = 108
K_NAME = 109
K_OBJECT_ID = 118
K_FAVOURITE_FLAG = 31
K_VALUE = 119
K_BLOCK = 98
K_PARAM_INDEX = 28
K_PATH = 26
K_COMMIT = 29
K_ENABLED = 59
K_MODEL = 25
K_MODEL_REF = 100
K_PAIRED = 23
K_PAIRED_MODEL = 26
K_DOCUMENT = 110
K_SNAPSHOT = 92

K_ROUTING = 51
K_CC = 71
K_ASSIGN_TARGET = 95
K_ASSIGN_SCOPE = 96
K_ASSIGN_FLAGS = 74
K_ASSIGN_SOURCE = 0
K_MOMENTARY = 65
K_LED_COLOUR = 66
K_SWITCH_ASSIGNED = 67
K_SWITCH_TARGET = 69
K_SWITCH = 102
K_ASSIGN_MIN = 72
K_ASSIGN_MAX = 73
K_ASSIGN_KIND = 71
K_ASSIGN_EXTRA = 129

K_IR_SLOT = 112
K_IR_CHECKSUM = 113
K_IR_SAMPLES = 110
K_IR_FORMAT_A = 114
K_IR_FORMAT_B = 115

# --------------------------------------------------------------- opcodes ---

OP_LIST_PRESETS = 1
OP_SELECT_PRESET = 20
OP_READ_PRESET = 22          # read the loaded preset as an l6-helix document
OP_PRESET_INFO = 23          # metadata: bank, slot, name
OP_FETCH_OBJECT = 24         # {118: id}
OP_SET_OBJECT = 25           # {118: id, 119: value}
OP_SET_PARAM = 30
OP_SET_ENABLED = 41
OP_SET_ROUTING = 42
OP_TEMPO_IS_EXTERNAL = 99
OP_GLOBAL_EQ = 76
OP_RENAME_PRESET = 6
OP_SAVE_PRESET = 71          # commit edit buffer to a slot (File > Save Preset)
OP_LIST_SETLISTS = 0
OP_LIST_IRS = 13
OP_BEGIN = 255
OP_END = 254
OP_READY = 112
OP_WRITE_PRESET = 21
OP_ASSIGN_CONTROLLER = 37
OP_READ_ASSIGNMENT = 36
OP_SET_ASSIGN_CC = 64
OP_ASSIGN_MIN_OP = 65
OP_ASSIGN_MAX_OP = 66
OP_ASSIGN_FOOTSWITCH = 56
OP_UNASSIGN_FOOTSWITCH = 57
OP_FOOTSWITCH_CONFIG = 33
OP_SWITCH_TYPE = 58
OP_SWITCH_LABEL = 59
OP_SWITCH_LABEL_CLEAR = 60
OP_SWITCH_COLOUR = 61
OP_SWITCH_COLOUR_AUTO = 62
OP_CLEAR_BLOCK = 28
OP_UPLOAD_IR = 9
OP_CLEAR_IR = 15
OP_SET_MODEL = 40
OP_SELECT_BLOCK = 78
OP_SELECT_SNAPSHOT = 88

# Addressed slots (fast path: name the slot, never load it).
OP_FETCH_PRESET = 4          # {107, 108, 101: 2}
OP_WRITE_SLOT = 5
OP_WRITE_SLOT_NAMED = 8
OP_CLEAR_SLOT = 16

# The object store (backup walks 803 ids this way).
OP_FETCH_BLOB = 109
OP_WRITE_BLOB = 111

OP_WRITE_GLOBALS = 86
OP_GLOBAL_EQ_RESET = 77

OP_IR_DESCRIPTOR = 12
OP_IR_SAMPLES = 11
OP_RENAME_IR = 10

OP_LIST_FAVOURITES = 112
OP_READ_AS_FAVOURITE = 45
OP_FETCH_FAVOURITE = 113
OP_SAVE_FAVOURITE = 119
OP_WRITE_FAVOURITE = 114
OP_CLEAR_FAVOURITE = 116
OP_RENAME_FAVOURITE = 117

#: Reply statuses (key 103).
STATUS_DONE = 0
STATUS_ACCEPTED = 1
STATUS_REFUSED = 255

#: The preset document magic (an l6-helix payload is itself MessagePack).
PRESET_MAGIC = "l6-helix"


class ChannelId:
    """A bidirectional channel, identified by its device-side and host-side nodes.

    ``services`` is the list of services opened on the channel in order; the
    last one is what requests ride on. The control channel is opened twice -
    once for service 5, then from scratch for service 2. [confirmed]
    """

    __slots__ = ("device", "host", "services")

    def __init__(self, device: int, host: int, services: tuple[int, ...]):
        self.device = device
        self.host = host
        self.services = services

    @property
    def service(self) -> int:
        return self.services[-1] if self.services else 0

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"ChannelId(device=0x{self.device:04x}, host=0x{self.host:04x})"


#: Device/session control.
CONTROL = ChannelId(0x1001, 0x03EF, (5, 2))
#: UI and state notifications.
EVENTS = ChannelId(0x1002, 0x03F0, (4,))
#: Preset and global data.
DATA = ChannelId(0x1080, 0x03ED, (6,))

ALL_CHANNELS: tuple[ChannelId, ...] = (CONTROL, EVENTS, DATA)
