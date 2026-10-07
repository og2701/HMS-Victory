"""/skyrim in the HMS Games activity: the engine (lib/features/skyrim) as JSON. See
docs/skyrim-activity-contract.md for every route and shape.

  common.py    text, the character at a glance, panel pieces
  registry.py  which module builds and runs each panel
  core.py      the town, the map, launching a delve and playing it turn by turn (with animation cues)
  bouts.py     the Pit and ghost duels
  panels_*.py  every other screen, each a port of a views.py screen
  routes.py    the aiohttp routes, wired in from lib/activities/server.py
"""
