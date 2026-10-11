#!/usr/bin/env python3
"""Read-only backup of every preset to a timestamped JSON file.

Safe: walks each preset slot with opcode 4 (read a stored preset *without*
loading it). No writes, no edit buffer, no preset loads - the one operation that
cannot wedge the device. Also available as the ``backup_presets`` MCP tool.

    .venv\\Scripts\\python.exe backup_now.py

Writes into ``backups/`` (git-ignored) as base64 ``l6-helix`` documents plus the
preset names.
"""

from __future__ import annotations

import sys

from hx_mcp.tools import device


def main() -> int:
    result = device.backup_presets()
    if not result.get("ok"):
        print(f"backup failed: {result.get('error')}")
        return 1
    print(
        f"backed up {result['non_empty']}/{result['count']} presets -> "
        f"{result['path']} ({result['errors']} errors)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
