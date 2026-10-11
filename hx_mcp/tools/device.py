"""Device tools: read-only identity and preset inspection.

The functions here are plain Python (no MCP dependency) so they can be unit
tested and reused by ``probe.py``. ``server.py`` wraps them as MCP tools.

Everything in this module is **read-only**. Destructive operations (parameter
writes, IR upload/delete, global writes, slot restore) live in other tool
modules and must be gated behind explicit user confirmation.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from typing import Any, Iterator, Optional

from hx_mcp import session as session_mod
from hx_mcp.catalog import default_catalog
from hx_mcp.protocol import opcodes as op
from hx_mcp.session import HxError, HxTimeout, Session
from hx_mcp.session_manager import manager
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


@contextmanager
def open_session(found: Optional[usb.FoundDevice] = None) -> Iterator[Session]:
    """Yield the process-wide persistent session.

    The session stays open between tool calls (see :mod:`hx_mcp.session_manager`),
    so the device is drained and kept alive continuously - an unsaved edit can sit
    in the buffer safely and the panel can reload/discard it as usual. All wire
    access is serialised by the manager's lock, so the keepalive thread and tool
    calls never touch the endpoints at once.
    """
    with manager.session() as session:
        yield session


def get_device_info() -> dict:
    """Identity of the connected device. Read-only, never writes."""
    found = manager.device_info()
    if found is None:
        reason = manager._last_error
        return asdict(
            DeviceInfo(
                connected=False,
                error=str(reason)
                if reason
                else "no HX Stomp found on the bus (is WinUSB bound to MI_00?)",
            )
        )
    info = DeviceInfo(
        connected=True,
        name=found.name,
        product_id=found.product_id,
        serial=found.serial,
        usb_version=found.usb_version,
    )
    try:
        with open_session() as session:
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


def _commit(session: Session) -> None:
    """Commit the edit buffer to the loaded preset's slot (flash write).

    Overwrites the stored preset. Only for an explicit ``save=True``.
    """
    setlist, index, name = session.preset_info()
    session.save_preset(setlist, index, name)


def _discard(session: Session) -> None:
    """Re-load the current preset, throwing away the edit buffer.

    Called after a non-saving write so the device is NEVER left on a stranded
    edit - which breaks on-device editing and can wedge the unit (needing a 9V
    pull). A write tool therefore always ends with either a commit or this.
    """
    setlist, index, _ = session.preset_info()
    session.select_preset(setlist, index)


def set_block_model(
    block: int, model: int, paired: Optional[int] = None, save: bool = False
) -> dict:
    """Change what a block is, by numeric wire model number (WRITE).

    ``save=True`` commits it to the preset (permanent). Otherwise the change
    lives in the edit buffer only: the server's persistent session keeps the
    device drained meanwhile, and reloading/discarding (or scrolling the panel)
    reverts it. Model numbers are Helix.sym indices; names need the HX Edit
    catalog.
    """
    with open_session() as session:
        session.set_model(block, model, paired)
        if save:
            _commit(session)
    return {"ok": True, "block": block, "model": model, "paired": paired, "saved": save}


def assign_block_footswitch(block: int, switch: int, save: bool = False) -> dict:
    """Put a block's bypass (on/off) on a footswitch. ``switch`` is 1-based (WRITE).

    Committed when ``save=True``; otherwise edit-buffer only.
    """
    if switch < 1:
        raise HxError("footswitch numbers are 1-based (1, 2, 3 ...)")
    with open_session() as session:
        session.assign_bypass_footswitch(block, switch - 1)
        if save:
            _commit(session)
    return {"ok": True, "block": block, "switch": switch, "saved": save}


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


def backup_presets(directory: str = "backups") -> dict:
    """Read-only backup of every preset to a timestamped JSON file.

    Walks each slot with opcode 4 (read without loading). No writes.
    """
    from datetime import datetime

    from hx_mcp import backup as backup_mod

    with open_session() as session:
        setlist, index, name = session.preset_info()
        data = backup_mod.capture(session)
    data["device"] = {"setlist": setlist, "index": index, "name": name}
    path = os.path.join(directory, f"hx_stomp_{datetime.now():%Y%m%d_%H%M%S}.json")
    backup_mod.save(data, path)
    nonempty = sum(1 for p in data["presets"] if p.get("document_b64"))
    errors = sum(1 for p in data["presets"] if p.get("error"))
    return {
        "ok": True,
        "path": path,
        "count": len(data["presets"]),
        "non_empty": nonempty,
        "errors": errors,
    }


def clear_block(block: int, save: bool = False) -> dict:
    """WRITE: empty a block slot (removes whatever model is there).

    Committed when ``save=True``; otherwise edit-buffer only.
    """
    with open_session() as session:
        session.clear_block(block)
        if save:
            _commit(session)
    return {"ok": True, "block": block, "cleared": True, "saved": save}


def unassign_block_footswitch(block: int, switch: int, save: bool = False) -> dict:
    """WRITE: take a block's on/off off a footswitch. ``switch`` is 1-based."""
    if switch < 1:
        raise HxError("footswitch numbers are 1-based (1, 2, 3 ...)")
    with open_session() as session:
        session.unassign_bypass_footswitch(block, switch - 1)
        if save:
            _commit(session)
    return {"ok": True, "block": block, "switch": switch, "saved": save}


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
    catalog = default_catalog()
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
                    "name": catalog.display_name(model) if model is not None else None,
                    "values": values,
                }
            )
    return blocks
