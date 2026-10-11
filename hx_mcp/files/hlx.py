"""``.hlx`` preset files - plain JSON, so they need no hardware.

An ``.hlx`` is Line 6's exported preset format and is independently documented
as JSON. This module is deliberately tolerant: it loads whatever JSON is there
and exposes a best-effort summary of the blocks, so it is useful for inspection
even before M2 maps model numbers to names.

TODO(M1+): pin the exact schema against real exports and add the writer side
(``preset -> .hlx``) plus round-trip validation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Union

PathLike = Union[str, Path]


class HlxError(Exception):
    """A .hlx file could not be read or decoded."""


@dataclass
class HlxPreset:
    name: str
    #: The raw decoded JSON, untouched.
    raw: Any
    #: Best-effort list of block descriptors.
    blocks: list[dict] = field(default_factory=list)


def load(path: PathLike) -> HlxPreset:
    """Read an .hlx file from disk."""
    p = Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HlxError(f"could not read {p}: {exc}") from exc
    return parse(data)


def parse(data: Any) -> HlxPreset:
    """Interpret an already-decoded .hlx JSON document."""
    name = _find_name(data)
    return HlxPreset(name=name, raw=data, blocks=_summarise_blocks(data))


def _find_name(data: Any) -> str:
    if isinstance(data, dict):
        for key in ("name", "Name", "preset_name", "presetName"):
            value = data.get(key)
            if isinstance(value, str) and value:
                return value
        # HX Edit nests metadata under a "meta" object.
        meta = data.get("meta")
        if isinstance(meta, dict):
            inner = _find_name(meta)
            if inner:
                return inner
    return ""


def _get(obj: Any, *keys: Any) -> Any:
    """Fetch a value by any of several keys. JSON object keys are strings, so
    the same field reads as ``22`` on the wire and ``"22"`` in an .hlx."""
    if not isinstance(obj, dict):
        return None
    for key in keys:
        if key in obj:
            return obj[key]
    return None


def _summarise_blocks(data: Any) -> list[dict]:
    """Walk the document for slot-like objects; tolerant of schema variation."""
    blocks: list[dict] = []
    if isinstance(data, dict):
        # The preset map carries DSPs under keys 0..1, each with a slot array
        # at key 22. Accept both the wire shape (int keys) and .hlx (str keys).
        for dsp in (_get(data, 0, "0"), _get(data, 1, "1")):
            if isinstance(dsp, dict):
                _collect_slots(_get(dsp, 22, "22"), blocks)
    return blocks


def _collect_slots(slots: Any, out: list[dict]) -> None:
    if not isinstance(slots, list):
        return
    for i, slot in enumerate(slots):
        if not isinstance(slot, dict):
            continue
        kind = _get(slot, 19, "19")
        body = _get(slot, 20, "20")
        if body is None:
            body = slot
        model = None
        model_ref = _get(body, 24, "24")
        if isinstance(model_ref, dict):
            model = _get(model_ref, 25, "25")
        out.append({"slot": i, "kind": kind, "model": model})
