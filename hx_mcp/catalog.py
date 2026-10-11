"""Model catalog: index -> name/range/format, imported from the user's HX Edit.

Device model numbers and parameter indices are meaningless without the catalog
of names, ranges and display formats. That catalog lives in HX Edit's data
files (``HelixModelDefs.bin``, ``HX_ModelCatalog.json``, ``Helix.sym``, and the
``*.models`` files), which are **Line 6 proprietary data**.

This project therefore never bundles or redistributes them. It reads them from
the user's own installed HX Edit at runtime and degrades gracefully when HX
Edit is absent - numeric block/parameter indices still work without it.

M2 populates this module. For now it only knows how to *find* an install.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

#: Where HX Edit lands on each platform.
_WINDOWS_ROOTS = [
    r"C:\Program Files\Line 6\HX Edit",
    r"C:\Program Files (x86)\Line 6\HX Edit",
]
_MAC_ROOTS = [
    "/Applications/Line 6/HX Edit.app/Contents/Resources",
    "/Applications/HX Edit.app/Contents/Resources",
]
_LINUX_ROOTS = [
    os.path.expanduser("~/.local/share/line6/hxedit"),
]


@dataclass
class HxEditInstall:
    root: str

    def _has(self, *names: str) -> bool:
        return all(os.path.exists(os.path.join(self.root, n)) for n in names)

    @property
    def model_defs_bin(self) -> Optional[str]:
        path = os.path.join(self.root, "HelixModelDefs.bin")
        return path if os.path.exists(path) else None

    @property
    def model_catalog_json(self) -> Optional[str]:
        path = os.path.join(self.root, "HX_ModelCatalog.json")
        return path if os.path.exists(path) else None

    @property
    def helix_sym(self) -> Optional[str]:
        path = os.path.join(self.root, "Helix.sym")
        return path if os.path.exists(path) else None

    def available(self) -> bool:
        return bool(self.model_defs_bin or self.model_catalog_json or self.helix_sym)


def find_hx_edit() -> Optional[HxEditInstall]:
    """Locate the user's own HX Edit data. ``None`` when it is not installed."""
    candidates = _WINDOWS_ROOTS + _MAC_ROOTS + _LINUX_ROOTS
    env = os.environ.get("HXEDIT_DIR")
    if env:
        candidates.insert(0, env)
    for root in candidates:
        if os.path.isdir(root):
            install = HxEditInstall(root)
            if install.available():
                return install
    return None


#: A named parameter: its device index, human name, range and display format.
@dataclass
class ParamDef:
    index: int
    name: str
    min: float
    max: float
    default: float
    value_type: str
    display_type: str
    assign: Optional[int] = None


@dataclass
class ModelDef:
    number: int
    name: str
    symbolic_id: str
    category: str
    params: list[ParamDef]


class Catalog:
    """Index -> name/range mapping. Populated in M2; empty until then."""

    def __init__(self, install: Optional[HxEditInstall] = None) -> None:
        self.install = install if install is not None else find_hx_edit()
        self.models: dict[int, ModelDef] = {}

    @property
    def available(self) -> bool:
        return bool(self.models)

    def model_name(self, number: int) -> str:
        model = self.models.get(number)
        return model.name if model else f"model {number}"

    def param_name(self, model: int, index: int) -> str:
        model_def = self.models.get(model)
        if model_def and 0 <= index < len(model_def.params):
            return model_def.params[index].name
        return f"param {index}"


# TODO(M2): parse HelixModelDefs.bin (MessagePack) into ModelDef/ParamDef and
# join Helix.sym's array index (the device model number) to model names.
