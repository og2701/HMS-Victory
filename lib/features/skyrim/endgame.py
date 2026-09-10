"""Hall deeds and authored adventures. No Discord, network or file I/O.

Callers commit profiles and adventure snapshots together. Boons are a finite,
separate catalogue; they never create server badges or currency payouts.
"""
from copy import deepcopy

from lib.features.skyrim import data as D

VERSION = 1


def ensure(profile):
    hall = profile.setdefault("hall", {})
    hall.setdefault("deeds", [])
    hall.setdefault("chapters", [])
    hall.setdefault("boons", [])
    hall.setdefault("stories", {})
    rank = int((profile.get("legacy") or {}).get("rank", 0))
    if rank > D.LEGACY_MAX:
        deed(profile, "reborn")
    if int((profile.get("soulcairn") or {}).get("best", 0)) >= 20:
        deed(profile, "cairn_20")
    return hall


def deed(profile, key):
    if key not in D.HALL_DEEDS:
        return
    owned = profile.setdefault("hall", {}).setdefault("deeds", [])
    if key not in owned:
        owned.append(key)


def settle(profile):
    hall = ensure(profile)
    if int((profile.get("legacy") or {}).get("rank", 0)) < D.LEGACY_MAX:
        return []
    awarded = []
    for chapter in D.HALL_CHAPTERS:
        if not set(chapter["deeds"]).issubset(hall["deeds"]):
            break  # Chapters are ordered; proofs can be earned in any order.
        if chapter["key"] not in hall["chapters"]:
            hall["chapters"].append(chapter["key"])
        boon = chapter["boon"]
        if boon not in hall["boons"]:
            hall["boons"].append(boon)
            awarded.append(boon)
    return awarded


def next_deed(profile):
    hall = ensure(profile)
    return next((key for chapter in D.HALL_CHAPTERS for key in chapter["deeds"]
                 if key not in hall["deeds"]), None)


def life_baseline(profile):
    """Recover only counters the latest retirement record can substantiate."""
    lg = profile.setdefault("legacy", {"rank": 0, "boons": [], "epitaphs": []})
    rank = int(lg.get("rank", 0))
    baseline = lg.get("life")
    kills = int(profile.get("alduin_slain", 0) or 0)
    dragons = int((profile.get("stats") or {}).get("dragons", 0))
    if (isinstance(baseline, dict) and baseline.get("rank") == rank
            and isinstance(baseline.get("alduin"), int) and 0 <= baseline['alduin'] <= kills
            and isinstance(baseline.get("dragons"), int) and 0 <= baseline['dragons'] <= dragons):
        return baseline
    ep = (lg.get("epitaphs") or [])[-1:] or [{}]
    ep = ep[0]
    valid = (len(lg.get("epitaphs") or []) == rank and rank > 0
             and isinstance(ep.get("alduin"), int) and 0 <= ep["alduin"] <= kills
             and isinstance(ep.get("dragons"), int) and 0 <= ep["dragons"] <= dragons)
    baseline = {"rank": rank, "alduin": ep["alduin"] if valid else kills,
                "dragons": ep["dragons"] if valid else dragons}
    lg["life"] = baseline
    return baseline


def life_counts(profile):
    baseline = life_baseline(profile)
    return (max(0, int(profile.get("alduin_slain", 0) or 0) - baseline["alduin"]),
            max(0, int(profile.get("stats", {}).get("dragons", 0)) - baseline["dragons"]))


def begin_life(profile):
    lg = profile["legacy"]
    lg["life"] = {"rank": int(lg["rank"]),
                  "alduin": int(profile.get("alduin_slain", 0) or 0),
                  "dragons": int(profile.get("stats", {}).get("dragons", 0))}


def unlocked(profile):
    from lib.features.skyrim import engine as E
    return E.level(profile) >= 20 and bool(profile.get("alduin_slain"))


def location(key):
    variant = D.HALL_ADVENTURES.get(key)
    if not variant:
        return None
    base = D.LOCATIONS[variant["base"]]
    return {**base, "name": variant["name"], "emoji": variant["emoji"],
            "desc": variant["rule"], "arrive": variant["rule"],
            "min_level": 20, "difficulty": "Hard", "rooms": 6, "events": 3,
            "word_wall": False, "clear_septims": 100, "hall_adventure": True}


def story_state(profile):
    from lib.features.skyrim import progression as P
    key = profile.get("allegiance")
    if key not in D.FACTION_STORIES or not unlocked(profile) or P.faction_rank_index(profile, key) < 4:
        return None
    saved = ensure(profile)["stories"].get(key) or {"stage": 0, "choice": None}
    if saved.get("complete"):
        return None
    stage = int(saved.get("stage", 0))
    if stage not in (0, 1):
        return None
    story = D.FACTION_STORIES[key]
    return {"faction": key, "stage": stage, "choice": saved.get("choice"),
            "name": story["name"], "road": story["roads"][stage]}


def featured(profile, date):
    if not unlocked(profile):
        return None
    story = story_state(profile)
    if story:
        return story["road"]
    missing = next_deed(profile)
    for key, variant in D.HALL_ADVENTURES.items():
        if missing in variant["deeds"]:
            return key
    import random
    return random.Random(f"skyrim-hall-{date}").choice(sorted(D.HALL_ADVENTURES))


def goal(profile):
    if not unlocked(profile):
        return None
    story = story_state(profile)
    if story:
        return f"{story['name']} ({story['stage'] + 1}/2): visit {D.HALL_ADVENTURES[story['road']]['name']}."
    key = next_deed(profile)
    if key == "reborn":
        return None  # The Hall's existing retirement readiness already routes this.
    if key == "faction_story":
        return None  # Existing faction/promotion goals lead to the required rank.
    return D.HALL_DEEDS.get(key)


def rooms(key):
    def enemy(name, boss=False, **extra):
        return {"kind": "enemy", "key": name, "boss": boss, "resolved": False, **extra}
    def event(name="chest", **extra):
        return {"kind": "event", "key": name, "boss": False, "resolved": False, **extra}
    if key == "rune_warden":
        return [enemy("draugr"), event(locked=True), enemy("draugr"), event("shrine"),
                event("fork", story="runes"),
                enemy("draugr_deathlord", True, hall_warden=True, hall_hp=6)]
    if key == "lost_caravan":
        return [enemy("bandit"), event(locked=True), enemy("bandit"), event("shrine"),
                event("fork", story="captive"),
                enemy("bandit_chief", True, hall_captain=True, hall_hp=4,
                      combat={"intent": "charge"})]
    if key == "sealed_vault":
        return [enemy("bandit"), event(locked=True), enemy("bandit"), event("shrine"),
                event("fork", hall_event="vault"), enemy("bandit_chief", True, hall_hp=4)]
    raise ValueError("Unknown Hall adventure.")


def prepare(profile, delve):
    """Freeze available boons and this story chapter before a board is posted."""
    delve.endgame = {"version": VERSION, "boons": list(ensure(profile)["boons"]), "used": []}
    if delve.location not in D.HALL_ADVENTURES:
        return
    delve.endgame["variant"] = delve.location
    story = story_state(profile)
    if not story or story["road"] != delve.location:
        return
    delve.endgame["story"] = deepcopy(story)
    if story["stage"] == 0:
        delve.rooms[1] = {"kind": "event", "key": "fork", "hall_event": "faction",
                          "boss": False, "resolved": False}
    else:
        spec = D.FACTION_STORIES[story["faction"]]
        careful = story["choice"] == "careful"
        effect = {"note": spec["careful" if careful else "bold"]}
        effect.update({"guard": True} if careful else {"damage": 1})
        delve.rooms[-1]["faction_effect"] = effect
        if not careful:
            delve.rooms[-1].setdefault("combat", {})["intent"] = "charge"
            if delve.location == "sealed_vault":
                delve.endgame["alarm"] = True
        delve.say(f"📜 {story['name']}, final chapter: {effect['note']}")


def check(profile, delve, skill, chance, rng):
    """One ordinary roll; a learned boon may retry the first failure in its skill."""
    if rng.random() * 100 < chance:
        return True
    key = {"lockpicking": "steady_hands", "speech": "silver_tongue", "sneak": "quiet_step"}.get(skill)
    state = delve.endgame
    if key and key in state.get("boons", []) and key not in state.get("used", []):
        state.setdefault("used", []).append(key)
        delve.say(f"{D.HALL_BOONS[key]['emoji']} {D.HALL_BOONS[key]['name']} gives you one more chance.")
        return rng.random() * 100 < chance
    return False


def ward_hit(delve, style):
    if not delve.room.get("hall_warden"):
        return False
    state = delve.room.setdefault("hall_ward", {"style": None, "landed": []})
    if state["style"] == style:
        delve.say(f"📜 The ward absorbs {D.STYLES[style]['label']}. Land a different weapon to break it.")
        return True
    state["style"] = style
    if style not in state["landed"]:
        state["landed"].append(style)
    return False


def choices(delve):
    kind = delve.room.get("hall_event")
    if kind == "faction":
        story = D.FACTION_STORIES[delve.endgame["story"]["faction"]]
        return [("📜", label, "hall:" + key) for label, key in story["choices"]]
    if kind == "vault":
        return [("🗝️", "Pick lock", "hall:lock"), ("💬", "Negotiate", "hall:talk"),
                ("⚔️", "Force entry", "hall:force")]
    if kind == "jammed":
        return [("⏳", "Wait out patrol", "hall:wait"), ("⚔️", "Force entry", "hall:force")]
    if kind == "ledger":
        return [("📜", "Take ledger", "hall:ledger")]
    return None


def text(delve):
    kind = delve.room.get("hall_event")
    if kind == "faction":
        story = D.FACTION_STORIES[delve.endgame["story"]["faction"]]
        return story["opening"] + "\n-# " + story["preview"]
    result = {
        "vault": "The ledger lies behind a guarded door. Pick its lock, negotiate entry, or fight the captain.",
        "jammed": "The patrol is coming. Waiting opens a quiet route but leaves 40 fewer septims; forcing entry raises the alarm.",
        "ledger": "The guard has moved on. The ledger is yours to recover.",
    }.get(kind)
    if result and delve.endgame.get("alarm"):
        result += "\n-# Your earlier choice already raised the alarm. A quiet deed is unavailable this run."
    return result


def event(profile, delve, action, rng):
    from lib.features.skyrim import engine as E
    offered = choices(delve)
    if not offered or action not in {item[2] for item in offered}:
        return
    action = action.split(":", 1)[1]
    if delve.room["hall_event"] == "faction":
        delve.endgame["story"]["selected"] = action
        delve.say("📜 Your decision will shape the next chapter if you bring this adventure home.")
    elif action == "ledger":
        delve.endgame["ledger"] = True
        delve.say("📜 You recover the ledger. Its names are safe.")
    elif action in ("lock", "talk"):
        skill = "lockpicking" if action == "lock" else "speech"
        chance = E.lockpick_pct(profile) if action == "lock" else E._clamp(
            35 + E._skill_component(profile["skills"]["speech"], 45))
        success = check(profile, delve, skill, chance, rng)
        # One practice allowance across both choices, including a failed approach.
        E.C.practice(profile, delve.room, skill, success, D.STONES)
        delve.endgame["approach"] = action
        if not success:
            delve.room["hall_event"] = "jammed"
            delve.say("The approach fails. You can still wait for a quiet opening.")
            return
        _quiet_route(delve)
        delve.endgame["negotiated"] = action == "talk"
        delve.say("The door opens quietly. The ledger waits beyond.")
    elif action == "wait":
        delve.endgame["waited"] = True
        _quiet_route(delve)
        delve.say("You wait. The patrol leaves, taking 40 septims from the vault with it.")
    elif action == "force":
        delve.endgame["alarm"] = True
        delve.endgame["approach"] = "force"
        delve.say("The alarm sounds. The captain bars your way to the ledger.")
    delve._advance(profile)


def _quiet_route(delve):
    delve.rooms[-1] = {"kind": "event", "key": "fork", "hall_event": "ledger",
                       "boss": False, "resolved": False}


def guard(delve, intent):
    if delve.room.get("hall_captain") and intent == "charge":
        delve.endgame["guarded_charge"] = True


def finish(profile, delve):
    if delve.state != "cleared" or delve.endgame.get("settled"):
        return
    before = set(ensure(profile)["deeds"])
    variant = delve.endgame.get("variant")
    if variant == "rune_warden" and delve.endgame.get("warden_killed"):
        deed(profile, "warden_clear")
        boss = next(r for r in delve.rooms if r.get("hall_warden"))
        if set((boss.get("hall_ward") or {}).get("landed", [])) == set(D.STYLES):
            deed(profile, "warden_styles")
    elif variant == "lost_caravan":
        rescued = any(s.get("story") == "captive" and s.get("choice") == "story_help"
                      for s in delve.summary.get("stories", []))
        if rescued:
            deed(profile, "caravan_rescue")
            if delve.endgame.get("guarded_charge"):
                deed(profile, "caravan_guard")
    elif variant == "sealed_vault" and delve.endgame.get("ledger") and not delve.endgame.get("alarm"):
        deed(profile, "vault_quiet")
        if delve.endgame.get("negotiated"):
            deed(profile, "vault_parley")
    story = delve.endgame.get("story")
    current = story_state(profile)
    if story and current and (story["faction"], story["stage"]) == (current["faction"], current["stage"]):
        if story["stage"] == 0 and story.get("selected") in ("careful", "bold"):
            ensure(profile)["stories"][story["faction"]] = {"stage": 1, "choice": story["selected"]}
            delve.summary["story_result"] = f"{story['name']}: chapter 1 complete."
            delve.say(f"📜 {story['name']}: the next chapter is ready in Adventure.")
        elif story["stage"] == 1 and story.get("choice") == current.get("choice"):
            ensure(profile)["stories"][story["faction"]]["complete"] = True
            deed(profile, "faction_story")
            title = D.FACTION_STORIES[story["faction"]]["title"]
            titles = profile.setdefault("titles", [])
            if title not in titles:
                titles.append(title)
            delve.summary["story_result"] = f"{story['name']} complete · {title}"
            delve.say(f"📜 {story['name']} is complete. You are {title}.")
    awarded = settle(profile)
    for boon in awarded:
        delve.say(f"🏛️ Hall deed complete: {D.HALL_BOONS[boon]['name']} is yours for future adventures.")
    if awarded:
        delve.summary["hall_boons"] = awarded
    delve.summary["hall_deeds"] = [key for key in ensure(profile)["deeds"] if key not in before]
    delve.endgame["settled"] = True
