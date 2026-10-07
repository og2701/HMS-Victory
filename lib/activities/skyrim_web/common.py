"""What every part of /skyrim in the activity shares: tidying the engine's Discord text for a web page, the
character at a glance, and the pieces a panel is built from (docs/skyrim-activity-contract.md).

Everything here runs on the bot's event loop, like the views it stands in for: the engine's saves are
read-modify-write on whole JSON files, so nothing may touch them from a thread.
"""

from __future__ import annotations

import re

from lib.features.skyrim import data as D
from lib.features.skyrim import engine as E


class Refuse(Exception):
    """Something the game won't do, with the reason to show the player (answered as 422)."""


class Stale(Exception):
    """The player acted on a turn that's already moved on (answered as 409 with the current turn)."""


# ---- text ------------------------------------------------------------------------------------------------

_MENTION = re.compile(r"<@!?(\d+)>")
_STAMP = re.compile(r"<t:(\d+)(?::[a-zA-Z])?>")
_CUSTOM_EMOJI = re.compile(r"<a?:\w+:\d+>")


def _ago(ts: int) -> str:
    import time
    left = int(ts) - int(time.time())
    if abs(left) < 60:
        return "now"
    mins = abs(left) // 60
    span = f"{mins} min" if mins < 60 else f"{mins // 60}h {mins % 60}m" if mins < 1440 else f"{mins // 1440} days"
    return f"in {span}" if left > 0 else f"{span} ago"


def clean(line, names: dict | None = None) -> str:
    """One line of engine text for the page: bold kept (the client draws it), the Discord-only bits rewritten."""
    if line is None:
        return ""
    s = str(line)
    s = _CUSTOM_EMOJI.sub("", s)
    s = _STAMP.sub(lambda m: _ago(int(m.group(1))), s)
    s = _MENTION.sub(lambda m: (names or {}).get(m.group(1)) or _name_of(m.group(1)), s)
    s = re.sub(r"^\s*(-#|#{1,3})\s+", "", s)
    s = s.replace("\\_", "_").replace("\\*", "*")
    return s.strip()


def lines(text_or_lines, names: dict | None = None) -> list[str]:
    """A block of engine text (one string with newlines, or a list) as clean, non-empty lines."""
    if not text_or_lines:
        return []
    raw = text_or_lines.split("\n") if isinstance(text_or_lines, str) else [x for t in text_or_lines for x in str(t).split("\n")]
    out = [clean(x, names) for x in raw]
    return [x for x in out if x]


def _name_of(uid: str) -> str:
    p = E.get_profile(int(uid)) if str(uid).isdigit() else None
    return (p or {}).get("name") or "someone"


# ---- the character at a glance -----------------------------------------------------------------------------

def hero(profile: dict | None) -> dict | None:
    """SkHero: the strip across the top of most screens."""
    if not profile:
        return None
    xp = int(profile.get("xp", 0))
    into, need = D.xp_into_level(xp)
    tiers = D.GEAR_TIERS
    wt, at = int(profile.get("weapon_tier", 0)), int(profile.get("armour_tier", 0))
    temper = profile.get("temper") or {}
    pet = E.active_companion(profile)
    fkey = profile.get("allegiance")
    stone = D.STONES.get(profile.get("stone"), {})
    return {
        "name": profile.get("name") or "Adventurer",
        "stone": profile.get("stone"),
        "stoneName": stone.get("name", ""),
        "archetype": E.archetype(profile),
        "level": E.level(profile),
        "xp": xp, "xpInto": into, "xpNeed": need,
        "septims": int(profile.get("septims", 0)),
        "potions": int(profile.get("potions", 0)),
        "potionCap": E.potion_cap(profile),
        "hearts": E.heart_max(profile),
        "weapon": {"name": E.gear_name(profile, "weapon"), "tier": wt, "tierName": tiers[min(wt, len(tiers) - 1)]["name"],
                   "temper": int(temper.get("weapon", 0))},
        "armour": {"name": E.gear_name(profile, "armour"), "tier": at, "tierName": tiers[min(at, len(tiers) - 1)]["name"],
                   "temper": int(temper.get("armour", 0)), "style": profile.get("armour_style", "heavy")},
        "souls": int(profile.get("souls", 0)),
        "words": int(profile.get("words", 0)),
        "voice": E.voice_charges(profile),
        "perkPoints": E.perk_points(profile),
        "streak": E.current_streak(profile),
        "delvesLeft": E.delves_left(profile),
        "delveCap": E.delve_cap(profile),
        "nextDelveAt": int(E.next_delve_at(profile) or 0),
        "companion": {"key": profile.get("companion"), "name": pet["name"], "art": pet.get("art")} if pet else None,
        "faction": ({"key": fkey, "name": D.FACTIONS[fkey]["name"], "rank": E.faction_rank(profile, fkey)}
                    if fkey in D.FACTIONS else None),
        "legacy": {"rank": E.legacy_rank(profile)},
    }


def load(uid: int, name: str | None = None) -> dict | None:
    """The player's profile (None if they've no character yet), with their name kept up to date."""
    profile = E.get_profile(uid)
    if profile is not None and name and profile.get("name") != name:
        profile["name"] = name
        E.save_profile(profile)
    return profile


def need(uid: int) -> dict:
    """The player's profile, or a refusal if they haven't made a character yet."""
    profile = E.get_profile(uid)
    if profile is None:
        raise Refuse("Make your character first.")
    return profile


# ---- after anything that changes the game ------------------------------------------------------------------

async def after(ctx: dict, profile: dict | None = None):
    """What views.py did after every interaction, for the Discord side: the engine's audit lines to the game-log
    thread, any Wonder found announced in the channel the activity was opened in, and server badges. All of it
    best-effort: none of it may break the game."""
    import logging
    from lib.features.skyrim import views as V
    client = ctx.get("client")
    await V._flush_game_log(client)
    try:
        found = E.drain_wonders()
        ch = client.get_channel(int(ctx["ch"])) if found and client is not None and ctx.get("ch") else None
        for user_id, key in found:
            if ch is None or not hasattr(ch, "send"):
                break
            p = E.get_profile(user_id)
            owned = len([k for k in (p.get("wonders") or []) if k in D.WONDERS]) if p else None
            import discord
            await ch.send(E.wonder_announcement(user_id, key, owned), allowed_mentions=discord.AllowedMentions(users=True))
    except Exception:
        logging.getLogger(__name__).debug("skyrim wonder announcement failed", exc_info=True)
    if profile is not None and client is not None:
        try:
            from lib.features.skyrim import badges
            await badges.award_skyrim_badges(client, profile)
        except Exception:
            logging.getLogger(__name__).debug("skyrim badge hook failed", exc_info=True)


# ---- panels ------------------------------------------------------------------------------------------------

def action(id: str, label: str, emoji: str = "", style: str = "secondary", disabled: bool = False,
           hint: str = "", nav: str | None = None, confirm: str | None = None) -> dict:
    return {"id": id, "label": clean(label), "emoji": emoji, "style": style, "disabled": bool(disabled),
            "hint": clean(hint), "nav": nav, "confirm": confirm}


def option(value: str, label: str, blurb: str = "", emoji: str = "", chosen: bool = False) -> dict:
    return {"value": str(value), "label": clean(label), "blurb": clean(blurb), "emoji": emoji, "chosen": bool(chosen)}


def select(id: str, placeholder: str, options: list, min: int = 1, max: int = 1) -> dict:
    return {"id": id, "placeholder": clean(placeholder), "min": min, "max": max, "options": options}


def stat(label: str, value, icon: str = "") -> dict:
    return {"label": label, "value": str(value), "icon": icon}


def tile(title: str, *, icon: str | None = None, value=None, cost: int | None = None, sub: str | None = None, meter=None, pips=None,
         state: str | None = None, act: str | None = None, body: dict | None = None, nav: str | None = None,
         confirm: str | None = None, badge: str | None = None, info: str | None = None) -> dict:
    """SkTile: one picture card on a screen, read at a glance and tapped to act. Keep words to a name (a few words),
    a value (a count, a rank), a cost in septims (drawn as coin) and at most a few words of sub. icon: "a:<key>" game icon art (public/skyrim/icon),
    "s:<key>" a scene, "c:<key>" a cut-out, "i:<name>" a drawn icon; "a:x|i:y" falls back to y. meter / pips:
    (n, of) as a bar / as diamonds. state: "ready" (something to do here), "locked", "done", "max" or None. A tap
    posts act (with body) to the screen, or goes to nav; a tile with neither, or a locked one, shows info."""
    return {"title": clean(title), "icon": icon, "value": None if value is None else clean(str(value)),
            "cost": None if cost is None else int(cost),
            "sub": clean(sub) if sub else None, "meter": [int(meter[0]), int(meter[1])] if meter else None,
            "pips": [int(pips[0]), int(pips[1])] if pips else None, "state": state, "act": act, "body": body or None,
            "nav": nav, "confirm": confirm, "badge": clean(badge) if badge else None, "info": clean(info) if info else None}


def section(title: str, body=(), tiles=None, cols: int = 2) -> dict:
    """A block of a screen: tiles (cols across) and/or a few short lines."""
    return {"title": clean(title), "lines": lines(body), "tiles": list(tiles or []), "cols": cols}


def panel(key: str, title: str, *, art: str | None = None, back: str | None = "town", blurb=(), stats=(),
          sections=(), actions=(), selects=()) -> dict:
    """SkPanel. The blurb is the screen's flavour and rules, kept behind an info button: lead with tiles."""
    return {"key": key, "title": clean(title), "art": art, "back": back, "blurb": lines(list(blurb)),
            "stats": list(stats), "sections": [s for s in sections if s and (s.get("lines") or s.get("tiles"))],
            "actions": list(actions), "selects": [s for s in selects if s and s.get("options")]}


def result(panel_: dict, profile: dict | None, toast: str | None = None, cues=(), nav: str | None = None) -> dict:
    """SkPanelResult."""
    return {"panel": panel_, "toast": clean(toast) if toast else None, "cues": list(cues), "nav": nav,
            "hero": hero(profile)}
