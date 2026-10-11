"""MCP tool definitions and handlers.

Each submodule holds plain functions; ``server.py`` registers them with the MCP
SDK. Destructive tools (IR upload/delete, global writes, slot restore) must be
marked as requiring explicit user confirmation - they are not present yet.
"""

from . import device

__all__ = ["device"]
