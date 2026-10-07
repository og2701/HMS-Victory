"""The town, the map and the delve itself: /skyrim as data (docs/skyrim-activity-contract.md).

Each function here is a port of a views.py screen that returns the same decisions the Discord buttons make, with
the engine as the only source of rules. Nothing in this module rolls dice or edits a profile except through the
engine and the sessions prepare/commit pair, exactly as the Discord layer does.

Animation cues are worked out afterwards by comparing the delve and profile before and after a turn plus the
log lines the engine wrote (the engine never says "that was a crit", so the crit lines in D.CRIT_LINES are the
signal). They are best-effort: the state in SkDelve is always right, a cue is only a hint for the client.
"""

from __future__ import annotations

import copy
import os
import time
import zlib

from lib.activities.skyrim_web import common
from lib.features.skyrim import data as D
from lib.features.skyrim import engine as E
from lib.features.skyrim import progression as P
from lib.features.skyrim import sessions

_ASSET_DIR = os.path.join("data", "skyrim")
_STORY_ART = {"captive": "story_captive", "brazier": "story_brazier", "runes": "story_runes"}

# Where a delve ended is remembered for a reload straight after (the board itself is deleted when it ends).
_ENDED: dict[int, tuple[float, dict]] = {}
_ENDED_KEEP = 3600
# Players who pressed "abandon and delve anew": the map hides the old run, launching is what really leaves it.
_ABANDONING: set[int] = set()

_GOAL_PANELS = {"notice": "notice", "perks": "perks", "shop": "shop", "alchemy": "alchemy",
                "factions": "factions", "hall": "hall", "sheet": "character"}

_EVENT_CHOICES = {      # views._EVENT_CHOICES: (emoji, label, choice)
    "chest": [("🧰", "Open it", "open"), ("🚶", "Move on", "skip")],
    "sweetroll": [("🍩", "Take the sweetroll", "take"), ("🚶", "Walk away", "skip")],
    "shrine": [("🙏", "Pray", "pray"), ("🚶", "Move on", "skip")],
    "satchel": [("🧪", "Take it", "take"), ("🚶", "Move on", "skip")],
    "maiq": [("💬", "Talk to M'aiq", "talk"), ("🚶", "Move on", "skip")],
    "wordwall": [("🗣️", "Approach the wall", "approach"), ("🚶", "Move on", "skip")],
    "giant": [("🚶", "Back away slowly", "retreat"), ("🧀", "About that cheese...", "approach")],
    "knee_trap": [("🚶", "Limp onward", "continue")],
    "fork": [("🪙", "The deep way", "deep"), ("🚶", "The safe way", "safe")],
    "fallen": [("💰", "Loot the satchel", "loot"), ("⚰️", "Lay them to rest", "honor")],
    "stray": [("🐾", "Befriend it", "befriend"), ("🚶", "Shoo it home", "skip")],
    "mudcrab": [("🦀", "Trade with the crab", "trade"), ("🚶", "Move on", "skip")],
    "nazeem": [("😤", "\"Yes, actually.\"", "yes"), ("😮‍💨", "Sigh deeply", "sigh")],
    "adoring_fan": [("🤩", "Let him follow", "adopt"), ("👉", "Send him home", "skip")],
}
_EVENT_NAMES = {
    "chest": "A chest", "sweetroll": "A sweetroll", "shrine": "A shrine", "satchel": "An alchemist's satchel",
    "maiq": "M'aiq the Liar", "knee_trap": "A tripwire", "giant": "A giant's camp", "mudcrab": "A mudcrab",
    "nazeem": "Nazeem", "fork": "A fork in the road", "fallen": "A fallen adventurer", "stray": "A stray",
    "adoring_fan": "The Adoring Fan", "wordwall": "A Word Wall",
}
_SHOUT_EFFECTS = {      # views._SHOUT_EFFECTS
    1: ("FUS", "Ground a dragon, or stagger a foe (1 charge)"),
    2: ("FUS RO", "2 damage; dragons are grounded and lose 1 HP (2 charges)"),
    3: ("FUS RO DAH", "The full Thu'um: 2 damage to anything (3 charges)"),
}
_BUFF_NAMES = {"heart": "Vigor", "soak": "Fortitude", "fight": "Fury", "crit": "True Shot", "loot": "Haul"}
_SOAK_MARKS = ("armour turns the blow", "(wound absorbed", "ally catches the blow", "glances off your greave")
_WARD_MARKS = ("ward absorbs", "ward flares")


# ---- small shared pieces ----------------------------------------------------------------------------------

def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _art_exists(name: str) -> bool:
    return any(os.path.exists(os.path.join(_ASSET_DIR, f"{name}.{ext}")) for ext in ("webp", "png"))


def _weather() -> dict:
    w = E.weather_today()
    return {"key": w["key"], "name": common.clean(w["name"]), "line": common.clean(E.weather_line(w))}


def _stamina_line(profile) -> str:
    """views._stamina_line without the Discord countdown stamp the page can't tick."""
    left, cap = E.delves_left(profile), E.delve_cap(profile)
    bit = f"{left}/{cap} delve{'s' if left != 1 else ''} ready"
    at = E.next_delve_at(profile)
    return bit + (f" · next {common.clean(f'<t:{at}:R>')}" if at else "")


def _live_delve(profile):
    mid = profile.get("active_delve") if profile else None
    delve = E.load_delve(mid) if mid else None
    return delve if delve is not None and delve.playing() else None


def _active(profile) -> dict | None:
    live = _live_delve(profile)
    if live is None:
        return None
    return {"id": int(live.message_id), "location": common.clean(live.loc["name"]), "room": live.idx + 1,
            "rooms": None if live.kind == "soulcairn" else len(live.rooms)}


def _goal(profile, live) -> dict | None:
    """P.next_goal as a client action: views._goal_callback's mapping, with 'delve' for resuming a run."""
    goal = P.next_goal(profile)
    if not goal:
        return None
    action = _GOAL_PANELS.get(goal.get("action"), "adventure")
    if action == "adventure" and live is not None:
        action = "delve"
    return {"text": common.clean(goal["text"]), "action": action}


# ---- the town ---------------------------------------------------------------------------------------------

def _spot(label: str, glow: bool = False, badge: str | None = None, locked: str | None = None) -> dict:
    out = {"label": label, "glow": bool(glow) and not locked, "locked": locked}
    if badge:
        out["badge"] = badge
    return out


def _town(profile) -> dict:
    """views._hub_rows: which buildings are lit (the green buttons) and which are shut."""
    if profile is None:
        shut = "Make your character first."
        return {k: _spot(v, locked=shut) for k, v in (
            ("adventure", "Adventure"), ("character", "Character"), ("shop", "Belethor's"),
            ("notice", "Notice Board"), ("pit", "The Pit"), ("factions", "Factions"),
            ("holdings", "Holdings"), ("hall", "Hall of Legends"), ("rankings", "Rankings"))}
    pts = E.perk_points(profile)
    daily = E.daily_available(profile)
    claimable = E.tasks_claimable(profile)
    notice_hot = daily or claimable or E.wb_available(profile) or bool(E.wb_share_waiting(profile))
    mood_emoji = D.DAILY_MOODS[E.daily_mood()]["emoji"]
    notice_badge = "Claim!" if claimable else "Daily" if daily else "Boss" if notice_hot else None
    pit_locked = None if E.level(profile) >= 5 else "The Pit opens at level 5."
    hs = E.homestead(profile)
    hold_ready = (any(E.expedition_ready(profile, s) for s in E.expedition_slots(profile))
                  or E.homestead_yield_days(profile) > 0
                  or bool(hs.get("building") and E.homestead_hours_left(profile) <= 0))
    ready, _line = E.retire_ready(profile)
    hall_open = bool(ready or E.legacy_rank(profile) or profile.get("alduin_slain"))
    fac_ready = E.faction_claimable(profile)
    return {
        "adventure": _spot("First adventure" if E.tutorial_available(profile) else "Adventure", glow=True),
        "character": _spot("Character", badge=f"{pts} perk{'s' if pts != 1 else ''}" if pts else None),
        "shop": _spot("Belethor's"),
        "notice": _spot(f"Notice Board {mood_emoji}".strip() if daily else "Notice Board", glow=notice_hot,
                        badge=notice_badge),
        "pit": _spot("The Pit", glow=E.pit_available(profile), locked=pit_locked),
        "factions": _spot("Factions", glow=fac_ready, badge="Claim!" if fac_ready else None),
        "holdings": _spot("Holdings", glow=hold_ready, badge="Ready" if hold_ready else None),
        "hall": _spot("Hall of Legends", glow=bool(ready), badge="Retirement awaits" if ready else None,
                      locked=None if hall_open else "The Hall opens once Alduin has fallen."),
        "rankings": _spot("Rankings"),
    }


def _perk_lines(stone: dict) -> list:
    names = {**{k: v["name"] for k, v in D.STYLES.items()}, "sneak": "Sneak", "lockpicking": "Lockpicking",
             "speech": "Speech"}
    boost = [names.get(k, k.title()) for k in stone["boost"]]
    start = [f"{names.get(k, k.title())} {v}" for k, v in stone["start"].items()]
    return [f"{', '.join(boost)} grow{'s' if len(boost) == 1 else ''} faster as you use "
            f"{'it' if len(boost) == 1 else 'them'}.",
            f"You start with {', '.join(start)}.",
            "Every skill stays open to you whatever you choose."]


def _class_pick() -> dict:
    return {"intro": common.lines(D.INTRO_TEXT) + ["Choose a blessing, then try a short guided adventure.",
                                                   "Your first adventure is free. Skills and gear stay yours when you fall."],
            "stones": [{"key": k, "name": s["name"], "emoji": s["emoji"], "blurb": s["blurb"], "perks": _perk_lines(s)}
                       for k, s in D.STONES.items()]}


def _home_data(profile) -> dict:
    live = _live_delve(profile) if profile else None
    dragon = D.DRAGON_ROSTER.get(E.dragon_of_the_week(), {})
    return {
        "hero": common.hero(profile),
        "classPick": None if profile else _class_pick(),
        "weather": _weather(),
        "dragonOfWeek": common.clean(dragon.get("name", "")),
        "activeDelve": _active(profile) if profile else None,
        "town": _town(profile),
        "goal": _goal(profile, live) if profile else None,
        "tutorial": bool(profile and E.tutorial_available(profile)),
    }


async def home(ctx) -> dict:
    """GET /skyrim: the town, or the class pick for someone with no character."""
    profile = common.load(ctx["uid"], ctx.get("name"))
    data = _home_data(profile)
    await common.after(ctx, profile)
    return data


def create(ctx, stone) -> dict:
    """POST /skyrim/create: touch a Guardian Stone (once)."""
    if stone not in D.STONES:
        raise common.Refuse("That isn't one of the three stones.")
    if E.get_profile(ctx["uid"]) is not None:
        raise common.Refuse("You already have a character.")
    E.create_profile(ctx["uid"], ctx.get("name") or "Adventurer", stone)
    return _home_data(E.get_profile(ctx["uid"]))


# ---- the map ----------------------------------------------------------------------------------------------

def _location(profile, key: str) -> dict:
    loc = E.location_data(key)
    try:
        sessions._validate(profile, key, "normal")          # the one place the rules for a road live
        locked = None
    except ValueError as e:
        locked = str(e)
    rc = E.route_condition(key)
    route = (f"{D.ROUTE_CONDITIONS[rc]['emoji']} {D.ROUTE_CONDITIONS[rc]['name']}: {D.ROUTE_CONDITIONS[rc]['short']}"
             if rc else None)
    tomorrow = None
    if E.homestead_built(profile, "watchtower"):
        rc2 = E.route_condition(key, E._date_plus(1))
        tomorrow = (f"{D.ROUTE_CONDITIONS[rc2]['emoji']} {D.ROUTE_CONDITIONS[rc2]['name']}" if rc2
                    else "A plain road")
    rank = E.stirred_rank(profile, key)
    return {"key": key, "name": common.clean(loc["name"]), "emoji": loc.get("emoji", ""),
            "band": loc["difficulty"], "rooms": loc["rooms"], "blurb": common.clean(loc["desc"]),
            "minLevel": int(loc.get("min_level", 1)), "drops": common.clean(E.location_drops(key)),
            "route": route, "routeTomorrow": tomorrow,
            "stirred": {"rank": rank, "name": E.stirred_name(rank)} if rank else None,
            "kind": "normal", "locked": locked}


def _offers_data(ctx, profile) -> dict:
    uid = int(profile["user_id"])
    left = E.delves_left(profile)
    resting = left <= 0
    live = _live_delve(profile)
    if live is None:
        _ABANDONING.discard(uid)
    active = None if uid in _ABANDONING else _active(profile)

    daily_loc = E.daily_location()
    daily_ok = E.daily_available(profile)
    mood = D.DAILY_MOODS[E.daily_mood()]
    ready, req_line = E.alduin_ready(profile)
    if E.alduin_available(profile):
        alduin = {"available": True, "reason": "Alduin's path is open."}
    elif ready:
        alduin = {"available": False, "reason": "Alduin waits at Skuldafn - one attempt per day. Return tomorrow."}
    elif E.level(profile) >= 12:
        alduin = {"available": False, "reason": common.clean(
            f"The World-Eater will meet you when you are ready: {req_line}.")}
    else:
        alduin = None
    soulcairn = ({"available": E.soulcairn_available(profile), "best": E.soulcairn_best(profile)}
                 if E.soulcairn_unlocked(profile) else None)
    legends = [] if resting else [
        {"key": D.RUMOURS[rk]["loc"], "name": common.clean(D.LOCATIONS[D.RUMOURS[rk]["loc"]]["name"])}
        for rk in E.heard_rumours(profile)[:3]]

    stock = E.elixir_stock(profile)
    elixirs = None
    if stock and not resting:
        elixirs = {"stock": [{"key": k, "name": D.RECIPES[k]["name"], "emoji": D.RECIPES[k]["emoji"], "count": n,
                              "blurb": common.clean(D.RECIPES[k]["desc"])} for k, n in stock.items()],
                   "chosen": [k for k in (profile.get("nextelixirs") or []) if k in stock]}
    pacts = None
    if E.level(profile) >= E.PACT_MIN_LEVEL and not resting:
        pacts = {"open": True,
                 "options": [{"key": k, "name": p["name"],
                              "blurb": common.clean(f"{p.get('mult_note') or 'x%g' % p['mult']} - {p['desc']}")}
                             for k, p in D.PACTS.items()],
                 "sworn": [k for k in (profile.get("nextpacts") or []) if k in D.PACTS]}
    return {
        "hero": common.hero(profile), "weather": _weather(),
        "resting": resting, "restUntil": int(E.next_delve_at(profile) or 0) if resting else 0,
        "delvesLeft": left, "delveCap": E.delve_cap(profile),
        "activeDelve": active,
        "canAbandon": bool(active and live is not None and "clavicus" not in live.pacts),
        "tutorial": E.tutorial_available(profile),
        "locations": [_location(profile, k) for k in E.offer_locations(profile)],
        "daily": {"key": daily_loc["key"], "name": common.clean(daily_loc["name"]),
                  "mood": common.clean(mood["name"]), "available": daily_ok, "done": not daily_ok},
        "alduin": alduin, "soulcairn": soulcairn, "legends": legends,
        "elixirs": elixirs, "pacts": pacts,
    }


def offers(ctx) -> dict:
    """GET /skyrim/offers: views._open_location_picker and _show_offers as data."""
    return _offers_data(ctx, common.need(ctx["uid"]))


def offers_action(ctx, action, body) -> dict:
    """POST /skyrim/offers/{action}: pick elixirs, swear pacts, or walk away from the run in progress."""
    profile = common.need(ctx["uid"])
    body = body or {}
    keys = [str(k) for k in (body.get("keys") or []) if isinstance(k, (str, int))]
    if action == "elixirs":
        E.select_elixirs(profile, keys)
        E.save_profile(profile)
    elif action == "pacts":
        err = E.swear_pacts(profile, keys)
        if err:
            raise common.Refuse(common.clean(err))
        E.save_profile(profile)
    elif action == "abandon":
        live = _live_delve(profile)
        if live is not None:
            if "clavicus" in live.pacts:
                raise common.Refuse("The Bargain seals the exit. Finish your current pact first.")
            _ABANDONING.add(int(profile["user_id"]))
    else:
        raise common.Refuse("You can't do that here.")
    return _offers_data(ctx, E.get_profile(ctx["uid"]))


# ---- the delve as data ------------------------------------------------------------------------------------

def _scene_art(delve) -> str:
    """views._scene_art: the scene key for the board."""
    if delve.state == "cleared":
        return "victory"
    if delve.state == "dead":
        return "death"
    if delve.state == "launched":
        return "giant"
    if delve.state in ("left", "fled", "abandoned"):
        return "leave"
    r = delve.room
    if r.get("hall_warden"):
        return "hall_rune_warden"
    if r.get("hall_event") in ("vault", "jammed", "ledger"):
        return "hall_sealed_vault"
    if delve.location == "lost_caravan" and r.get("story") == "captive":
        return "hall_lost_caravan"
    if r["kind"] == "enemy":
        e = D.ENEMIES[r["key"]]
        if e["type"] == "dragon":
            variant = f"{e['art']}_{'grounded' if delve.grounded else 'air'}"
            if _art_exists(variant):
                return variant
        return e["art"]
    if r["key"] == "fork":
        variant = _STORY_ART.get(r.get("story"))
        if variant and _art_exists(variant):
            return variant
    return D.EVENTS[r["key"]]["art"]


def _intro(delve, e, nd) -> str:
    """The room's arrival line. Picked from the delve and room so a reload reads the same."""
    intros = e["intro"]
    line = intros[zlib.crc32(f"{delve.delve_id}:{delve.idx}".encode()) % len(intros)]
    if nd:
        line = line.replace("**Dragon**", f"**{nd['name']}**")
    return line


def _room_view(delve, profile) -> dict:
    r = delve.room
    out = {"idx": delve.idx, "total": None if delve.kind == "soulcairn" else len(delve.rooms),
           "kind": r["kind"], "key": r["key"], "boss": bool(r.get("boss")), "art": _scene_art(delve),
           "cut": None, "affix": None, "bounty": bool(r.get("bounty")), "story": r.get("story"), "text": []}
    text = []
    if r["kind"] == "enemy":
        e = D.ENEMIES[r["key"]]
        nd = E.named_dragon(delve)
        name = "Rune Warden" if r.get("hall_warden") else nd["name"] if nd else e["name"]
        out["name"] = common.clean(name)
        out["cut"] = f"enemy_{e['art']}"
        if delve.engaged:
            text.append(f"{e['emoji']} The **{name}** presses the attack!")
        else:
            text.append(f"{e['emoji']} {_intro(delve, e, nd)}")
        if nd:
            text.append(f"🐲 **{nd['name']}** - {nd['twist']}")
        if r.get("affix"):
            aff = D.AFFIXES[r["affix"]]
            out["affix"] = {"name": common.clean(f"{aff['tag']} {e['name']}"), "blurb": common.clean(aff["desc"])}
            text.append(f"{aff['emoji']} **{aff['tag']} {e['name']}** - {aff['desc']}")
        if r.get("bounty"):
            title = D.BOUNTY_TITLES.get(e["type"], "Notorious")
            text.append(f"🏴 A **{title} {e['name']}** - a marked bounty. Tougher, but worth triple.")
        if e["type"] == "dragon" and not delve.grounded:
            text.append("☁️ **Airborne** - blades and fire barely reach it. Loose arrows, or **Shout** it out of the sky.")
        if delve.grounded:
            text.append("🪨 The dragon is **grounded** - every style bites now. Press it!")
        intent = E.combat_intent(delve)
        if intent and delve.hearts <= intent["max_wound"]:
            text.append(f"⚠️ A wound can cost {intent['max_wound']} HP; you have {delve.hearts}.")
        if delve.venom:
            text.append("🟢 **Venom in your blood** - drink before you leave this room.")
        if delve.ambush:
            text.append(f"🥷 You are **hidden and in position** - strike at +{E.AMBUSH_BONUS}%, or slip past unseen.")
    else:
        ev = D.EVENTS[r["key"]]
        story = E.story_text(delve) or ev["text"]
        if r["key"] == "fallen" and r.get("corpse"):
            who = r["corpse"].get("name", "a fallen soul")
            tag = " (their end is fresh)" if r["corpse"].get("real") else ""
            story = f"A body slumps against the wall - **{who}**{tag}, satchel still in cold hands."
        out["name"] = _EVENT_NAMES.get(r["key"], r["key"].replace("_", " ").capitalize())
        text.append(f"{ev['emoji']} {story}")
    if r.get("lesson"):
        text.append(f"💡 {r['lesson']}")
    hint = delve.next_hint()
    if hint:
        text.append(hint)
    out["text"] = common.lines(text)
    return out


def _enemy_view(delve) -> dict | None:
    if not delve.playing() or delve.room["kind"] != "enemy":
        return None
    r = delve.room
    e = D.ENEMIES[r["key"]]
    nd = E.named_dragon(delve)
    intent = E.combat_intent(delve)
    return {"key": r["key"], "name": common.clean("Rune Warden" if r.get("hall_warden") else nd["name"] if nd else e["name"]),
            "tier": e["tier"], "hp": delve.enemy_hp, "maxHp": max(delve.enemy_hp, delve._hp_for(r)),
            "intent": {"key": intent["key"], "label": common.clean(intent["label"]), "hint": common.clean(intent["hint"]),
                       "counter": intent["counter"] or "", "guardAvailable": bool(intent["guard_available"]),
                       "maxWound": intent["max_wound"], "guardHint": common.clean(intent["guard_hint"])},
            "airborne": e["type"] == "dragon" and not delve.grounded, "grounded": bool(delve.grounded)}


def _act(id, kind, label, emoji, style, pct=None, hint="", disabled=False) -> dict:
    return {"id": id, "kind": kind, "label": common.clean(label), "emoji": emoji, "pct": pct,
            "hint": common.clean(hint), "disabled": bool(disabled), "style": style}


def _actions(delve, profile) -> list:
    """The buttons views.build_delve_layout would draw for this state, as data."""
    if not delve.playing():
        return []
    room, out = delve.room, []
    if room["kind"] == "enemy":
        key = room["key"]
        enemy = D.ENEMIES[key]
        intent = E.combat_intent(delve)
        for skill, style in D.STYLES.items():
            crit = round(E.crit_chance(profile, key, skill, delve) * 100)
            out.append(_act(f"atk:{skill}", "attack", style["label"], style["emoji"], "danger",
                            pct=E.fight_pct(profile, key, skill, delve), hint=f"{crit}% critical on a hit"))
        if delve.ambush:
            out.append(_act("slp", "slip", "Slip past", "🥷", "primary", hint="Leave unseen for XP."))
        elif not delve.engaged:
            for token, kind, label, emoji, pct in (
                    ("snk", "sneak", "Sneak", "🥷", E.sneak_pct(profile, key, delve)),
                    ("per", "persuade", "Talk", "💬", E.persuade_pct(profile, key, delve))):
                if pct is not None:
                    out.append(_act(token, kind, label, emoji, "primary", pct=pct))
        out.append(_act("guard", "guard", "Guard", "🛡️", "primary", hint=intent.get("guard_hint", ""),
                        disabled=not intent.get("guard_available", True)))
        words = profile.get("words", 0)
        if words > 0 and delve.shout_charges > 0:
            grounded_dragon = enemy["type"] == "dragon" and delve.grounded
            for c in range(1, min(words, delve.shout_charges) + 1):
                if grounded_dragon and c == 1:          # FUS is wasted on a grounded dragon
                    continue
                name, desc = _SHOUT_EFFECTS[c]
                out.append(_act(f"sht:{c}", "shout", name, "🗣️", "success", hint=desc))
    else:
        key = room["key"]
        choices = E.story_choices(delve)
        if choices is None:
            choices = ([("🔓", "Pick lock", "pick"), ("🚶", "Move on", "skip")]
                       if key == "chest" and room.get("locked")
                       else _EVENT_CHOICES.get(key, [("🚶", "Move on", "skip")]))
        for emoji, label, choice in choices:
            pct = E.lockpick_pct(profile) if choice == "pick" else None
            out.append(_act(f"evt:{choice}", "event", label, emoji, "primary", pct=pct))
    if (profile["potions"] > 0 and "namira" not in delve.pacts
            and (delve.hearts < E.delve_heart_max(delve, profile) or delve.venom)):
        out.append(_act("pot", "potion", "Heal +1", "🧪", "success",
                        hint="Restores a heart" + (" and cures venom." if delve.venom else ".")))
    if "clavicus" not in delve.pacts and room["key"] != "giant":
        kept = int(delve.satchel * E.pact_mult(delve) * (E.FLEE_KEEP if delve.engaged else 1))
        out.append(_act("lve", "leave", f"{'Flee' if delve.engaged else 'Bank'} {kept:,}",
                        "🏃" if delve.engaged else "🚪", "secondary",
                        hint="Run: the rest of the satchel spills." if delve.engaged else "Leave with the satchel."))
    return out


def _result(delve, profile) -> dict | None:
    """views._debrief_text as data."""
    if delve.playing():
        return None
    summary = getattr(delve, "summary", None) or {}
    ending = {"cleared": "Dungeon cleared", "dead": "You fell", "left": "Safely home",
              "fled": "Escaped the fight", "launched": "An unexpected flight"}.get(delve.state, "Adventure complete")
    out = [f"{ending} · {delve.loc['name']}"]
    if summary and "banked_gold" in summary:
        banked, lost = int(summary.get("banked_gold", 0)), int(summary.get("lost_gold", 0))
        out.append(f"💰 **{banked:,} banked**" + (f" · {lost:,} lost" if lost else ""))
        stored = sum(max(0, int(n)) for n in (summary.get("banked_ingredients") or {}).values())
        dropped = sum(max(0, int(n)) for n in (summary.get("lost_ingredients") or {}).values())
        if stored or dropped:
            out.append(f"🌿 {stored} ingredients banked" + (f" · {dropped} lost" if dropped else ""))
    out.append(f"✨ {delve.xp_gained} XP · {delve.kills} kills")
    boons, deeds = summary.get("hall_boons") or [], summary.get("hall_deeds") or []
    if boons:
        out.append("🏛️ **Boon unlocked:** " + ", ".join(D.HALL_BOONS[k]["name"] for k in boons))
    elif deeds:
        out.append(f"🏛️ {len(deeds)} new Hall deed{'s' if len(deeds) != 1 else ''}")
    if summary.get("story_result"):
        out.append("📜 " + summary["story_result"])
    gains = summary.get("skill_gains") or {}
    bits = [f"{D.STYLES.get(k, {}).get('name', k.title())} +{v}" for k, v in gains.items() if v > 0]
    if bits:
        out.append("📈 " + ", ".join(bits))
    start = summary.get("start") or {}
    before, now = start.get("level", E.level(profile)), E.level(profile)
    opened = [name for at, name in ((5, "Pit"), (8, "Factions"), (10, "Pacts"), (20, "Alduin's path"))
              if before < at <= now]
    for skill, old in (start.get("skills") or {}).items():
        if old < 100 <= profile["skills"].get(skill, 0):
            opened.append(f"{D.STYLES.get(skill, {}).get('label', skill.title())} mastery")
    if profile.get("words", 0) > start.get("words", profile.get("words", 0)):
        opened.append("a Word of Power")
    if opened:
        out.append("🔓 " + ", ".join(opened) + " unlocked")
    task_changes = summary.get("task_gains") or {}
    if task_changes:
        out.append(f"📋 Progress on {len(task_changes)} task{'s' if len(task_changes) != 1 else ''}")
    elif E.tasks_claimable(profile):
        out.append("🎁 Task rewards ready on the Notice Board")
    out.append(_stamina_line(profile))
    return {"line": common.clean(delve.result_line), "summary": common.lines(out),
            "goal": _goal(profile, None)}


def _buffs(delve) -> list:
    out = [f"{_BUFF_NAMES.get(k, k.title())} +{v}" for k, v in delve.buffs.items() if v]
    return out + (["Adoring Fan"] if delve.fan else [])


def delve_view(delve, profile) -> dict:
    """SkDelve for a board and the profile it belongs to."""
    loc = delve.loc
    return {
        "id": int(delve.message_id), "rev": int(delve.revision), "kind": delve.kind, "state": delve.state,
        "location": {"key": delve.location, "name": common.clean(loc["name"]), "band": loc["difficulty"]},
        "depth": int(delve.depth),
        "room": _room_view(delve, profile),
        "enemy": _enemy_view(delve),
        "you": {"hearts": max(0, delve.hearts), "maxHearts": E.delve_heart_max(delve, profile),
                "satchel": delve.satchel, "potions": int(profile["potions"]), "shoutCharges": delve.shout_charges,
                "venom": delve.venom, "blessed": delve.blessed, "engaged": delve.engaged, "ambush": delve.ambush,
                "spotted": delve.spotted, "buffs": _buffs(delve),
                "pacts": [D.PACTS[k]["name"] for k in delve.pacts if k in D.PACTS]},
        "actions": _actions(delve, profile),
        "log": common.lines(delve.log),
        "result": _result(delve, profile),
        "xpGained": delve.xp_gained, "kills": delve.kills,
    }


def _turn(delve, profile, cues=(), toasts=()) -> dict:
    return {"delve": delve_view(delve, profile), "cues": list(cues), "hero": common.hero(profile),
            "toasts": list(toasts)}


# ---- launching and resuming -------------------------------------------------------------------------------

_FIXED_LOC = {"alduin": "skuldafn", "soulcairn": "soul_cairn", "tutorial": "embershard"}


def _synthetic_id() -> int:
    """A negative id for a run with no Discord message, unique among the stored boards."""
    views = E.load_persistent_views()
    mid = -(time.time_ns() // 1000)
    while str(mid) in views:
        mid -= 1
    return mid


async def launch(ctx, loc, kind) -> dict:
    """POST /skyrim/launch: views._launch_delve without a message to post."""
    uid = int(ctx["uid"])
    kind = kind or "normal"
    if kind == "daily":
        loc = E.daily_location()["key"]
    elif kind in _FIXED_LOC:
        loc = _FIXED_LOC[kind]
    async with sessions.launch_lock(uid):
        profile = E.get_profile(uid)
        try:
            pending = sessions.prepare(profile, _int_or_none(ctx.get("ch")), loc, kind)
            pending.commit(_synthetic_id())
        except ValueError as e:
            raise common.Refuse(str(e))
        except OSError:
            raise common.Refuse("Your adventure could not be saved. Nothing was spent; try again.")
    _ABANDONING.discard(uid)
    _ENDED.pop(uid, None)
    await common.after(ctx, pending.profile)
    return _turn(pending.delve, pending.profile, [{"t": "advance", "room": 0}])


def current(ctx) -> dict | None:
    """GET /skyrim/delve: the run in progress, or (once, just after it ended) how it ended."""
    uid = int(ctx["uid"])
    profile = E.get_profile(uid)
    if profile is None:
        return None
    delve = _live_delve(profile)
    if delve is not None:
        return _turn(delve, profile)
    kept = _ENDED.pop(uid, None)
    if kept and time.time() - kept[0] < _ENDED_KEEP:
        return {**kept[1], "cues": [], "toasts": []}
    return None


# ---- one turn ---------------------------------------------------------------------------------------------

def _stale(message: str, ctx) -> common.Stale:
    exc = common.Stale(message)
    exc.turn = current(ctx)
    return exc


def _new_lines(before: list, after: list) -> list:
    """What the engine added to the history: the part of `after` past its longest overlap with the end of `before`."""
    for n in range(min(len(before), len(after)), 0, -1):
        if before[len(before) - n:] == after[:n]:
            return after[n:]
    return list(after)


def _cues(action: str, b: dict, a: dict, bp: dict, ap: dict) -> tuple[list, list]:
    """(cues, toasts) for a turn, from the delve and profile before (b, bp) and after (a, ap) it."""
    lines = _new_lines(b.get("history") or [], a.get("history") or [])
    blob = "\n".join(lines)
    kind, _, arg = action.partition(":")
    style = arg if kind == "atk" and arg in D.STYLES else "blade" if kind == "atk" else "shout"
    cues, toasts = [], []
    same_room = a["idx"] == b["idx"] and a["rooms"][a["idx"]]["kind"] == b["rooms"][b["idx"]]["kind"]
    killed = a["kills"] > b["kills"]
    playing = a["state"] == "playing"
    crit = any(c in blob for c in D.CRIT_LINES)
    room = b["rooms"][b["idx"]]

    if kind == "evt":
        cues.append({"t": "event", "key": room["key"], "choice": arg})
    if kind == "sht" and b["shout_charges"] > a["shout_charges"]:
        cost = b["shout_charges"] - a["shout_charges"]
        cues.append({"t": "shout", "cost": cost, "word": " ".join(D.SHOUT_WORDS[:cost])})

    if kind in ("atk", "sht"):
        staggered = any(s in blob for s in D.STAGGER_LINES + D.STAGGER_DRAGON_LINES)
        drop = b["enemy_hp"] - a["enemy_hp"] if same_room else 0
        warned = "press Attack again" in blob
        warded = any(m in blob for m in _WARD_MARKS)
        if killed:
            cues.append({"t": "hit", "dmg": max(1, b["enemy_hp"]), "crit": crit, "style": style})
            cues.append({"t": "kill", "crit": crit, "boss": bool(room.get("boss"))})
        elif drop > 0 or (staggered and not warded):
            cues.append({"t": "hit", "dmg": max(1, drop) if drop > 0 else 2 if crit else 1, "crit": crit,
                         "style": style})
        elif kind == "atk" and not warned:
            cues.append({"t": "miss", "style": style})
        if same_room:
            if not b["grounded"] and a["grounded"]:
                cues.append({"t": "grounded"})
            elif b["grounded"] and not a["grounded"]:
                cues.append({"t": "airborne"})
    elif kind == "snk":
        if a["ambush"] and not b["ambush"]:
            cues.append({"t": "sneak", "ok": True})
        elif a["spotted"] and not b["spotted"]:
            cues.append({"t": "sneak", "ok": False})
    elif kind == "per":
        if a["idx"] != b["idx"] or not playing:
            cues.append({"t": "persuade", "ok": True})
        elif a["engaged"] and not b["engaged"]:
            cues.append({"t": "persuade", "ok": False})
    elif kind == "slp":
        if a["idx"] != b["idx"] or not playing:
            cues.append({"t": "slip"})
    elif kind == "guard":
        if (not (room.get("combat") or {}).get("guard_used")
                and (a["rooms"][b["idx"]].get("combat") or {}).get("guard_used")):
            cues.append({"t": "guard"})

    lost = b["hearts"] - max(0, a["hearts"])
    if any(m in blob for m in _SOAK_MARKS):
        cues.append({"t": "soak"})
    if lost > 0:
        cues.append({"t": "hurt", "hearts": lost, "crushing": lost >= 2 or "crushing blow" in blob})
    gained = a["hearts"] - b["hearts"]
    if gained > 0 or (kind == "pot" and ap["potions"] < bp["potions"]):
        cues.append({"t": "heal", "hearts": max(0, gained)})

    new_items = [f"{D.INGREDIENTS.get(k, {}).get('name', k)}" for k, n in (a["ingredients"] or {}).items()
                 if n > (b["ingredients"] or {}).get(k, 0)]
    if ap["potions"] > bp["potions"] and kind != "pot":
        new_items.append("Health potion")
    coin = a["satchel"] - b["satchel"]
    if (coin > 0 or new_items) and kind != "lve" and a["state"] not in ("left", "fled", "launched"):
        cues.append({"t": "loot", "septims": max(0, coin), "items": new_items})
    if a["xp_gained"] > b["xp_gained"]:
        cues.append({"t": "xp", "amount": a["xp_gained"] - b["xp_gained"]})
    if E.level(ap) > E.level(bp):
        cues.append({"t": "level", "level": E.level(ap)})
        toasts.append(f"Level up! You are now level {E.level(ap)}.")
    if ap["souls"] > bp["souls"]:
        cues.append({"t": "soul"})
        toasts.append("A dragon soul is yours.")
    if ap["words"] > bp["words"]:
        word = D.SHOUT_WORDS[min(ap["words"], len(D.SHOUT_WORDS)) - 1]
        cues.append({"t": "word", "word": word})
        toasts.append(f"You learned the Word of Power: {word}.")
    for key in (ap.get("wonders") or []):
        if key not in (bp.get("wonders") or []) and key in D.WONDERS:
            name = common.clean(D.WONDERS[key]["name"])
            cues.append({"t": "wonder", "key": key, "name": name})
            toasts.append(f"A Wonder: {name}.")
    if a["idx"] != b["idx"] and playing:
        cues.append({"t": "advance", "room": a["idx"]})
    if b["state"] == "playing" and not playing:
        cues.append({"t": "end", "state": a["state"]})
    return cues, toasts


async def act(ctx, action, rev) -> dict:
    """POST /skyrim/act: views._handle_delve_click for a move, answered with what to animate."""
    uid = int(ctx["uid"])
    profile = common.need(uid)
    mid = profile.get("active_delve")
    board = E.load_delve(mid) if mid else None
    if board is None or not board.playing():
        kept = _ENDED.get(uid)
        if kept and time.time() - kept[0] < _ENDED_KEEP:
            exc = common.Stale("That adventure has ended.")
            exc.turn = current(ctx)
            raise exc
        raise common.Refuse("That adventure has ended.")
    if board.player_id != uid:
        raise common.Refuse("This is not your adventure.")
    if rev is not None and rev != board.revision:
        raise _stale("That choice belongs to an earlier turn.", ctx)
    before_d, before_p = copy.deepcopy(board.to_dict()), copy.deepcopy(profile)
    try:
        pending = sessions.prepare_action(profile, board, action, expected_revision=rev)
        pending.commit()
    except ValueError as e:
        if "earlier turn" in str(e) or "changed" in str(e):
            raise _stale(str(e), ctx)
        raise common.Refuse(str(e))
    except OSError:
        raise common.Refuse("That turn could not be saved. Your progress is safe; try again.")
    cues, toasts = _cues(action, before_d, pending.delve.to_dict(), before_p, pending.profile)
    turn = _turn(pending.delve, pending.profile, cues, toasts)
    if not pending.delve.playing():
        _ENDED[uid] = (time.time(), turn)
    await common.after(ctx, pending.profile)
    return turn
