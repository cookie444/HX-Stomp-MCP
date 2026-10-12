#!/usr/bin/env python3
"""MCP stdio server entrypoint for the HX Stomp server.

Run via an MCP client (OpenCode/Claude/Cursor) or directly:

    .venv\\Scripts\\python.exe server.py

Exposes read-only tools plus edit-buffer writes (set block model, footswitch
assign, reload/save). Firmware/DFU is never touched.

⚠️ DESKTOP BETA: host-side edits leave the pedal unable to scroll/switch presets
and can wedge it (recover by pulling the 9V power adapter). Reads are always
safe. See README.
"""

from __future__ import annotations

import json

# The MCP SDK renamed FastMCP to MCPServer in v2; support both. The subset we
# use (``.tool()``, ``.resource()`` and ``.run()``) is identical across them.
try:  # mcp < 2
    from mcp.server.fastmcp import FastMCP as _Server
except ModuleNotFoundError:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer as _Server

try:
    from mcp.types import ToolAnnotations
except Exception:  # pragma: no cover - very old SDKs
    ToolAnnotations = None  # type: ignore

from hx_mcp.catalog import default_catalog
from hx_mcp.files import hlx
from hx_mcp.session import HxError
from hx_mcp.tools import device

#: Read-only: safe, never changes the device (and has never wedged it).
READ = ToolAnnotations(readOnlyHint=True) if ToolAnnotations else None
#: Edits the edit buffer / preset. Beta: can wedge the pedal (see README).
EDIT = (
    ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False)
    if ToolAnnotations
    else None
)
#: Restores the preset; not destructive, idempotent.
RELOAD = (
    ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True)
    if ToolAnnotations
    else None
)


def _ann(a):
    return {"annotations": a} if a is not None else {}


mcp = _Server("hx-stomp")


# ------------------------------------------------------------- read tools ---


@mcp.tool(**_ann(READ))
def get_device_info() -> dict:
    """Identity and version of the connected Line 6 HX Stomp (read-only).

    Returns USB VID/PID, serial, USB release, firmware version, and the
    currently loaded preset. Never writes to the device.
    """
    return device.get_device_info()


@mcp.tool(**_ann(READ))
def list_setlists() -> dict:
    """List the device's setlists by name (read-only)."""
    return _guard(device.list_setlists)


@mcp.tool(**_ann(READ))
def list_presets(setlist: int = 0) -> dict:
    """List preset names in a setlist, in order (read-only).

    Args:
        setlist: Setlist number, 0-based.
    """
    return _guard(device.list_presets, setlist)


@mcp.tool(**_ann(READ))
def read_current_preset() -> dict:
    """Summarise the loaded preset: name, firmware, and blocks with model names.

    Block and parameter names come from your HX Edit install. Read-only.
    """
    return _guard(device.read_current_preset)


@mcp.tool(**_ann(READ))
def read_preset_at(setlist: int, index: int) -> dict:
    """Read a stored preset by slot WITHOUT loading it (read-only).

    Reads flash, so it shows what is saved, unaffected by the edit buffer.

    Args:
        setlist: Setlist number, 0-based.
        index: Preset index within the setlist, 0-based.
    """
    return _guard(device.read_preset_at, setlist, index)


@mcp.tool(**_ann(READ))
def read_block_params(block: int) -> dict:
    """A block's model and every parameter (index, name, current value; read-only).

    Use this before set_block_param to learn the parameter indices.

    Args:
        block: Slot index of the block.
    """
    return _guard(device.read_block_params, block)


@mcp.tool(**_ann(EDIT))
def set_block_param(block: int, index: int, value: float, save: bool = False) -> dict:
    """WRITE (beta): set one parameter on a block (opcode 30) - shapes the tone.

    Get the index from read_block_params. ⚠️ editing can break the pedal's
    scroll (see README).

    Args:
        block: Slot index of the block.
        index: Parameter index (Helix.sym order), from read_block_params.
        value: New value; 0.0..1.0 for most knobs (amp knobs are 0..1 == 0..10).
        save: True commits permanently; False (default) edit-buffer only.
    """
    return _guard(device.set_block_param, block, index, value, save)


@mcp.tool(**_ann(READ))
def read_footswitch(switch: int) -> dict:
    """A footswitch's configuration: what it controls and its colour (read-only).

    Args:
        switch: Footswitch number, 1-based.
    """
    return _guard(device.read_footswitch, switch)


@mcp.tool(**_ann(READ))
def model_name(number: int) -> dict:
    """Resolve a numeric wire model number to its name (read-only).

    Needs HX Edit's model data (Helix.sym); returns the number unresolved if it
    is not installed.

    Args:
        number: Wire model number, e.g. 247 -> "Room".
    """
    catalog = default_catalog()
    return {"model": number, "name": catalog.display_name(number), "catalog": catalog.available}


@mcp.tool(**_ann(READ))
def find_model(name: str) -> dict:
    """Find wire model numbers whose name matches, e.g. "Glitz" -> [490, 491].

    Read-only. Needs HX Edit's model data.
    """
    catalog = default_catalog()
    numbers = catalog.find(name)
    return {"name": name, "numbers": numbers, "names": {n: catalog.display_name(n) for n in numbers}}


@mcp.tool(**_ann(READ))
def backup_presets() -> dict:
    """Read-only backup of every preset to a timestamped file in ./backups/.

    Safe: reads each slot without loading it. Returns the file path and counts.
    """
    return _guard(device.backup_presets)


@mcp.tool(**_ann(READ))
def parse_hlx(path: str) -> dict:
    """Parse an .hlx preset file offline - no hardware required (read-only).

    Args:
        path: Path to an .hlx file.
    """
    try:
        preset = hlx.load(path)
    except hlx.HlxError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "name": preset.name, "blocks": preset.blocks}


# ------------------------------------------------------------ write tools ---


@mcp.tool(**_ann(EDIT))
def set_block_model(block: int, model: int, save: bool = False) -> dict:
    """WRITE (beta): change what a block is, by numeric wire model number.

    save=True commits it to the preset (permanent). Otherwise it lives in the
    edit buffer only. ⚠️ Editing can leave the pedal unable to scroll presets -
    build, save, reload, then use the panel. Model numbers are Helix.sym
    indices; use model_name/find_model for names.

    Args:
        block: Slot index (0 = input; effect slots are 1..8).
        model: Numeric model number, e.g. 247 is the "Room" reverb.
        save: True commits permanently; False (default) edit-buffer only.
    """
    return _guard(device.set_block_model, block, model, None, save)


@mcp.tool(**_ann(EDIT))
def assign_block_footswitch(block: int, switch: int, save: bool = False) -> dict:
    """WRITE (beta): put a block's on/off (bypass) on a footswitch. 1-based.

    Committed when save=True; otherwise edit-buffer only.

    Args:
        block: Slot index of the block.
        switch: Footswitch number, 1-based.
        save: True commits permanently; False (default) edit-buffer only.
    """
    return _guard(device.assign_block_footswitch, block, switch, save)


@mcp.tool(**_ann(EDIT))
def clear_block(block: int, save: bool = False) -> dict:
    """WRITE (beta): empty a block slot.

    Args:
        block: Slot index of the block.
        save: True commits permanently; False (default) edit-buffer only.
    """
    return _guard(device.clear_block, block, save)


@mcp.tool(**_ann(EDIT))
def unassign_block_footswitch(block: int, switch: int, save: bool = False) -> dict:
    """WRITE (beta): take a block's on/off off a footswitch. ``switch`` 1-based.

    Args:
        block: Slot index of the block.
        switch: Footswitch number, 1-based.
        save: True commits permanently; False (default) edit-buffer only.
    """
    return _guard(device.unassign_block_footswitch, block, switch, save)


@mcp.tool(**_ann(EDIT))
def save_current_preset(name: str = "") -> dict:
    """WRITE (destructive, beta): commit the edit buffer to the preset's slot.

    Overwrites the slot permanently. Back up first.

    Args:
        name: New preset name; empty keeps the current name.
    """
    return _guard(device.save_current_preset, name or None)


@mcp.tool(**_ann(RELOAD))
def reload_current_preset() -> dict:
    """Re-load the current preset, discarding any unsaved edit buffer.

    Use after host edits you don't want to keep, or to un-stick a front panel
    showing a stranded edit. Not destructive.
    """
    return _guard(device.reload_current_preset)


# -------------------------------------------------------------- resources ---


@mcp.resource(
    "hx://device/info",
    name="device-info",
    description="Identitiy and loaded preset of the connected HX Stomp, as JSON.",
    mime_type="application/json",
)
def resource_device_info() -> str:
    return json.dumps(device.get_device_info(), indent=2)


@mcp.resource(
    "hx://catalog/models",
    name="model-catalog",
    description="Wire model number -> name, imported from the user's HX Edit, as JSON.",
    mime_type="application/json",
)
def resource_model_catalog() -> str:
    catalog = default_catalog()
    models = [
        {"number": i, "name": catalog.display_name(i), "symbol": sym}
        for i, sym in enumerate(catalog.symbols)
    ]
    return json.dumps(
        {"available": catalog.available, "count": len(models), "models": models}, indent=1
    )


# ---------------------------------------------------------------- prompts ---
# Reusable workflows distilled from real use. Each one encodes the rules learned
# the hard way: research first, build from scratch, one session, edit buffer
# only, never save, and don't touch the pedal while editing.


@mcp.prompt(
    name="build_tone",
    title="Build a tone from scratch",
    description="Build a guitar tone on the HX Stomp edit buffer from an artist/song reference.",
)
def prompt_build_tone(reference: str, extra: str = "") -> str:
    notes = f"\nExtra guidance: {extra}\n" if extra else ""
    return f"""Build a guitar tone on the connected Line 6 HX Stomp for: {reference}.{notes}
Follow these rules exactly - they come from hard-won testing.

1. RESEARCH FIRST. Before choosing any model, look up the real rig for
   "{reference}" (amp, cab, pedals, EQ, and how much gain). Base the tone on what
   was actually used, not guesswork.

2. FROM SCRATCH. Call read_current_preset, then clear_block on EVERY existing
   effect block. Nothing may carry over from a previous tone.

3. CHOOSE MODELS BY NAME. Use find_model / model_name to turn names into wire
   numbers (the number is the Helix.sym index; names come from the user's HX
   Edit). Usual layout: slot 1 = drive/boost, slot 2 = amp (use the paired
   amp+cab form). Add delay/reverb ONLY if the real rig used them - many are
   dry. Keep gain light unless the reference is genuinely heavy.

4. SET THE PARAMETERS - this is what makes it sound right. For each block, call
   read_block_params to get the parameter indices, then set_block_param to set
   gain/EQ/mix. Match the researched knob values (amp knobs are 0..1 = 0..10).

5. ONE SESSION, NO RECONNECTS. All edits share the persistent session; never
   re-open the device between edits.

6. EDIT BUFFER ONLY - NEVER SAVE. Saving is out of scope and has broken the
   device. Do not call save_current_preset.

7. DON'T TOUCH THE PEDAL. Do not ask the user to scroll or switch presets while
   editing. Tell them to play it; when they're done, call
   reload_current_preset to discard.

Finally, summarise the chain: slot -> model name -> the key settings you chose.
"""


@mcp.prompt(
    name="tweak_tone",
    title="Tweak the current tone",
    description="Adjust the current edit buffer, e.g. 'a little more crunch'.",
)
def prompt_tweak_tone(change: str) -> str:
    return f"""Adjust the current HX Stomp edit buffer: "{change}".

Read the chain with read_current_preset, inspect the relevant block with
read_block_params, and change only what's needed with set_block_param (gain, EQ,
mix, ...). Prefer the smallest change that achieves "{change}".

Edit buffer only; one session; NEVER save. Summarise exactly what you changed.
"""


@mcp.prompt(
    name="swap_block",
    title="Change a block's model",
    description="Change one slot to a different model, by name.",
)
def prompt_swap_block(block: int, to: str) -> str:
    return f"""On the HX Stomp edit buffer, change slot {block} to "{to}".

Resolve the model with find_model / model_name, set it with set_block_model,
then give it sensible starting parameters using read_block_params +
set_block_param. Edit buffer only; never save. Report the new block.
"""


@mcp.prompt(
    name="remove_block",
    title="Remove a block",
    description="Empty a slot in the edit buffer.",
)
def prompt_remove_block(block: int) -> str:
    return f"""Remove the block in slot {block} from the HX Stomp edit buffer with
clear_block. Edit buffer only; never save.
"""


@mcp.prompt(
    name="inspect_rig",
    title="Explain the loaded preset",
    description="Describe the current signal chain and settings in plain language.",
)
def prompt_inspect_rig() -> str:
    return """Explain the HX Stomp's currently loaded preset.

Call read_current_preset for the chain, then read_block_params for each effect
block. Describe the signal chain in order and each block's model and key
settings in plain language. Read-only - do not edit anything.
"""


# ----------------------------------------------------------------- helpers ---


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
