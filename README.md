# HX Stomp MCP server

An [MCP](https://modelcontextprotocol.io/) server that controls a **Line 6 HX
Stomp** over its USB vendor protocol - the same on-wire protocol HX Edit uses -
so an AI assistant can read and edit presets, blocks, parameters, snapshots,
IRs, globals and backups.

> **No firmware. Ever.** This project never sends firmware, flash or DFU
> messages. Firmware update is the one operation that can brick an HX Stomp, and
> it is permanently out of scope. See [Safety](#safety).

---

## Status

| Milestone | State |
|---|---|
| **M0 Handshake** - open MI_00, read identity/version | **PASSED** on hardware: USB release 2.00, firmware 3.80 |
| M1 read/edit buffer, block/param enumeration | not started |
| M2 model catalog (names/ranges from HX Edit) | not started |
| M3 live edit | partial: set block model, enable/disable (edit buffer) |
| M4 save/load, setlists, favourites | not started |
| M5 snapshots + controller assigns | partial: bypass assign to a footswitch |
| M6 IR manager (destructive, guarded) | not started |
| M7 globals | not started |
| M8 backup/restore (.hxb/.hlx) | container header only |
| M9 packaging/docs | offline tests + MCP server scaffold done |

The codec, transport, session engine, an **offline test suite** and a working
M0 probe are in place. **M0 has passed against a real HX Stomp** (USB release
2.00, firmware 3.80, preset read live over MI_00). The only prerequisite was
binding WinUSB to the vendor control interface, described next.

---

## Quick start

```powershell
# 1. Create the venv and install dependencies (Python 3.11+)
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt

# 2. Run the offline test suite (no hardware needed)
.\.venv\Scripts\python.exe -m pytest

# 3. Bind WinUSB to MI_00 (see "The driver step" below), then:
.\.venv\Scripts\python.exe probe.py
```

Expected probe output once the driver is bound:

```
[1/4] Enumerating USB (VID 0x0E41)...
  - HX Stomp  VID=0x0E41 PID=0x4246 serial=... usb=2.00
[2/4] Opening interface 0 (EP 0x01 OUT / 0x81 IN)...
[3/4] Handshake (3 channels, draining notifications)...
  current preset: setlist 0, index 7 (CT-Sad)
[4/4] Version
  USB device release : 2.00
  Firmware (from doc): 3.80

M0 PASSED - interface 0 is ours and the device answers.
```

---

## The driver step (Windows) - required before M0 can pass

The HX Stomp exposes several USB interfaces. We want **only** this one:

| Interface | Class | Role | Action |
|---|---|---|---|
| **MI_00** | `0xFF` vendor-specific | **editor channel** | **bind WinUSB to this** |
| MI_01 | USB Audio 2.0 | audio | **leave alone** |
| MI_05 | HID | keyboard/switches | **leave alone** |

Out of the box on this machine, MI_00 shows **Device Manager Problem Code 28**
("The drivers for this device are not installed"): its compatible IDs are only
`USB\COMPAT_VID_0e41&Class_ff...`, with no `WINUSB` ID, so Windows will not
auto-install a driver and neither will libusb.

### Bind WinUSB with Zadig

1. Download Zadig from <https://zadig.akeo.ie> (a single `.exe`, no installer).
2. Plug in the HX Stomp. Right-click **Zadig -> Run as administrator**.
3. In **Options**, tick **List All Devices**.
4. In the device dropdown, select the entry with **VID `0E41` / PID `4246` /
   MI_00** (it may read `HX Stomp`, `USB Composite Device`, or similar - match
   the VID/PID/interface, not the name).
5. Set the target driver (the box on the right) to **WinUSB**.
6. Click **Install Driver** (or **Replace Driver**).
7. Leave MI_01 and MI_05 untouched. If you accidentally target them, select the
   `MI_01`/`MI_05` entry and choose *Uninstall Driver* to restore Windows' own.

### Is this safe? Can it brick the device?

**No.** Zadig installs a *host-side* Windows driver and associates it with one
interface's device node. It never talks to firmware and never writes the
device's flash. The only thing that bricks HX gear is firmware/DFU, which this
project does not implement. The change is per-interface and **reversible**:

> Device Manager -> the device -> **Uninstall device** -> tick **Delete the
> driver software** -> **Scan for hardware changes**. MI_00 returns to exactly
> the state it is in today (Code 28, no driver).

The one trade-off: with WinUSB on MI_00, **HX Edit can no longer talk to the
pedal**. You have no Line 6 driver installed anyway, so nothing is lost now.
Later, for the model catalog (M2), you can install HX Edit purely to read its
data files - that needs no exclusive device access.

`libusb-package` (installed as a dependency) supplies `libusb-1.0.dll`, so pyusb
has a backend on Windows with no separate libusb install.

---

## Using it as an MCP server

`server.py` speaks MCP over stdio. Copy
[`opencode.example.jsonc`](opencode.example.jsonc) to `opencode.jsonc` and put
real absolute paths in it (the real `opencode.jsonc` is git-ignored so local
paths are never published):

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "servers": {
      "hx-stomp": {
        "type": "local",
        // Windows:   [".venv\\Scripts\\python.exe", "server.py"]
        // Linux/mac: [".venv/bin/python", "server.py"]
        "command": ["/ABSOLUTE/PATH/TO/python", "/ABSOLUTE/PATH/TO/server.py"],
        "cwd": "/ABSOLUTE/PATH/TO/hx-stomp-mcp"
      }
    }
  }
}
```

Currently exposed:

**Read-only**

| Tool | What it does |
|---|---|
| `get_device_info` | VID/PID, serial, USB release, firmware, loaded preset |
| `list_setlists` | setlist names |
| `list_presets` | preset names in a setlist |
| `read_current_preset` | name, firmware, numeric block list |
| `read_footswitch` | a footswitch's config (what it controls, colour) |
| `parse_hlx` | parse an `.hlx` file **offline**, no hardware |

**Write (edit buffer only; a preset reload reverts them)**

| Tool | What it does |
|---|---|
| `set_block_model` | change what a block is, by numeric model number |
| `assign_block_footswitch` | put a block's on/off (bypass) on a footswitch |

Saving to a slot, IR upload/delete, global writes and slot restore land in
M4-M8 and must require explicit user confirmation.

---

## Safety

Baked into the design, and called out here because each one cost somebody a
wedged pedal:

1. **Never send firmware/DFU messages.** Out of scope, forever.
2. **Always drain unsolicited device notifications.** A read buffer is only
   posted while a request is in flight, so between operations the device has
   nowhere to put the notifications it emits unasked. Once its outgoing queue
   fills it stops draining the *incoming* endpoint, and the next write simply
   times out with nothing visibly wrong. This is the #1 wedge cause. The session
   drains before every request and acknowledges idle channels (ack = `0x1000` +
   bytes consumed, not a bare count).
3. **Never issue a USB reset.** `libusb_reset_device` takes the HX Stomp off the
   bus and it does **not** re-enumerate; recovering needs a physical replug.
   `clear_halt` is used instead.
4. **A write that timed out poisons the session.** The frame may or may not have
   reached the device, so nothing further is sent until you reconnect.
5. **Back up before writes.** Some operations have no undo (IR ops, global
   writes, restoring a preset over a slot); others only affect the edit buffer
   (a reload reverts them).
6. **One holder of interface 0 at a time.** HX Edit, another editor, or this
   server may hold it - never two at once. A process that exits without
   releasing the interface leaves it claimed, and everyone else (including HX
   Edit, and this server's own next run) gets "Access denied" until that
   process ends. The server disposes its USB handle on every call and retries
   the device's alternating opening handshake once, so repeated MCP calls are
   safe.

### Recovery (read this before you need it)

The unit can wedge hard enough that a **USB replug is not enough**: it is
externally powered and keeps its session across re-enumeration. The fix is to
**pull the 9V power adapter** for a few seconds, then reconnect. Symptoms: it
took several operations happily, then refused; or it echoes stale
acknowledgements instead of answering a handshake.

---

## Architecture

```
MCP client (OpenCode / Claude / Cursor)
  └─ stdio ─ server.py (MCP tools)
      ├─ hx_mcp/tools/        tool handlers (device.py, ...)
      ├─ hx_mcp/session.py    channels, sequence/ack, RPC, edit buffer state
      ├─ hx_mcp/protocol/     codec.py (framing, msgpack), opcodes.py
      ├─ hx_mcp/transport/    usb.py (pyusb -> interface 0, drain loop)
      ├─ hx_mcp/catalog.py    HX Edit data import (index -> name/range)
      └─ hx_mcp/files/        .hlx / .hxb / .hls parsers
```

The wire stacks three layers inside each 512-byte bulk transfer on interface 0:

```
USB frame      u24 len | flags | u16 dst | u16 src | payload (pad to 4)
Channel hdr    u16 seq (BE) | u16 msgtype (BE) | u32 ack (LE)
Stream prefix  u16 originator | u16 service | u32 len | MessagePack body
```

The transport is abstracted behind a `Wire` protocol so a `Session` can run
against a recorded transcript instead of hardware - which is how the codec/session
layer is regression-tested offline, and how Linux/macOS support can be added
without touching the session logic.

Repo layout:

```
hx_mcp/transport/usb.py     enumerate, open, bulk r/w, backend selection
hx_mcp/protocol/codec.py    frames, channel headers, msgpack, stream reader
hx_mcp/protocol/opcodes.py  opcodes, keys, channels, framing constants
hx_mcp/session.py           device state + RPC engine
hx_mcp/catalog.py           HX Edit data import (M2)
hx_mcp/files/               .hlx / .hxb / .hls parsers/writers
hx_mcp/tools/               MCP tool definitions/handlers
server.py                   MCP stdio entrypoint
probe.py                    M0 handshake probe
tests/fixtures/             captured byte fixtures for offline tests
```

---

## Model data and licensing

Model/parameter **names and ranges are not on the device**. They come from HX
Edit's data files (`HelixModelDefs.bin`, `HX_ModelCatalog.json`, `Helix.sym`,
`*.models`), which are **Line 6 proprietary data**. This project does not bundle
or redistribute them; `hx_mcp/catalog.py` reads them from *your own* HX Edit
install at runtime and degrades gracefully (numeric indices still work) when HX
Edit is absent.

`.hlx` preset files are plain JSON and can be parsed/generated offline.

---

## Background and references

Protocol reconstructed from public reverse engineering - study these, do not
copy blindly:

- [crmne/tonepush](https://github.com/crmne/tonepush) (Rust) - `PROTOCOL.md`,
  `docs/_reference/opcodes.md`, `model-catalog.md`, MessagePack RPC, opcode
  109 read/restore, wedge post-mortem.
- [john-baxter-dev/fretwire](https://github.com/john-baxter-dev/fretwire) -
  protocol/safety notes.
- [allansomensi/openhx](https://github.com/allansomensi/openhx),
  [BenjaminDR/openhx](https://github.com/BenjaminDR/openhx) - CLI+GUI, docs.
- [frankdeath/hx-tools](https://github.com/frankdeath/hx-tools) - `.hxb`/`.hls`.
- [AntonyCorbett/HelixBackupFiles](https://github.com/AntonyCorbett/HelixBackupFiles)
  - `.hxb` (`AF6L`) format.
- [kempline/helix_usb](https://github.com/kempline/helix_usb) - early USB RE.
