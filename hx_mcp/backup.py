"""Read-only backup of the pedal's presets.

Walks every preset slot with opcode 4 (read a stored preset *without* loading
it) and stores the raw ``l6-helix`` document, base64-encoded, alongside the
preset names. This is a faithful, restorable snapshot and touches nothing on the
device - no writes, no edit buffer, no preset loads.

Later milestones extend this to globals and IRs (the ``.hxb`` container).
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any, Callable, Optional

from hx_mcp.session import Session

#: HX Stomp holds 126 presets; other devices differ.
DEFAULT_COUNT = 126


def capture(
    session: Session,
    setlist: int = 0,
    count: int = DEFAULT_COUNT,
    on_progress: Optional[Callable[[int, dict], None]] = None,
) -> dict[str, Any]:
    """Read every stored preset into a JSON-serialisable dict."""
    names = session.presets(setlist)
    presets: list[dict[str, Any]] = []
    for index in range(count):
        try:
            blob = session.read_preset_at(setlist, index)
        except Exception as exc:  # noqa: BLE001 - record and keep going
            entry: dict[str, Any] = {"index": index, "error": f"{type(exc).__name__}: {exc}"}
        else:
            entry = {"index": index, "name": names[index] if index < len(names) else None}
            if blob is None:
                entry["empty"] = True
            else:
                entry["bytes"] = len(blob)
                entry["document_b64"] = base64.b64encode(blob).decode("ascii")
        presets.append(entry)
        if on_progress is not None:
            on_progress(index, entry)
    return {
        "setlist": setlist,
        "count": count,
        "preset_names": names,
        "presets": presets,
    }


def save(data: dict[str, Any], path: str) -> str:
    """Write a capture to disk as JSON. Returns the path."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, indent=1), encoding="utf-8")
    return path
