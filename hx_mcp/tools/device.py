"""Device tools: read-only identity and preset inspection.

The functions here are plain Python (no MCP dependency) so they can be unit
tested and reused by ``probe.py``. ``server.py`` wraps them as MCP tools.

Everything in this module is **read-only**. Destructive operations (parameter
writes, IR upload/delete, global writes, slot restore) live in other tool
modules and must be gated behind explicit user confirmation.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from typing import Any, Iterator, Optional

from hx_mcp import session as session_mod
from hx_mcp.protocol import opcodes as op
from hx_mcp.session import HxError, HxTimeout, Session
from hx_mcp.transport import usb


@dataclass
class DeviceInfo:
    connected: bool
    name: str = ""
    vendor_id: int = op.VENDOR_ID
    product_id: int = 0
    serial: Optional[str] = None
    #: USB device release (bcdDevice), e.g. "2.00".
    usb_version: str = ""
    #: Firmware version decoded from the loaded preset's system block (BCD).
    firmware: Optional[str] = None
    setlist: Optional[int] = None
    preset_index: Optional[int] = None
    preset_name: Optional[str] = None
    error: Optional[str] = None


def _open_with_retry(found: usb.FoundDevice) -> tuple[usb.UsbWire, Session]:
    """Open interface 0 and bring a session up, retrying exactly once.

    The device ignores a fresh session's *opening handshake* on almost exactly
    every other attempt - a deterministic alternation - so the very first
    request of a second consecutive session times out. A single try therefore
    makes repeated calls (every MCP tool call after the first) unreliable. On a
    timeout or protocol error we release the handle, re-enumerate a clean one,
    and try once more, mirroring the reference implementation. A plain USB
    error (busy, access denied) is not retried.
    """
    last: Optional[Exception] = None
    for attempt in range(2):
        wire: Optional[usb.UsbWire] = None
        try:
            wire = usb.UsbWire(found.device).open()
            return wire, Session.bring_up(wire)
        except (HxTimeout, HxError) as exc:
            last = exc
            if wire is not None:
                wire.close()
            else:
                usb.dispose(found)
            if attempt == 0:
                usb.dispose(found)
                fresh = usb.list_devices()
                if not fresh:
                    raise
                found = fresh[0]
                for extra in fresh[1:]:
                    usb.dispose(extra)
    raise last  # type: ignore[misc]


@contextmanager
def open_session(found: Optional[usb.FoundDevice] = None) -> Iterator[Session]:
    """Open and bring up a session; always release the interface afterwards.

    Pass an already-enumerated ``found`` to avoid opening a second handle to
    the same device: pyusb's ``find`` opens the device, and holding one handle
    while claiming the interface on another is what produces "Access denied"
    (both from other processes and from this one). When ``found`` is omitted we
    enumerate and own the handle ourselves.
    """
    owned = found is None
    devices: list[usb.FoundDevice] = []
    if found is None:
        devices = usb.list_devices()
        if not devices:
            raise HxError(
                "no HX Stomp (VID 0E41) found. Check the USB cable and that "
                "WinUSB is bound to MI_00 (see README: Zadig step)."
            )
        found = devices[0]
        for extra in devices[1:]:
            usb.dispose(extra)
    wire: Optional[usb.UsbWire] = None
    session: Optional[Session] = None
    try:
        wire, session = _open_with_retry(found)
        yield session
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:
                pass
        if wire is not None:
            wire.close()
        if owned:
            for d in devices:
                usb.dispose(d)


def get_device_info() -> dict:
    """Identity of the connected device. Read-only, never writes."""
    try:
        devices = usb.list_devices()
    except Exception as exc:
        return asdict(DeviceInfo(connected=False, error=str(exc)))
    try:
        if not devices:
            return asdict(
                DeviceInfo(
                    connected=False,
                    error="no HX Stomp found on the bus (is WinUSB bound to MI_00?)",
                )
            )
        found = devices[0]
        info = DeviceInfo(
            connected=True,
            name=found.name,
            product_id=found.product_id,
            serial=found.serial,
            usb_version=found.usb_version,
        )
        try:
            # Reuse the already-open handle; opening a second one while this
            # one is live is what makes the claim fail with "Access denied".
            with open_session(found) as session:
                setlist, index, name = session.preset_info()
                info.setlist = setlist
                info.preset_index = index
                info.preset_name = name
                try:
                    blob = session.read_preset_blob()
                    _, _, tone = session_mod.parse_preset(blob)
                    info.firmware = session_mod.firmware_version(tone)
                except HxError:
                    pass
        except Exception as exc:
            info.error = str(exc)
        return asdict(info)
    finally:
        for d in devices:
            usb.dispose(d)


def list_presets(setlist: int = 0) -> dict:
    """Preset names in a setlist. Read-only."""
    with open_session() as session:
        names = session.presets(setlist)
    return {"setlist": setlist, "count": len(names), "presets": names}


def list_setlists() -> dict:
    """Setlist names. Read-only."""
    with open_session() as session:
        names = session.setlists()
    return {"setlists": names}


def read_current_preset() -> dict:
    """A best-effort summary of the loaded preset (numeric; names need M2)."""
    with open_session() as session:
        setlist, index, name = session.preset_info()
        blob = session.read_preset_blob()
        _, sections, tone = session_mod.parse_preset(blob)
    return {
        "setlist": setlist,
        "index": index,
        "name": name,
        "firmware": session_mod.firmware_version(tone),
        "document_bytes": len(blob),
        "blocks": _summarise_tone(tone),
    }


def set_block_model(block: int, model: int, paired: Optional[int] = None) -> dict:
    """Change what a block is, by numeric wire model number.

    Edit-buffer only: the device sounds different immediately, but reloading the
    preset reverts it. Marks a WRITE. Model numbers are Helix.sym indices; names
    require the HX Edit catalog (M2).
    """
    with open_session() as session:
        session.set_model(block, model, paired)
    return {"ok": True, "block": block, "model": model, "paired": paired}


def assign_block_footswitch(block: int, switch: int) -> dict:
    """Put a block's bypass (on/off) on a footswitch. ``switch`` is 1-based.

    Edit-buffer only. Marks a WRITE.
    """
    if switch < 1:
        raise HxError("footswitch numbers are 1-based (1, 2, 3 ...)")
    with open_session() as session:
        session.assign_bypass_footswitch(block, switch - 1)
    return {"ok": True, "block": block, "switch": switch}


def read_footswitch(switch: int) -> dict:
    """A footswitch's configuration (read-only)."""
    with open_session() as session:
        return {"switch": switch, "config": session.read_switch(switch)}


def read_preset_at(setlist: int, index: int) -> dict:
    """Read a stored preset by slot without loading it (read-only).

    Reads flash, so it shows what is actually saved - unaffected by the edit
    buffer. Handy for checking whether a write ever reached storage.
    """
    with open_session() as session:
        blob = session.read_preset_at(setlist, index)
    if blob is None:
        return {"setlist": setlist, "index": index, "empty": True}
    blocks = None
    try:
        _, _, tone = session_mod.parse_preset(blob)
        blocks = _summarise_tone(tone)
    except HxError:
        pass
    return {"setlist": setlist, "index": index, "empty": False, "bytes": len(blob), "blocks": blocks}


def clear_block(block: int) -> dict:
    """WRITE: empty a block slot (removes whatever model is there).

    Edit-buffer only; a preset reload reverts it.
    """
    with open_session() as session:
        session.clear_block(block)
    return {"ok": True, "block": block, "cleared": True}


def unassign_block_footswitch(block: int, switch: int) -> dict:
    """WRITE: take a block's on/off off a footswitch. ``switch`` is 1-based."""
    if switch < 1:
        raise HxError("footswitch numbers are 1-based (1, 2, 3 ...)")
    with open_session() as session:
        session.unassign_bypass_footswitch(block, switch - 1)
    return {"ok": True, "block": block, "switch": switch}


def reload_current_preset() -> dict:
    """Discard any unsaved edit buffer by re-loading the current slot.

    Use after host edits you don't want to keep, or to un-stick a front panel
    that is showing a stranded edit. Read-mostly; restores stored state.
    """
    with open_session() as session:
        setlist, index, name = session.preset_info()
        ok = session.select_preset(setlist, index)
    return {"ok": ok, "setlist": setlist, "index": index, "name": name}


def save_current_preset(name: Optional[str] = None) -> dict:
    """DESTRUCTIVE: commit the edit buffer to the loaded preset's slot (flash write).

    Overwrites the slot. Without this an edit lives only in the buffer and the
    front panel can get stranded on it. Back up first.
    """
    with open_session() as session:
        setlist, index, current = session.preset_info()
        session.save_preset(setlist, index, name or current)
    return {"ok": True, "setlist": setlist, "index": index, "name": name or current}


def _summarise_tone(tone: Any) -> list[dict]:
    blocks: list[dict] = []
    if not isinstance(tone, dict):
        return blocks
    for dsp_key in (0, 1):
        dsp = tone.get(dsp_key)
        if not isinstance(dsp, dict):
            continue
        slots = dsp.get(22)
        if not isinstance(slots, list):
            continue
        for i, slot in enumerate(slots):
            if not isinstance(slot, dict):
                continue
            kind = slot.get(19)
            body = slot.get(20)
            if kind == 8:  # empty slot
                continue
            model = None
            if isinstance(body, dict):
                ref = body.get(24)
                if isinstance(ref, dict):
                    model = ref.get(25)
            values = None
            if isinstance(body, dict):
                v = body.get(11)
                if isinstance(v, dict):
                    values = v.get(4)
            blocks.append(
                {
                    "dsp": dsp_key,
                    "slot": i,
                    "kind": kind,
                    "model": model,
                    "values": values,
                }
            )
    return blocks
