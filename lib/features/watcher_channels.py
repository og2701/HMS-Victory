"""Channels the hand-run watcher is covering for a while, on Oggers' say-so in #bot-workshop.

The watcher skill (.claude/skills/watcher) keeps ``data/watcher/extra_channels.json`` with one entry per watch:
the channel, the users whose pings go to the watcher there, and when it ends. While a watch runs, Vic's own chat
replies (direct-mention answers and live chat) stand down for those users in that channel, so nobody gets two
answers; everyone else there still gets normal Vic.

Two time checks mean a crashed session can never leave a channel muted: an entry stops counting the moment its
``expires_at`` passes, and every entry stops counting when the watcher's heartbeat file is more than
``HEARTBEAT_MAX_AGE`` old (the watcher touches it on every poll while a watch is on).
"""

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

import config

logger = logging.getLogger(__name__)

WATCHER_DIR = os.path.join(config.DATA_DIR, "watcher")
STATE_FILE = os.path.join(WATCHER_DIR, "extra_channels.json")
HEARTBEAT_FILE = os.path.join(WATCHER_DIR, "heartbeat")
HEARTBEAT_MAX_AGE = 300  # seconds; the orchestrator restarts the watcher within moments of each wake
CACHE_SECONDS = 5.0

_cache: Dict[str, Any] = {"at": 0.0, "watches": []}


def _read_watches() -> List[Dict[str, Any]]:
    try:
        with open(STATE_FILE) as f:
            data = json.load(f)
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        logger.warning("Couldn't read %s; treating it as no watches.", STATE_FILE, exc_info=True)
        return []
    watches = data.get("watches") if isinstance(data, dict) else None
    return [w for w in watches or [] if isinstance(w, dict)]


def _heartbeat_fresh(now: float) -> bool:
    try:
        return now - os.path.getmtime(HEARTBEAT_FILE) <= HEARTBEAT_MAX_AGE
    except OSError:
        return False


def active_watches(now: Optional[float] = None) -> List[Dict[str, Any]]:
    """The watches in force right now: unexpired, and only while the watcher is alive. Cached a few seconds."""
    now = time.time() if now is None else now
    if now - _cache["at"] > CACHE_SECONDS or now < _cache["at"]:
        _cache["watches"] = _read_watches()
        _cache["at"] = now
    watches = _cache["watches"]
    if not watches or not _heartbeat_fresh(now):
        return []
    out = []
    for w in watches:
        try:
            if float(w.get("expires_at", 0)) > now:
                out.append(w)
        except (TypeError, ValueError):
            continue
    return out


def watch_for(channel_id: Optional[int], user_id: Optional[int], now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """The watch that hands this user's pings in this channel to the watcher, if one is on."""
    if channel_id is None or user_id is None:
        return None
    for w in active_watches(now):
        try:
            if int(w.get("channel_id")) != int(channel_id):
                continue
            if int(user_id) in {int(u) for u in w.get("users") or []}:
                return w
        except (TypeError, ValueError):
            continue
    return None


def clear_cache() -> None:
    _cache["at"] = 0.0
    _cache["watches"] = []
