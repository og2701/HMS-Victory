"""The Pit and ghost duels as SkBout (docs/skyrim-activity-contract.md): ports of views.py's
_pit_board_layout / _handle_pit_click and _duel_board_layout / _handle_duel_click.

The engine plays a round per click (E.pit_action / E.duel_action) and clears the bout when it settles, so the
bout dict is held by reference across the call: the engine mutates it before it settles, which lets the hearts
before and after a round be compared (that is where the cues come from) even when the bout is gone afterwards.
"""

from __future__ import annotations

from lib.activities.skyrim_web import common
from lib.activities.skyrim_web.common import Refuse
from lib.activities.skyrim_web.panels_town import have_art, pit_art
from lib.features.skyrim import data as D
from lib.features.skyrim import engine as E

ARENAS = ("pit", "duel")
_MIN_PIT = 5
_HINTS = {"strike": "The reliable swing.",
          "power": "-15% to hit, but 2 damage.",
          "guard": "Halves the foe's hit chance and buys +10% on your next swing."}
_LABELS = {"strike": ("Strike", "⚔️"), "power": ("Power blow", "💥"), "guard": ("Guard", "🛡️")}


def _arena(arena: str) -> str:
    if arena not in ARENAS:
        raise Refuse("There's no such arena.")
    return arena


def _actions(live: bool) -> list:
    return [{"id": k, "label": _LABELS[k][0], "emoji": _LABELS[k][1], "hint": _HINTS[k], "disabled": not live}
            for k in ("strike", "power", "guard")]


def _foe(arena: str, profile: dict, state: str) -> tuple[dict, dict | None]:
    """(SkBout foe, the engine's record of them) for the bout, live or just settled."""
    if arena == "pit":
        b = E.pit_bout_active(profile)
        champ = D.PIT_CHAMPS[b["rank"]] if b else None
        if champ is None:
            return {"name": "", "title": "", "art": "pit", "hp": 0, "maxHp": 0, "quirk": None}, None
        return ({"name": champ["name"], "title": f"Bout {b['rank'] + 1} · {champ['style']}",
                 "art": pit_art(b["rank"]), "hp": max(0, b["foe"]), "maxHp": champ["hp"],
                 "quirk": common.clean(champ["quirk_desc"])}, champ)
    duel = profile.get("duel") or {}
    g = duel.get("ghost")
    if not g:
        return {"name": "", "title": "", "art": "pit", "hp": 0, "maxHp": 0, "quirk": None}, None
    return _ghost_foe(g, int((duel.get("bout") or {}).get("foe", g["hp"])), state), g


def _ghost_foe(g: dict, hp: int, state: str) -> dict:
    key = "duel_circle"
    settled = {"won": "duel_victory", "lost": "duel_defeat"}.get(state)
    if settled and have_art(settled):
        key = settled
    if not have_art(key):
        key = "pit"
    return {"name": g["name"], "title": f"Level {g['level']} · {g['style']}", "art": key, "hp": max(0, hp),
            "maxHp": int(g["hp"]), "quirk": common.clean(g["quirk_desc"])}


def _me(profile: dict, hp: int) -> dict:
    return {"name": profile.get("name") or "Adventurer", "hp": max(0, hp), "maxHp": E.heart_max(profile),
            "cut": f"hero_{profile.get('stone')}_idle"}


def _empty(arena: str, profile: dict, lines=(), cues=()) -> dict:
    return {"arena": arena, "state": "none", "round": 0, "me": _me(profile, 0),
            "foe": {"name": "", "title": "", "art": "pit", "hp": 0, "maxHp": 0, "quirk": None},
            "actions": _actions(False), "next": [], "lines": common.lines(list(lines)), "cues": list(cues),
            "hero": common.hero(profile)}


def _live(arena: str, profile: dict, lines=(), cues=()) -> dict:
    b = E.pit_bout_active(profile) if arena == "pit" else E.duel_bout_active(profile)
    if not b:
        return _empty(arena, profile, lines, cues)
    foe, _rec = _foe(arena, profile, "playing")
    notes = []
    if arena == "pit" and b.get("fatigue"):
        notes.append(f"-# 😮‍💨 Fighting tired: -{b['fatigue']}% to hit.")
    if b.get("staggered"):
        notes.append("-# 🛡️ Her shieldwall is closed - your next swing is at -15%." if arena == "pit"
                     else "-# 🛡️ The ghost's guard is closed - your next swing is at -15%.")
    if b.get("opening"):
        notes.append("-# 👁️ You see an opening - your next strike is at +10%.")
    return {"arena": arena, "state": "playing", "round": int(b["round"]), "me": _me(profile, b["me"]), "foe": foe,
            "actions": _actions(True), "next": [], "lines": common.lines(list(lines) + notes), "cues": list(cues),
            "hero": common.hero(profile)}


def _settled(arena, profile, state, b, foe, lines, cues, nxt=()) -> dict:
    """A bout that just ended: the engine has cleared it, so the numbers come from the dict held by reference."""
    return {"arena": arena, "state": state, "round": int(b["round"]), "me": _me(profile, b["me"]), "foe": foe,
            "actions": _actions(False), "next": list(nxt), "lines": common.lines(lines), "cues": list(cues),
            "hero": common.hero(profile)}


def bout(ctx: dict, arena: str) -> dict:
    """GET: the live bout, or state "none" when there isn't one."""
    arena = _arena(arena)
    return _live(arena, common.need(ctx["uid"]))


def _cues(action: str, lines: list, before: dict, after: dict, state: str) -> list:
    cues = []
    if action == "guard":
        cues.append({"t": "guard"})
    dealt = before["foe"] - max(0, after["foe"])
    taken = before["me"] - max(0, after["me"])
    crit = any("CRACKS" in ln for ln in lines)
    if dealt > 0:
        cues.append({"t": "hit", "dmg": int(dealt), "crit": crit, "style": "blade"})
    elif action != "guard":
        cues.append({"t": "miss", "style": "blade"})
    if taken > 0:
        cues.append({"t": "hurt", "hearts": int(taken), "crushing": any("crushing" in ln for ln in lines)})
    if state == "won":
        cues.append({"t": "kill", "crit": False, "boss": False})
    if state != "playing":
        cues.append({"t": "end", "state": state})
    return cues


async def bout_action(ctx: dict, arena: str, action: str, body: dict) -> dict:
    arena = _arena(arena)
    profile = common.need(ctx["uid"])
    if action in ("strike", "power", "guard"):
        return await _round(ctx, arena, profile, action)
    if arena == "pit" and action == "fight_on":
        if not (E.level(profile) >= _MIN_PIT and E.pit_available(profile)):
            raise Refuse("The Pit is done with you today - fresh legs at dawn.")
        intro = E.pit_begin(profile)
        E.save_profile(profile)
        await common.after(ctx, profile)
        return _live(arena, profile, intro)
    if arena == "pit" and action == "bank":
        return _empty(arena, profile, ["🛌 The day's winnings are banked - the crowd drinks to the one who "
                                       "knew when to stop."])
    raise Refuse("You can't do that here.")


async def _round(ctx: dict, arena: str, profile: dict, action: str) -> dict:
    b = E.pit_bout_active(profile) if arena == "pit" else E.duel_bout_active(profile)
    if not b:
        return _empty(arena, profile, ["There's no bout to fight."])      # a stale board, as views just defers
    foe, rec = _foe(arena, profile, "playing")
    before = {"me": int(b["me"]), "foe": int(b["foe"])}
    state, story = (E.pit_action if arena == "pit" else E.duel_action)(profile, action)
    profile["arena_log"] = ((profile.get("arena_log") or []) + story)[-20:]
    E.save_profile(profile)                  # (the duel also saved the rival: E._h2h does)
    await common.after(ctx, profile)
    after = {"me": int(b["me"]), "foe": int(b["foe"])}
    cues = _cues(action, story, before, after, state)
    if state == "playing":
        return _live(arena, profile, story, cues)
    if arena == "duel":
        foe = _ghost_foe(rec, 0 if state == "won" else after["foe"], state)
    else:
        foe = dict(foe, hp=max(0, after["foe"]))
    nxt = []
    if arena == "pit" and state == "won" and E.level(profile) >= _MIN_PIT and E.pit_available(profile):
        nxt = [{"id": "fight_on", "label": f"Fight on (-{E.pit_fatigue(profile)}% tired)"},
               {"id": "bank", "label": "Bank it and rest"}]
    return _settled(arena, profile, state, b, foe, story, cues, nxt)
