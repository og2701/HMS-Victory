"""Stage a new adventure before Discord sends it, then commit it durably.

Preparation is synchronous and has no storage writes. A profile-side journal
commits the reward/attempt change and the new board together; recovery finishes
the shared view-file update after a crash. The bot still uses its existing JSON
stores. No Discord types or network operations belong in this module.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass, field
import logging

from lib.features.skyrim import data as D

logger = logging.getLogger(__name__)
_locks: dict[int, tuple[asyncio.Lock, int]] = {}


@asynccontextmanager
async def launch_lock(user_id):
    """One opening adventure per player; release unused locks after waiters exit."""
    uid = int(user_id)
    lock, users = _locks.get(uid, (asyncio.Lock(), 0))
    _locks[uid] = lock, users + 1
    try:
        async with lock:
            yield
    finally:
        _, users = _locks[uid]
        if users <= 1:
            _locks.pop(uid, None)
        else:
            _locks[uid] = lock, users - 1


def _validate(profile, loc_key, kind):
    from lib.features.skyrim import engine as E
    if kind == "normal":
        loc = D.LOCATIONS.get(loc_key)
        if not loc or loc.get("alduin") or loc.get("soulcairn"):
            raise ValueError("That road isn't available.")
        if E.level(profile) < int(loc.get("min_level", 1)):
            raise ValueError("Gain a few levels before taking that road.")
        if loc.get("dragon_lair"):
            import config
            if E.level(profile) < config.SKYRIM_DRAGON_MIN_LEVEL:
                raise ValueError("Dragon lairs aren't open yet.")
        if loc.get("rumour"):
            rumour = next((k for k, r in D.RUMOURS.items() if r["loc"] == loc_key), None)
            if not rumour or (profile.get("rumours") or {}).get(rumour) != "heard":
                raise ValueError("That legend must be heard at Belethor's first.")
        if E.delves_left(profile) <= 0:
            raise ValueError("Rest a while; no delves are ready yet.")
    elif kind == "daily":
        if not E.daily_available(profile):
            raise ValueError("Today's daily adventure is already used.")
    elif kind == "alduin":
        if not E.alduin_available(profile):
            raise ValueError("Skuldafn isn't available right now.")
    elif kind == "soulcairn":
        if not E.soulcairn_available(profile):
            raise ValueError("The Soul Cairn isn't available right now.")
    elif kind == "tutorial":
        if not E.tutorial_available(profile):
            raise ValueError("Your first adventure is already underway or finished.")
    else:
        raise ValueError("Unknown adventure.")


def _materialise(profile):
    """Replay the profile's committed board exactly once; safe after partial IO."""
    from lib.features.skyrim import engine as E
    journal = profile.get("_launch_commit")
    if not journal:
        return profile
    new = journal["board"]
    mid = str(new["message_id"])
    views = E.load_persistent_views()
    old = journal.get("old_board")
    old_mid = journal.get("old_message_id") or (old or {}).get("message_id")
    if old_mid:
        views.pop(str(old_mid), None)
    views[mid] = new
    E.save_persistent_views(views)
    if old and old.get("daily"):
        E.record_daily_result(profile, E.Delve.from_dict(old),
                              attempt_date=journal.get("old_daily_date"))
    profile.pop("_launch_commit", None)
    E.save_profile(profile)
    return profile


def recover_profile(profile):
    """Called before gameplay reads; a failed recovery must not enable stale play."""
    return _materialise(profile)


def recover_all():
    """Run before startup registers persistent boards."""
    from lib.features.skyrim import engine as E
    for profile in E._profiles().values():
        if profile.get("_launch_commit"):
            try:
                _materialise(profile)
            except (OSError, KeyError, TypeError, ValueError):
                # A broken game record must not prevent unrelated bot features
                # from starting. Reading this character retries before play.
                logger.exception("Could not recover Skyrim launch for %s", profile.get("user_id"))


@dataclass
class PendingLaunch:
    profile: dict
    delve: object
    baseline: dict
    source_board: dict | None = None
    old: object = None
    logs: list = field(default_factory=list)
    wonders: list = field(default_factory=list)
    committed: bool = False

    def commit(self, message_id):
        from lib.features.skyrim import engine as E
        if self.committed:
            if self.delve.message_id != int(message_id):
                raise ValueError("This adventure already has a board.")
            return
        current = E.get_profile(self.profile["user_id"])
        if current != self.baseline:
            raise ValueError("Your character changed while opening. Please try again.")
        mid = self.baseline.get("active_delve")
        current_board = E.load_persistent_views().get(str(mid)) if mid else None
        if current_board != self.source_board:
            # Guard, healing and other actions can change only the board. A
            # profile-only comparison would settle a stale clean-exit snapshot.
            raise ValueError("Your adventure changed while opening. Please try again.")
        self.delve.message_id = int(message_id)
        self.profile["active_delve"] = int(message_id)
        self.profile["_launch_commit"] = {
            "board": self.delve.to_dict(),
            "old_board": self.old.to_dict() if self.old else None,
            "old_message_id": self.baseline.get("active_delve"),
            "old_daily_date": (self.baseline.get("daily") or {}).get("date"),
        }
        # This atomic profile replacement is the commit point. It includes the
        # entire board needed to finish the second file if the process stops.
        E.save_profile(self.profile)
        self.committed = True
        try:
            _materialise(self.profile)
        except OSError:
            logger.exception("Skyrim launch committed; board recovery pending")
        for line in self.logs:
            E.glog(line)
        for uid, key in self.wonders:
            E.wlog(uid, key)


def prepare(profile, channel_id, loc_key, kind="normal"):
    from lib.features.skyrim import engine as E
    if profile is None:
        raise ValueError("Run /skyrim to create your character first.")
    baseline = deepcopy(profile)
    staged = deepcopy(profile)
    _validate(staged, loc_key, kind)
    old = E.load_delve(staged.get("active_delve")) if staged.get("active_delve") else None
    source_board = (deepcopy(E.load_persistent_views().get(str(staged["active_delve"])))
                    if staged.get("active_delve") else None)
    if old and old.playing() and "clavicus" in old.pacts:
        raise ValueError("The Bargain seals the exit. Finish your current pact first.")
    # Engine creation emits in-memory audit events, but must only publish them
    # after commit. There is no await here, so other event-loop actions cannot
    # interleave with this small synchronous staging section.
    logs_before, wonders_before = list(E._GAME_LOG), list(E._WONDER_QUEUE)
    E._GAME_LOG.clear()
    E._WONDER_QUEUE.clear()
    try:
        if old and old.playing():
            old.act_leave(staged)
        staged["active_delve"] = None
        delve = (E.start_soulcairn(staged, channel_id) if kind == "soulcairn"
                 else E.start_delve(staged, channel_id, loc_key, kind=kind))
        return PendingLaunch(staged, delve, baseline, source_board, old,
                             list(E._GAME_LOG), list(E._WONDER_QUEUE))
    finally:
        E._GAME_LOG[:] = logs_before
        E._WONDER_QUEUE[:] = wonders_before
