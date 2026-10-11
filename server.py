#!/usr/bin/env python3
"""MCP stdio server entrypoint for the HX Stomp server.

Run via an MCP client (OpenCode/Claude/Cursor) or directly:

    .venv\\Scripts\\python server.py

Currently exposes read-only tools (M0). Destructive tools land later and carry
an explicit-confirmation requirement.
"""

from __future__ import annotations

# The MCP SDK renamed FastMCP to MCPServer in v2; support both. The subset we
# use (``.tool()`` and ``.run()``) is identical across them.
try:  # mcp < 2
    from mcp.server.fastmcp import FastMCP as _Server
except ModuleNotFoundError:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer as _Server

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
def set_block_model(block: int, model: int) -> dict:
    """WRITE: change what a block is, by numeric wire model number (edit buffer).

    Reloading the preset reverts it. Model numbers are Helix.sym indices; names
    need the HX Edit catalog (M2). Not yet saved to a slot.

    Args:
        block: Slot index (0 = input; effect slots are 1..8).
        model: Numeric model number, e.g. 247 is the "Room" reverb.
    """
    return _guard(device.set_block_model, block, model)


@mcp.tool()
def assign_block_footswitch(block: int, switch: int) -> dict:
    """WRITE: put a block's on/off (bypass) on a footswitch (edit buffer).

    Footswitch numbers are 1-based (1, 2, 3). Reloading the preset reverts it.

    Args:
        block: Slot index of the block.
        switch: Footswitch number, 1-based.
    """
    return _guard(device.assign_block_footswitch, block, switch)


@mcp.tool()
def read_footswitch(switch: int) -> dict:
    """A footswitch's configuration: what it controls and its colour (read-only).

    Args:
        switch: Footswitch number, 1-based.
    """
    return _guard(device.read_footswitch, switch)


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
