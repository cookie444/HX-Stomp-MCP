"""Tone profiles - reusable 'preset bases' captured from the edit buffer.

When a tone sounds right, capture it: read the loaded preset's edit buffer and
store every block's model and parameter values to a JSON profile under
``tones/``. That is the *base* - the exact recipe - and it can be rebuilt onto
the edit buffer later with :func:`apply`.

Capture is read-only (reads the buffer, writes a local file); it never touches
the device. Apply is a write and follows the same desktop-beta rules: one
session, edit buffer only, never save.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from hx_mcp.catalog import default_catalog
from hx_mcp.session import Session, parse_preset

TONES_DIR = "tones"


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "tone"


def _effect_blocks(tone: Any) -> list[tuple[int, dict]]:
    return [
        (i, slot)
        for i, slot in enumerate(tone.get(0, {}).get(22) or [])
        if isinstance(slot, dict) and slot.get(19) == 6
    ]


def capture(session: Session, name: str, reference: str = "") -> dict:
    """Read the loaded preset (edit buffer) into a tone profile dict."""
    blob = session.read_preset_blob()
    _, _, tone = parse_preset(blob)
    catalog = default_catalog()

    blocks: list[dict] = []
    for i, slot in _effect_blocks(tone):
        body = slot.get(20) if isinstance(slot.get(20), dict) else {}
        ref = body.get(24) if isinstance(body.get(24), dict) else {}
        model = ref.get(25)
        names = catalog.param_names(model) if isinstance(model, int) else []
        values = (body.get(11) or {}).get(4) if isinstance(body.get(11), dict) else []
        params = [
            {
                "index": idx,
                "name": names[idx] if idx < len(names) else f"param {idx}",
                "value": values[idx] if values and idx < len(values) else None,
            }
            for idx in range(max(len(names), len(values or [])))
        ]
        blocks.append(
            {
                "slot": i,
                "model": model,
                "name": catalog.display_name(model) if isinstance(model, int) else None,
                "paired": ref.get(26) if ref.get(23) else None,
                "params": params,
            }
        )

    return {
        "name": name,
        "reference": reference,
        "captured": datetime.now().isoformat(timespec="seconds"),
        "blocks": blocks,
    }


def save(profile: dict, directory: str = TONES_DIR) -> str:
    path = os.path.join(directory, _slug(profile.get("name", "tone")) + ".json")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(profile, indent=1), encoding="utf-8")
    return path


def load(name: str, directory: str = TONES_DIR) -> tuple[dict, str]:
    path = os.path.join(directory, _slug(name) + ".json")
    return json.loads(Path(path).read_text(encoding="utf-8")), path


def list_tones(directory: str = TONES_DIR) -> list[str]:
    d = Path(directory)
    return sorted(p.stem for p in d.glob("*.json")) if d.exists() else []


def apply(session: Session, profile: dict) -> dict:
    """Rebuild a profile onto the edit buffer: clear all blocks, then set models
    and parameters. One session, no save."""
    _, _, tone = parse_preset(session.read_preset_blob())
    for i, _ in _effect_blocks(tone):
        session.clear_block(i)
    for block in profile.get("blocks", []):
        slot = block.get("slot")
        if not isinstance(slot, int):
            continue
        session.set_model(slot, block["model"], block.get("paired"))
        for param in block.get("params") or []:
            if param.get("value") is None:
                continue
            session.set_param(slot, param["index"], param["value"])
    return profile
