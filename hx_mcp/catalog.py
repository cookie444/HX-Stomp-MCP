"""Model catalog: wire model number <-> name, imported from the user's HX Edit.

The device addresses blocks by a **numeric model number**, which is the index
into HX Edit's ``Helix.sym`` array (833 entries) - verified: ``Helix.sym[247]``
is ``HD2_ReverbRoomStereo`` ("Room"), matching what the hardware reports.

``Helix.sym`` and ``HelixModelDefs.bin`` are **Line 6 proprietary data**. This
project never bundles or redistributes them; it reads them from the user's own
HX Edit install at runtime and degrades gracefully when HX Edit is absent
(numeric indices still work).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Optional

from hx_mcp.protocol.codec import unpack_values

#: Where HX Edit lands on each platform. ``res`` holds the data files.
_WINDOWS_ROOTS = [
    r"C:\Program Files (x86)\Line6\HX Edit",
    r"C:\Program Files\Line6\HX Edit",
    r"C:\Program Files\Line 6\HX Edit",
    r"C:\Program Files (x86)\Line 6\HX Edit",
    r"C:\Program Files\Line 6\HX Edit Pro",
]
_MAC_ROOTS = [
    "/Applications/Line 6/HX Edit.app/Contents/Resources",
    "/Applications/HX Edit.app/Contents/Resources",
    "/Applications/Line6/HX Edit.app/Contents/Resources",
]
_LINUX_ROOTS = [
    os.path.expanduser("~/.local/share/line6/hxedit"),
]

#: Files we need from an install.
_REQUIRED = "Helix.sym"


def _res_dir(root: str) -> Optional[str]:
    """Return the directory holding ``Helix.sym`` under ``root`` (or ``root``)."""
    for candidate in (os.path.join(root, "res"), root):
        if os.path.exists(os.path.join(candidate, _REQUIRED)):
            return candidate
    return None


@dataclass
class HxEditInstall:
    root: str  # the directory containing the data files

    @property
    def helix_sym(self) -> str:
        return os.path.join(self.root, "Helix.sym")

    @property
    def model_defs_bin(self) -> Optional[str]:
        path = os.path.join(self.root, "HelixModelDefs.bin")
        return path if os.path.exists(path) else None

    @property
    def model_catalog_json(self) -> Optional[str]:
        path = os.path.join(self.root, "HX_ModelCatalog.json")
        return path if os.path.exists(path) else None

    @property
    def controls_json(self) -> Optional[str]:
        path = os.path.join(self.root, "HelixControls.json")
        return path if os.path.exists(path) else None

    def available(self) -> bool:
        return os.path.exists(self.helix_sym)


def find_hx_edit() -> Optional[HxEditInstall]:
    """Locate the user's own HX Edit data. ``None`` when it is not installed."""
    candidates: list[str] = []
    env = os.environ.get("HXEDIT_DIR")
    if env:
        candidates.append(env)
    candidates += _WINDOWS_ROOTS + _MAC_ROOTS + _LINUX_ROOTS
    for root in candidates:
        res = _res_dir(root)
        if res:
            return HxEditInstall(res)
    return None


#: The suffix Helix.sym uses to split a model's mono and stereo DSP variants.
_SUFFIXES = ("Mono", "Stereo")


def _base_symbol(symbol: str) -> str:
    for suffix in _SUFFIXES:
        if symbol.endswith(suffix):
            return symbol[: -len(suffix)]
    return symbol


class Catalog:
    """Wire model number <-> name mapping, read from HX Edit's files.

    ``symbols[number]`` is the Helix.sym symbol at that index (the wire id);
    names come from ``HelixModelDefs.bin``, folding the Mono/Stereo split.
    """

    def __init__(self, install: Optional[HxEditInstall] = None) -> None:
        self.install = install if install is not None else find_hx_edit()
        #: index -> Helix.sym symbol (the wire model number is the index).
        self.symbols: list[str] = []
        #: symbol -> ordered parameter names (the device's parameter index order).
        self.params: dict[str, list[str]] = {}
        #: symbolicID -> definition record (name, category, params, ...).
        self.defs: dict[str, dict] = {}
        if self.install is not None:
            self._load()

    def _load(self) -> None:
        assert self.install is not None
        try:
            doc = json.loads(open(self.install.helix_sym, encoding="utf-8").read())
            self.symbols = [str(entry.get("symbol", "")) for entry in doc]
            self.params = {
                str(entry.get("symbol", "")): [str(p) for p in (entry.get("parameters") or [])]
                for entry in doc
            }
        except (OSError, json.JSONDecodeError):
            self.symbols = []
            self.params = {}
        path = self.install.model_defs_bin
        if path:
            try:
                records = unpack_values(open(path, "rb").read(), 1)[0]
            except Exception:  # noqa: BLE001 - degrade to symbol-only names
                records = []
            for record in records or []:
                if isinstance(record, dict) and record.get("symbolicID"):
                    self.defs[str(record["symbolicID"])] = record

    @property
    def available(self) -> bool:
        return bool(self.symbols)

    def display_name(self, number: int) -> str:
        """Human name for a wire model number, e.g. 491 -> "Glitz"."""
        if not (0 <= number < len(self.symbols)):
            return f"model {number}"
        symbol = self.symbols[number]
        record = self.defs.get(symbol) or self.defs.get(_base_symbol(symbol))
        name = (record or {}).get("name")
        if name:
            return str(name).strip()
        return _base_symbol(symbol).replace("HD2_", "")

    def find(self, name: str) -> list[int]:
        """Every wire model number whose display name matches ``name``."""
        query = name.strip().lower()
        out: list[int] = []
        for index, symbol in enumerate(self.symbols):
            record = self.defs.get(symbol) or self.defs.get(_base_symbol(symbol))
            disp = str((record or {}).get("name", "")).strip().lower()
            if disp == query or _base_symbol(symbol).lower() == query:
                out.append(index)
        return out

    def names_for(self, numbers) -> dict[int, str]:
        return {n: self.display_name(n) for n in numbers}

    def param_names(self, number: int) -> list[str]:
        """Ordered parameter names for a model - their device index order."""
        if not (0 <= number < len(self.symbols)):
            return []
        symbol = self.symbols[number]
        return list(self.params.get(symbol) or self.params.get(_base_symbol(symbol)) or [])

    def param_index(self, number: int, name: str) -> int | None:
        """The wire parameter index (key 28) for a named parameter."""
        for i, pname in enumerate(self.param_names(number)):
            if pname.lower() == name.lower():
                return i
        return None


#: Cached default catalog, so tool calls don't reparse ~1 MB every time.
_default: Optional[Catalog] = None


def default_catalog() -> Catalog:
    global _default
    if _default is None:
        _default = Catalog()
    return _default
