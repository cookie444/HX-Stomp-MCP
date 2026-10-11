#!/usr/bin/env python3
"""Read-only backup of every preset to a timestamped JSON file.

Safe: walks each preset slot with opcode 4 (read a stored preset *without*
loading it). No writes, no edit buffer, no preset loads - it is the one
operation that cannot wedge the device.

    .venv\\Scripts\\python.exe backup_now.py

Writes into ``backups/`` (git-ignored) as base64 ``l6-helix`` documents plus the
preset names.
"""

from __future__ import annotations

import os
from datetime import datetime

from hx_mcp import backup as bk
from hx_mcp.tools.device import open_session


def main() -> int:
    with open_session() as session:
        setlist, index, name = session.preset_info()
        print(f"device preset: {name} (index {index})")
        try:
            data = bk.capture(session)
        except Exception as exc:  # noqa: BLE001
            print(f"backup failed: {type(exc).__name__}: {exc}")
            return 1
        data["device"] = {"setlist": setlist, "index": index, "name": name}

    path = f"backups/hx_stomp_{datetime.now():%Y%m%d_%H%M%S}.json"
    bk.save(data, path)
    nonempty = sum(1 for p in data["presets"] if p.get("document_b64"))
    errors = sum(1 for p in data["presets"] if p.get("error"))
    print(
        f"backed up {nonempty}/{len(data['presets'])} presets -> {path} "
        f"({os.path.getsize(path)} bytes, {errors} errors)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
