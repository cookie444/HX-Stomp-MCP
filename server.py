#!/usr/bin/env python3
"""MCP stdio server entrypoint for the HX Stomp server.

Run via an MCP client (OpenCode/Claude/Cursor) or directly:

    .venv\\Scripts\\python server.py

Exposes read-only tools plus edit-buffer writes (set block model, footswitch
assign, reload/save). Firmware/DFU is never touched.
"""

from __future__ import annotations

# The MCP SDK renamed FastMCP to MCPServer in v2; support both. The subset we
# use (``.tool()`` and ``.run()``) is identical across them.
try:  # mcp < 2
    from mcp.server.fastmcp import FastMCP as _Server
except ModuleNotFoundError:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer as _Server

from hx_mcp.catalog import default_catalog
from hx_mcp.files import hlx
from hx_mcp.session import HxError
from hx_mcp.tools import device

mcp = _Server("hx-stomp")


@mcp.tool()
def get_device_info() -> dict:
    """Identity and version of the connected Line 6 HX Stomp (read-only).

    Returns USB VID/PID, serial, USB release, firmware version, and the
    currently loaded preset. Never writes to the device.
    """
    return device.get_device_info()


@mcp.tool()
def list_setlists() -> dict:
    """List the device's setlists by name (read-only)."""
    return _guard(device.list_setlists)


@mcp.tool()
def list_presets(setlist: int = 0) -> dict:
    """List preset names in a setlist, in order (read-only).

    Args:
        setlist: Setlist number, 0-based.
    """
    return _guard(device.list_presets, setlist)


@mcp.tool()
def read_current_preset() -> dict:
    """Summarise the loaded preset: name, firmware, and numeric block list.

    Block and parameter names require the HX Edit catalog (M2); until then the
    values are numeric indices. Read-only.
    """
    return _guard(device.read_current_preset)


@mcp.tool()
def set_block_model(block: int, model: int, save: bool = False) -> dict:
    """WRITE: change what a block is, by numeric wire model number.

    save=True commits it to the preset (permanent). Otherwise it lives in the
    edit buffer only: the server's persistent session keeps the device drained,
    and the panel (scrolling) or reload_current_preset reverts it.
    Model numbers are Helix.sym indices; use model_name/find_model for names.

    Args:
        block: Slot index (0 = input; effect slots are 1..8).
        model: Numeric model number, e.g. 247 is the "Room" reverb.
        save: True commits permanently; False (default) edit-buffer only.
    """
    return _guard(device.set_block_model, block, model, None, save)


@mcp.tool()
def assign_block_footswitch(block: int, switch: int, save: bool = False) -> dict:
    """WRITE: put a block's on/off (bypass) on a footswitch. switch is 1-based.

    Committed when save=True; otherwise edit-buffer only.

    Args:
        block: Slot index of the block.
        switch: Footswitch number, 1-based.
        save: True commits permanently; False (default) edit-buffer only.
    """
    return _guard(device.assign_block_footswitch, block, switch, save)


@mcp.tool()
def model_name(number: int) -> dict:
    """Resolve a numeric wire model number to its name (read-only).

    Needs HX Edit's model data (Helix.sym); returns the number unresolved if it
    is not installed.

    Args:
        number: Wire model number, e.g. 247 -> "Room".
    """
    catalog = default_catalog()
    return {"model": number, "name": catalog.display_name(number), "catalog": catalog.available}


@mcp.tool()
def find_model(name: str) -> dict:
    """Find wire model numbers whose name matches, e.g. "Glitz" -> [490, 491].

    Read-only. Needs HX Edit's model data.
    """
    catalog = default_catalog()
    numbers = catalog.find(name)
    return {"name": name, "numbers": numbers, "names": {n: catalog.display_name(n) for n in numbers}}


@mcp.tool()
def backup_presets() -> dict:
    """Read-only backup of every preset to a timestamped file in ./backups/.

    Safe: reads each slot without loading it. Returns the file path and counts.
    """
    return _guard(device.backup_presets)


@mcp.tool()
def read_footswitch(switch: int) -> dict:
    """A footswitch's configuration: what it controls and its colour (read-only).

    Args:
        switch: Footswitch number, 1-based.
    """
    return _guard(device.read_footswitch, switch)


@mcp.tool()
def read_preset_at(setlist: int, index: int) -> dict:
    """Read a stored preset by slot WITHOUT loading it (read-only).

    Reads flash, so it shows what is saved, unaffected by the edit buffer.

    Args:
        setlist: Setlist number, 0-based.
        index: Preset index within the setlist, 0-based.
    """
    return _guard(device.read_preset_at, setlist, index)


@mcp.tool()
def reload_current_preset() -> dict:
    """Discard any unsaved edit buffer by re-loading the current preset.

    Use after host edits you don't want to keep, or to un-stick a front panel
    showing a stranded edit.
    """
    return _guard(device.reload_current_preset)


@mcp.tool()
def save_current_preset(name: str = "") -> dict:
    """DESTRUCTIVE: commit the edit buffer to the current preset's slot (flash).

    Overwrites the slot. Back up first.

    Args:
        name: New preset name; empty keeps the current name.
    """
    return _guard(device.save_current_preset, name or None)


@mcp.tool()
def parse_hlx(path: str) -> dict:
    """Parse an .hlx preset file offline - no hardware required (read-only).

    Args:
        path: Path to an .hlx file.
    """
    try:
        preset = hlx.load(path)
    except hlx.HlxError as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "name": preset.name,
        "blocks": preset.blocks,
    }


def _guard(func, *args) -> dict:
    """Run a device function, turning connection/protocol errors into data."""
    try:
        return func(*args)
    except HxError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def main() -> None:
    """Run the server over stdio (the transport MCP clients spawn)."""
    mcp.run()


if __name__ == "__main__":
    main()
