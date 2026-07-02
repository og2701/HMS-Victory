"""Skyrim - a standalone persistent RPG for the server (no UKPence, no casino).

Public API: the /skyrim entry point and the restart reattachment hook.
"""

from lib.features.skyrim.views import handle_skyrim_command, reattach_skyrim_view

__all__ = ["handle_skyrim_command", "reattach_skyrim_view"]
