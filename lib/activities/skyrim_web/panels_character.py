"""The character panels of /skyrim in the activity: the sheet, perks, masteries, collection, records, companion
and the Hall of Legends. Each is a port of its views.py screen (same engine calls, same rules, same wording) as
SkPanel data (docs/skyrim-activity-contract.md).
"""

from __future__ import annotations

import logging

from lib.activities.skyrim_web import common
from lib.activities.skyrim_web.common import Refuse
from lib.activities.skyrim_web.registry import act, view
from lib.features.skyrim import data as D
from lib.features.skyrim import engine as E
from lib.features.skyrim import progression as P

log = logging.getLogger(__name__)

_SKILL_LABELS = {"blade": "One-Handed", "marksman": "Marksman", "destruction": "Destruction",
                 "sneak": "Sneak", "speech": "Speech", "lockpicking": "Lockpicking"}


def _values(body: dict) -> list[str]:
    """A select posts {"values": [...]} or {"value": "..."}; take either."""
    body = body or {}
    vals = body.get("values")
    if vals is None and body.get("value") is not None:
        vals = [body["value"]]
    return [str(v) for v in (vals or [])]


def _one(body: dict) -> str:
    vals = _values(body)
    if not vals:
        raise Refuse("Pick one first.")
    return vals[0]


def _fresh(profile: dict, ctx: dict) -> dict:
    """The profile as saved (views re-reads before it mutates)."""
    return E.get_profile(ctx["uid"]) or profile



_SKILL_ICON = {"blade": "sword", "marksman": "bow", "destruction": "flame", "sneak": "hood", "speech": "speech",
               "lockpicking": "door"}


def _skill_tile(key, _value=None, **kw):
    return common.tile(_SKILL_LABELS.get(key, key), icon=f"a:skill_{key}|i:{_SKILL_ICON.get(key, 'star')}", **kw)


# ---- character ---------------------------------------------------------------------------------------------

@view("character")
def character(profile, ctx):
    stone = D.STONES[profile["stone"]]
    s = profile["skills"]
    into, need = D.xp_into_level(profile["xp"])
    boosted = set(stone["boost"])
    legend = E.legacy_rank(profile)
    pts = E.perk_points(profile)
    open_n = len(E.doctrine_choices_open(profile))
    temper = profile.get("temper") or {}

    blurb = [f"Level {E.level(profile)} {E.archetype(profile)}"
             + (f"  ·  🏛️ Legend {'⭐' * legend}" if legend else ""),
             f"Blessed by {stone['name']}  ·  XP {into}/{need}",
             "Stone-blessed skills learn faster."]
    if legend:
        boons = [D.BOONS[b] for b in E.legacy(profile).get("boons", []) if b in D.BOONS]
        if boons:
            blurb.append("🏛️ Boons: " + "  ·  ".join(f"{b['emoji']} {b['name']}" for b in boons))

    skills = [_skill_tile(key, s.get(key, 0), value=s.get(key, 0), meter=(s.get(key, 0), 100),
                          badge="Blessed" if key in boosted else None,
                          state="max" if s.get(key, 0) >= 100 else None)
              for key in _SKILL_LABELS]

    wt, at = profile["weapon_tier"], profile["armour_tier"]
    wk, ak = D.GEAR_TIERS[wt]["key"], D.GEAR_TIERS[at]["key"]
    gear = [common.tile(D.GEAR_TIERS[wt]["name"], icon=f"a:weapon_{wk}|i:sword",
                        sub=f"+{temper['weapon']} tempered" if temper.get("weapon") else "weapon"),
            common.tile(D.GEAR_TIERS[at]["name"], icon=f"a:armour_{ak}|i:shield",
                        sub=f"soaks {E.soak_pct(profile)}%")]
    if profile["words"]:
        gear.append(common.tile("Voice", icon="i:shout", value=f"{E.voice_charges(profile)}/{profile['words']}",
                                info=" ".join(D.SHOUT_WORDS[:profile["words"]])))

    hero = [common.tile("Hearts", icon="i:heart", value=E.heart_max(profile)),
            common.tile("Souls", icon="i:soul", value=profile["souls"])]
    streak = E.current_streak(profile)
    if streak >= 2:
        hero.append(common.tile("Streak", icon="i:flame", value=streak, sub="days"))
    if legend:
        hero.append(common.tile("Legend", icon="i:crown", value=legend))
    if profile.get("alduin_slain"):
        hero.append(common.tile("Alduin", icon="i:skull", value=f"x{profile['alduin_slain']}", state="done",
                                info="Slayer of Alduin."))
    wonders = [k for k in (profile.get("wonders") or []) if k in D.WONDERS]
    if wonders:
        hero.append(common.tile("Wonders", icon="i:diamond", value=f"{len(wonders)}/{len(D.WONDERS)}", nav="collection"))

    pet = E.active_companion(profile)
    more = [common.tile("Perks", icon="s:perks", badge=f"{pts}" if pts else None, state="ready" if pts else None, nav="perks"),
            common.tile("Masteries", icon="s:masteries", badge=f"{open_n}" if open_n else None,
                        state="ready" if open_n else None, nav="masteries"),
            common.tile("Collection", icon="s:collection", value=f"{E.collection_pct(profile)}%", nav="collection"),
            common.tile("Records", icon="s:records", nav="records"),
            common.tile("Companion", icon=f"s:{pet['art']}" if pet else "s:stray", nav="companion")]
    ready, _line = E.retire_ready(profile)
    if ready or legend or profile.get("alduin_slain"):
        more.append(common.tile("Hall", icon="s:hall_of_legends", state="ready" if ready else None, nav="hall"))
    return common.panel("character", f"{stone['emoji']} {profile['name']}", back="town", blurb=blurb,
                        sections=[common.section("Skills", tiles=skills, cols=3),
                                  common.section("Gear", tiles=gear, cols=3),
                                  common.section("You", tiles=hero, cols=3),
                                  common.section("More", tiles=more, cols=3)])


# ---- perks -------------------------------------------------------------------------------------------------

_PERK_EFFECT = {"stalwart": "+1 heart", "honed_edge": "+4% attack", "muffled": "+6% sneak",
                "persuasive": "+7% persuade", "juggernaut": "+6% soak", "alchemist": "+1 pocket",
                "deep_pockets": "+20% coin", "quick_study": "+10% XP"}


@view("perks")
def perks(profile, ctx):
    pts = E.perk_points(profile)
    tiles = []
    for key, perk in D.PERKS.items():
        have = E.perk_rank(profile, key)
        maxed = have >= perk["ranks"]
        can = pts > 0 and not maxed
        tiles.append(common.tile(perk["name"], icon=f"a:perk_{key}|i:star", pips=(have, perk["ranks"]),
                                 sub=_PERK_EFFECT.get(key, "per rank"), state="max" if maxed else "ready" if can else None,
                                 act="take" if can else None, body={"value": key} if can else None,
                                 info=perk["desc"]))
    sections = [common.section("Perks", tiles=tiles, cols=2)]
    if profile.get("words", 0) > 0:
        breath = E.voice_charges(profile)
        full = breath >= profile["words"]
        can = not full and pts > 0
        sections.append(common.section("Meditate", tiles=[common.tile(
            "Meditate", icon="i:shout", value=f"{breath}/{profile['words']}", sub="1 point",
            state="ready" if can else "locked", act="meditate" if can else None,
            info="Your breath is already full." if full else "No perk points to spend.")]))
    return common.panel("perks", "📜 Perks", back="character",
                        blurb=["One perk point per level.",
                               "Meditating spends a point to restore the Voice in full. The Greybeards approve "
                               f"({int(profile.get('meditations') or 0)} so far)."],
                        stats=[common.stat("Points", pts, "📜")], sections=sections)


@act("perks")
async def perks_act(profile, ctx, action, body):
    profile = _fresh(profile, ctx)
    if action == "take":
        key = _one(body)
        err = E.take_perk(profile, key)
        if err:
            raise Refuse(common.clean(err))
        E.save_profile(profile)
        await common.after(ctx, profile)
        perk = D.PERKS[key]
        return common.result(perks(profile, ctx), profile,
                             toast=f"✅ {perk['name']} is now rank {E.perk_rank(profile, key)}.")
    if action == "meditate":
        err = E.meditate(profile)
        if err:
            raise Refuse(common.clean(err))
        E.save_profile(profile)
        await common.after(ctx, profile)
        return common.result(perks(profile, ctx), profile, toast="🧘 The mind stills. Your breath returns in full.")
    raise Refuse("You can't do that here.")


# ---- masteries ---------------------------------------------------------------------------------------------

@view("masteries")
def masteries(profile, ctx):
    chosen = profile.get("doctrines") or {}
    sections = []
    owned = []
    for sk in chosen:
        for ch in E.doctrine_keys(profile, sk):
            doc = D.DOCTRINES.get(sk, {}).get(ch)
            if doc:
                owned.append(common.tile(doc["name"], icon=f"a:skill_{sk}|i:{_SKILL_ICON.get(sk, 'star')}",
                                         sub=_SKILL_LABELS.get(sk, sk), state="done", info=doc["desc"]))
    stars = E.legendary_stars(profile)
    if stars:
        owned.append(common.tile("Stars", icon="i:star", value=stars, info="Legendary skills reset."))
    if owned:
        sections.append(common.section("Yours", tiles=owned, cols=3))
    open_choices = E.doctrine_choices_open(profile)
    for sk in open_choices:
        opts = []
        for ch in E.doctrine_options_open(profile, sk):
            doc = D.DOCTRINES[sk][ch]
            opts.append(common.tile(doc["name"], icon=f"a:skill_{sk}|i:{_SKILL_ICON.get(sk, 'star')}", sub="permanent",
                                    state="ready", act="doctrine", body={"value": f"{sk}:{ch}"},
                                    confirm=f"Learn {doc['name']}? It is permanent. {doc['desc']}", info=doc["desc"]))
        sections.append(common.section(_SKILL_LABELS.get(sk, sk), tiles=opts))
    ready = E.legendary_ready(profile)
    if ready:
        leg = []
        for sk in ready:
            more = len(E.doctrine_options_open(profile, sk)) > 0
            leg.append(common.tile(_SKILL_LABELS.get(sk, sk), icon=f"a:skill_{sk}|i:star", sub="reset for a star",
                                   state="ready", act="legendary", body={"value": sk},
                                   confirm=f"Make {_SKILL_LABELS.get(sk, sk)} Legendary? It resets to 15 and keeps "
                                           f"its Doctrine{' - re-master it to earn the other' if more else ''}.",
                                   info="Resets to 15 for a star. The Doctrine stays."))
        sections.append(common.section("Legendary", tiles=leg, cols=3))
    if not owned and not open_choices:
        s = profile["skills"]
        sections.append(common.section("Reach 100", tiles=[
            _skill_tile(k, s.get(k, 0), value=s.get(k, 0), meter=(s.get(k, 0), 100), state="locked",
                        info="A Doctrine unlocks at 100.") for k in _SKILL_LABELS], cols=3))
    return common.panel(
        "masteries", "✨ Masteries", back="character",
        blurb=["Every skill you carry to **100** unlocks a permanent **Doctrine** - pick one of two. Make a "
               "mastered skill **Legendary** to reset it to 15 for a ⭐ (the Doctrine stays) - carry it back to 100 "
               "and the OTHER Doctrine is yours too."],
        sections=sections)


@act("masteries")
async def masteries_act(profile, ctx, action, body):
    profile = _fresh(profile, ctx)
    if action == "doctrine":
        raw = _one(body)
        sk, _, ch = raw.partition(":")
        err = E.choose_doctrine(profile, sk, ch)
        if err:
            raise Refuse(common.clean(err))
        E.save_profile(profile)
        await common.after(ctx, profile)
        return common.result(masteries(profile, ctx), profile,
                             toast=f"✅ **{D.DOCTRINES[sk][ch]['name']}** learned - it is yours for good.")
    if action == "legendary":
        sk = _one(body)
        err = E.make_legendary(profile, sk)
        if err:
            raise Refuse(common.clean(err))
        E.save_profile(profile)
        await common.after(ctx, profile)
        return common.result(masteries(profile, ctx), profile,
                             toast=f"⭐ **{_SKILL_LABELS.get(sk, sk)}** is now Legendary. The climb begins again.")
    raise Refuse("You can't do that here.")


# ---- collection --------------------------------------------------------------------------------------------

_BOOK_ICON = {"Bestiary": "skull", "Marked foes": "eye", "Dragon Wall": "wing", "Encounters": "rune",
              "Places cleared": "door", "Recipes brewed": "flask", "Pacts honoured": "diamond",
              "Legends slain": "crown", "Pit champions": "sword", "Companions": "heart", "Wonders": "star",
              "Cairn depths": "soul"}


@view("collection")
def collection(profile, ctx):
    pct = E.collection_pct(profile)
    book = []
    for _emoji, label, done, total, missing in E.collection_summary(profile):
        book.append(common.tile(label, icon=f"i:{_BOOK_ICON.get(label, 'star')}", value=f"{done}/{total}",
                                meter=(done, max(1, total)), state="done" if not missing else None,
                                info=f"{len(missing)} still to find." if missing else "Complete."))
    owned = set(profile.get("wonders") or [])
    wonders = []
    for key, w in D.WONDERS.items():
        if key in owned:
            wonders.append(common.tile(w["name"], icon=f"a:wonder_{key}|i:star", state="done", info=w["blurb"]))
        else:
            wonders.append(common.tile("???", icon=f"a:wonder_{key}|i:diamond", state="locked", info="Not found yet."))
    return common.panel("collection", "📦 Collection", back="character",
                        blurb=["Everything unique, ever. Fill the book."],
                        sections=[common.section("Complete", tiles=[common.tile(
                                      "Book", icon="s:collection", value=f"{pct}%", meter=(pct, 100),
                                      state="done" if pct >= 100 else None)], cols=1),
                                  common.section("The book", tiles=book, cols=3),
                                  common.section("Wonders", tiles=wonders, cols=4)])


# ---- records -----------------------------------------------------------------------------------------------

@view("records")
def records(profile, ctx):
    r = E.records_of(profile)
    st = profile["stats"]
    career = [("Delves", "delves", "door"), ("Cleared", "clears", "shield"), ("Kills", "kills", "sword"),
              ("Dragons", "dragons", "wing"), ("Deaths", "deaths", "skull"), ("Sneaks", "sneaks", "hood"),
              ("Persuades", "persuades", "speech"), ("Sweetrolls", "sweetrolls", "coin"),
              ("Pact clears", "pact_clears", "rune")]
    tiles = [common.tile(label, icon=f"i:{icon}", value=f"{int(st.get(key, 0)):,}") for label, key, icon in career]
    tiles.append(common.tile("Meditations", icon="i:shout", value=int(profile.get("meditations") or 0)))
    if st.get("launched"):
        tiles.append(common.tile("Launched", icon="i:wing", value=st["launched"], info="Into low orbit, by a giant."))

    def best(title, icon, val, coin=False, shown=None):
        if val:
            return common.tile(title, icon=f"i:{icon}", cost=int(val) if coin else None,
                               value=None if coin else (shown if shown is not None else f"{val:,}"))
        return common.tile(title, icon=f"i:{icon}", value="-", state="locked", info="No mark set yet.")

    bests = [best("Best satchel", "coin", r.get("satchel"), coin=True),
             best("Delve kills", "sword", r.get("kills_delve")),
             best("Biggest kill", "skull", r.get("kill_loot"), coin=True),
             best("Cairn depth", "soul", r.get("depth")),
             best("Delve streak", "flame", r.get("streak")),
             best("Pit rank", "crown", r.get("pit_rank"),
                  shown=E.pit_title(r["pit_rank"]) if r.get("pit_rank") else None)]
    sections = [common.section("Career", tiles=tiles, cols=3), common.section("Bests", tiles=bests, cols=3)]
    try:
        from lib.features.skyrim import badges as B
        got, total, missing = B.progress(profile)
        if total:
            sections.append(common.section("Badges", tiles=[common.tile(
                "Badges", icon="i:crown", value=f"{got}/{total}", meter=(got, total),
                state="done" if not missing else None,
                info=("Next: " + ", ".join(missing[:3])) if missing else "All earned.")], cols=1))
    except Exception:
        log.debug("skyrim badge progress line failed", exc_info=True)
    rivals = []
    names = E.all_profiles() if profile.get("rivals") else {}
    for uid, rv in list((profile.get("rivals") or {}).items())[:6]:
        w, l = int(rv.get("w", 0)), int(rv.get("l", 0))
        rivals.append(common.tile(names.get(uid, {}).get("name", "A rival"), icon="i:sword", value=f"{w}-{l}",
                                  state="done" if w > l else None))
    if rivals:
        sections.append(common.section("Rivals", tiles=rivals, cols=3))
    return common.panel("records", "🎖️ Records", back="character",
                        blurb=["Personal bests, kept forever. Every delve is an attempt."], sections=sections)


# ---- companion ---------------------------------------------------------------------------------------------

_PET_EDGE = {"meeko": "blocks a wound", "vix": "+8% ingredients", "pincer": "+5% septims", "corvus": "+2% crit"}


@view("companion")
def companion(profile, ctx):
    owned = profile.get("companions") or []
    tiles = []
    for key, pet in D.COMPANIONS.items():
        if key in owned:
            active = profile.get("companion") == key
            tiles.append(common.tile(pet["name"], icon=f"s:{pet['art']}", sub=_PET_EDGE.get(key, "helps you"),
                                     state="done" if active else None, act=None if active else "choose",
                                     body=None if active else {"value": key}, info=pet["passive"]))
        else:
            tiles.append(common.tile("???", icon="i:heartEmpty", state="locked", info="Not found yet."))
    active = E.active_companion(profile)
    return common.panel("companion", "🐾 Companions", art=active.get("art") if active else None, back="character",
                        blurb=["Strays found on the road, kept forever. One walks with you at a time.",
                               "Keep an eye out for the stray."],
                        sections=[common.section("Walks with you", tiles=tiles, cols=2)])


@act("companion")
async def companion_act(profile, ctx, action, body):
    profile = _fresh(profile, ctx)
    if action != "choose":
        raise Refuse("You can't do that here.")
    key = _one(body)
    if key not in (profile.get("companions") or []) or key not in D.COMPANIONS:
        raise Refuse("That friend hasn't found you yet.")
    profile["companion"] = key
    E.save_profile(profile)
    await common.after(ctx, profile)
    pet = D.COMPANIONS[key]
    return common.result(companion(profile, ctx), profile, toast=f"{pet['emoji']} **{pet['name']}** trots to your side.")


# ---- the Hall of Legends -----------------------------------------------------------------------------------

# The boon and the stone are picked in separate requests and only used when the player retires, so the picks wait on
# the profile (tied to the legend rank they were made at, so a new life starts with none), surviving a restart. The
# inherited ability is saved on the profile too, like views.py does.


def _picks(profile: dict) -> dict:
    rank = E.legacy_rank(profile)
    pk = profile.get("hall_picks")
    if not isinstance(pk, dict) or pk.get("rank") != rank:
        pk = profile["hall_picks"] = {"rank": rank, "boon": None, "stone": None}
    return pk


_DEED_SHORT = {"warden_clear": "Rune Warden", "caravan_rescue": "Scout home", "vault_quiet": "Quiet vault",
               "warden_styles": "Three styles", "caravan_guard": "Guard duty", "vault_parley": "Parley",
               "reborn": "Reborn", "cairn_20": "Cairn 20", "faction_story": "Faction story"}


def _hall_sections(profile) -> list:
    lg = E.legacy(profile)
    hall = E.H.ensure(profile)
    sections = []
    owned = []
    for b in lg.get("boons", []) + hall["boons"]:
        if b in D.BOONS:
            owned.append(common.tile(D.BOONS[b]["name"], icon=f"a:boon_{b}|i:crown", state="done", info=D.BOONS[b]["desc"]))
        elif b in D.HALL_BOONS:
            owned.append(common.tile(D.HALL_BOONS[b]["name"], icon=f"a:hallboon_{b}|i:crown", state="done",
                                     info=D.HALL_BOONS[b]["desc"]))
    if owned:
        sections.append(common.section("Boons", tiles=owned, cols=3))
    asleep = E.legacy_rank(profile) < D.LEGACY_MAX
    for chapter in D.HALL_CHAPTERS:
        boon = D.HALL_BOONS[chapter["boon"]]
        tiles = [common.tile(_DEED_SHORT.get(key, "Deed"), icon="i:star", state="done" if key in hall["deeds"] else "locked",
                             info=D.HALL_DEEDS[key]) for key in chapter["deeds"]]
        has = chapter["boon"] in hall["boons"]
        tiles.append(common.tile(boon["name"], icon=f"a:hallboon_{chapter['boon']}|i:crown",
                                 state="done" if has else "locked",
                                 info=boon["desc"] + (" Awakens at Legend 5." if asleep else "")))
        sections.append(common.section(chapter["name"], tiles=tiles, cols=4))
    stories = []
    for key, saved in hall["stories"].items():
        if key in D.FACTION_STORIES:
            spec = D.FACTION_STORIES[key]
            stories.append(common.tile(spec["name"], icon=f"a:faction_{key}|i:star",
                                       sub=spec["title"] if saved.get("complete") else "begun",
                                       state="done" if saved.get("complete") else None))
    if stories:
        sections.append(common.section("Stories", tiles=stories, cols=3))
    legends = []
    for i, ep in enumerate(lg.get("epitaphs") or [], start=1):
        boon = D.BOONS.get(ep.get("boon"), {})
        legends.append(common.tile(f"Legend {i}", icon="i:crown", value=f"L{ep.get('level', '?')}", sub=str(ep.get("name", "?")),
                                   state="done", info=(f"{ep.get('days', 0)} days, {ep.get('dragons', 0)} dragons, "
                                                       f"Alduin x{ep.get('alduin', 0)}." + (f" Took {boon['name']}." if boon else "")
                                                       + (f" {ep['line']}" if ep.get("line") else ""))))
    if legends:
        sections.append(common.section("Your legends", tiles=legends, cols=3))
    others = []
    for uid, other in E.all_profiles().items():
        if int(uid) == int(profile["user_id"]):
            continue
        for i, ep in enumerate((other.get("legacy") or {}).get("epitaphs") or [], start=1):
            others.append((other.get("name", "?"), i, ep))
    seats = [common.tile(name, icon="i:crown", value=f"L{ep.get('level', '?')}", sub=f"Legend {i}",
                         info=f"{ep.get('days', 0)} days, {ep.get('dragons', 0)} dragons, Alduin x{ep.get('alduin', 0)}.")
             for name, i, ep in others[-6:]]
    if seats:
        sections.append(common.section("Other seats", tiles=seats, cols=3))
    return sections


@view("hall")
def hall(profile, ctx):
    ready, req_line = E.retire_ready(profile)
    pk = _picks(profile)
    offer = E.boon_offer(profile) if ready else []
    inheritance = P.inheritance_options(profile) if ready else []
    selected = profile.get("inheritance") or {}
    chosen_inh = next((i for i in inheritance if i["skill"] == selected.get("skill")
                       and i["choice"] == selected.get("choice")), None)
    boon_key = pk["boon"] if pk["boon"] in offer else None
    stone_key = pk["stone"] if pk["stone"] in D.STONES else profile["stone"]
    lg_rank = E.legacy_rank(profile)
    head = [common.tile("Seats", icon="i:crown", value=lg_rank),
            common.tile("Retire", icon="s:hall_of_legends", state="ready" if ready else "locked",
                        info="The Hall is ready for you." if ready else f"Needs {common.clean(req_line)}.")]
    if lg_rank >= D.LEGACY_MAX:
        h = E.H.ensure(profile)
        head.append(common.tile("Deeds", icon="i:star", value=f"{len(h['deeds'])}/{len(D.HALL_DEEDS)}"))
    sections = [common.section("Hall", tiles=head, cols=3)]
    actions = []
    if ready:
        if offer:
            sections.append(common.section("Pick a boon", tiles=[
                common.tile(D.BOONS[k]["name"], icon=f"a:boon_{k}|i:crown", state="done" if k == boon_key else "ready",
                            act="boon", body={"value": k}, info=D.BOONS[k]["desc"]) for k in offer]))
        if inheritance:
            sections.append(common.section("Carry an ability", tiles=[
                common.tile(D.DOCTRINES[i["skill"]][i["choice"]]["name"],
                            icon=f"a:skill_{i['skill']}|i:{_SKILL_ICON.get(i['skill'], 'star')}",
                            sub=_SKILL_LABELS.get(i["skill"], i["skill"]),
                            state="done" if i is chosen_inh else "ready", act="inherit",
                            body={"value": f"{i['skill']}:{i['choice']}"}) for i in inheritance[:25]], cols=3))
        sections.append(common.section("Wake under", tiles=[
            common.tile(s["name"].replace("The ", "").replace(" Stone", ""), icon="i:rune",
                        state="done" if k == stone_key else None, act="stone", body={"value": k}, info=s["blurb"])
            for k, s in D.STONES.items()], cols=3))
        b = D.BOONS.get(boon_key)
        stone = D.STONES[stone_key]
        swapping = stone_key != profile["stone"]
        confirm = (f"Retire {profile['name']} (level {E.level(profile)}, {profile['stats'].get('dragons', 0)} "
                   f"dragons, {profile['septims']:,} septims)"
                   + (f" and take {b['emoji']} {b['name']} - {b['desc']}" if b
                      else f" and record Legend {E.legacy_rank(profile) + 1} in the Hall.")
                   + f"\n\nThey wake again under {stone['emoji']} {stone['name']}"
                   + (" - a new path this time." if swapping else " - the same stone as before.")
                   + "\n\nThis cannot be undone. Level, skills, gear, gold, perks, doctrines and the Voice reset, "
                     "except your chosen inherited ability. The collection, records, wonders, companions, career "
                     "deeds and the estate stay yours forever.")
        hint = ("Choose a boon first." if offer and not boon_key
                else "Choose an ability to inherit first." if inheritance and not chosen_inh else "")
        actions.append(common.action("retire", "Retire them, forever", "🏛️", "danger", disabled=bool(hint),
                                     hint=hint, confirm=common.clean(confirm)))
    sections += _hall_sections(profile)
    blurb = ["Retire a champion and begin again. Level, skills, gear, gold, perks and the Voice reset to a fresh "
             "start; your collection, records, wonders, companions and the estate persist. The first five legends "
             "choose a boon; later lives add a new seat, with three more boons earned through Hall deeds."]
    return common.panel("hall", "🏛️ The Hall of Legends", art="hall_of_legends", back="character",
                        blurb=blurb, sections=sections, actions=actions)


@act("hall")
async def hall_act(profile, ctx, action, body):
    profile = _fresh(profile, ctx)
    ready, _line = E.retire_ready(profile)
    if action not in ("boon", "inherit", "stone", "retire"):
        raise Refuse("You can't do that here.")
    if not ready:
        raise Refuse("The Hall isn't ready for you yet.")
    pk = _picks(profile)
    if action == "boon":
        key = _one(body)
        if key not in E.boon_offer(profile):
            raise Refuse("Fate never offered that boon.")
        pk["boon"] = key
        E.save_profile(profile)
        b = D.BOONS[key]
        return common.result(hall(profile, ctx), profile, toast=f"{b['emoji']} {b['name']} chosen.")
    if action == "stone":
        key = _one(body)
        if key not in D.STONES:
            raise Refuse("No such Guardian Stone.")
        pk["stone"] = key
        E.save_profile(profile)
        return common.result(hall(profile, ctx), profile, toast=f"{D.STONES[key]['emoji']} {D.STONES[key]['name']} chosen.")
    if action == "inherit":
        sk, _, ch = _one(body).partition(":")
        err = P.inherit(profile, sk, ch)
        if err:
            raise Refuse(common.clean(err))
        E.save_profile(profile)
        return common.result(hall(profile, ctx), profile, toast="Ability chosen to carry into your next life.")
    # retire
    body = body or {}
    boon_key = pk["boon"]
    if body.get("boon"):
        boon_key = str(body["boon"])
    stone_key = body.get("stone") or pk["stone"] or profile["stone"]
    offer = E.boon_offer(profile)
    if offer and boon_key not in offer:
        raise Refuse("Choose a boon first.")
    if P.inheritance_options(profile):
        sel = profile.get("inheritance") or {}
        if not any(o["skill"] == sel.get("skill") and o["choice"] == sel.get("choice")
                   for o in P.inheritance_options(profile)):
            raise Refuse("Choose an ability to inherit first.")
    if not offer:
        boon_key = None
    expected = E.legacy_rank(profile)
    b = D.BOONS.get(boon_key)
    stone = D.STONES.get(stone_key) or D.STONES[profile["stone"]]
    err = E.retire(profile, boon_key, stone_key, expected_rank=expected)
    if err:
        raise Refuse(common.clean(err))
    profile.pop("hall_picks", None)
    E.save_profile(profile)
    await common.after(ctx, profile)
    n = E.legacy_rank(profile)
    toast = (f"🏛️ **Legend {n} takes their seat in the Hall.** Hey, you. You're finally awake... again. "
             + (f"{b['emoji']} {b['name']} rides with you this time, " if b else "Your boons travel with you, ")
             + f"under {stone['emoji']} {stone['name']}.")
    return common.result(hall(profile, ctx), profile, toast=toast)
