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


def _bar(value: int, lo: int = 15, hi: int = 100, width: int = 8) -> str:
    """The same filled/empty bar views.py draws."""
    filled = round(width * (value - lo) / (hi - lo)) if hi != lo else 0
    return "▰" * max(0, filled) + "▱" * max(0, width - filled)


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


# ---- character ---------------------------------------------------------------------------------------------

@view("character")
def character(profile, ctx):
    stone = D.STONES[profile["stone"]]
    s = profile["skills"]
    into, need = D.xp_into_level(profile["xp"])
    words = " ".join(D.SHOUT_WORDS[:profile["words"]]) if profile["words"] else "not yet learned"
    boosted = set(stone["boost"])
    legend = E.legacy_rank(profile)
    pts = E.perk_points(profile)
    open_n = len(E.doctrine_choices_open(profile))
    temper = profile.get("temper") or {}

    blurb = [f"Level {E.level(profile)} {E.archetype(profile)}"
             + (f"  ·  🏛️ Legend {'⭐' * legend}" if legend else ""),
             f"Blessed by {stone['name']}  ·  XP {_bar(into, 0, need)} {into}/{need}"]
    if legend:
        boons = [D.BOONS[b] for b in E.legacy(profile).get("boons", []) if b in D.BOONS]
        if boons:
            blurb.append("🏛️ Boons: " + "  ·  ".join(f"{b['emoji']} {b['name']}" for b in boons))
    if profile.get("alduin_slain"):
        n = profile["alduin_slain"]
        blurb.append(f"⭐ **Slayer of Alduin**{f' (x{n})' if n > 1 else ''}")

    stats = [common.stat("Level", E.level(profile), "⭐"),
             common.stat("Hearts", E.heart_max(profile), "❤️"),
             common.stat("Potions", f"{profile['potions']}/{E.potion_cap(profile)}", "🧪"),
             common.stat("Septims", f"{profile['septims']:,}", "💰"),
             common.stat("Souls", profile["souls"], "🐉"),
             common.stat("Collection", f"{E.collection_pct(profile)}%", "📦")]
    # the numbers above aren't repeated below: the sections carry the skills, the gear and the rest

    skill_lines = [f"{label:<12} **{s.get(key, 0)}** {_bar(s.get(key, 0))}" + ("  ✨" if key in boosted else "")
                   for key, label in _SKILL_LABELS.items()]
    skill_lines.append("Stone-blessed skills ✨ learn faster.")
    t_bit = (f"  ·  🪓 +{temper.get('weapon', 0)}/+{temper.get('armour', 0)} tempered"
             if temper.get("weapon") or temper.get("armour") else "")
    gear = [f"**Gear**: {E.gear_name(profile, 'weapon')}  ·  {E.gear_name(profile, 'armour')} "
            f"(soaks {E.soak_pct(profile)}%){t_bit}",
            f"**The Voice**: 🗣️ {words}  ·  breath {E.voice_charges(profile)}/{profile['words']}"
            f"  ·  🐉 {profile['souls']} soul{'s' if profile['souls'] != 1 else ''}"]
    extra = []
    doc = profile.get("doctrines") or {}
    if doc:
        bits = [f"{D.DOCTRINES[sk][ch]['emoji']} {D.DOCTRINES[sk][ch]['name']}"
                for sk in doc for ch in E.doctrine_keys(profile, sk) if ch in D.DOCTRINES.get(sk, {})]
        star = f"  ·  ⭐x{E.legendary_stars(profile)}" if E.legendary_stars(profile) else ""
        extra.append(f"**Doctrines**: {'  ·  '.join(bits)}{star}")
    pet = E.active_companion(profile)
    if pet:
        extra.append(f"**Companion**: {pet['emoji']} {pet['name']} - {pet['passive']}")
    wonders = [k for k in (profile.get("wonders") or []) if k in D.WONDERS]
    if wonders:
        shelf = " ".join(D.WONDERS[k]["emoji"] for k in wonders)
        extra.append(f"**Wonders**: ✨ {shelf}  ({len(wonders)}/{len(D.WONDERS)})")
    streak = E.current_streak(profile)
    foot = []
    if streak >= 2:
        foot.append(f"🔥 {streak}-day streak")
    if pts:
        foot.append(f"📜 {pts} perk point{'s' if pts != 1 else ''} to spend")
    if foot:
        extra.append("  ·  ".join(foot))

    actions = [
        common.action("masteries", f"Masteries ({open_n})" if open_n else "Masteries", "✨",
                      "primary" if open_n else "secondary", nav="masteries"),
        common.action("perks", f"Perks ({pts})" if pts else "Perks", "📜",
                      "primary" if pts else "secondary", nav="perks"),
        common.action("collection", f"Collection {E.collection_pct(profile)}%", "📦", nav="collection"),
        common.action("records", "Records", "🎖️", nav="records"),
        common.action("companion", "Companion", "🐾", nav="companion"),
    ]
    ready, _line = E.retire_ready(profile)
    if ready or legend or profile.get("alduin_slain"):
        actions.append(common.action("hall", "Hall of Legends" + (" - retirement awaits" if ready else ""), "🏛️",
                                     "danger" if ready else "secondary", nav="hall"))
    return common.panel("character", f"{stone['emoji']} {profile['name']}", back="town", blurb=blurb,
                        stats=stats, sections=[common.section("Skills, which grow as you use them", skill_lines),
                                               common.section("Gear and Voice", gear),
                                               *([common.section("Standing", extra)] if extra else [])],
                        actions=actions)


# ---- perks -------------------------------------------------------------------------------------------------

@view("perks")
def perks(profile, ctx):
    pts = E.perk_points(profile)
    lines = []
    for key, perk in D.PERKS.items():
        have = E.perk_rank(profile, key)
        lines.append(f"{perk['emoji']} **{perk['name']}** {have}/{perk['ranks']} - {perk['desc']}")
    actions, selects = [], []
    has_voice = profile.get("words", 0) > 0
    sections = [common.section("Perks", lines)]
    if has_voice:
        breath = E.voice_charges(profile)
        full = breath >= profile["words"]
        state = ("your breath is already **full** - nothing to restore"
                 if full else f"breath {breath}/{profile['words']}")
        sections.append(common.section("Meditation", [
            f"🧘 Spend a point to still the mind and restore the Voice in full ({state}). "
            f"The Greybeards approve. ({int(profile.get('meditations') or 0)} so far)"]))
        hint = ("Your breath is already full." if full else "" if pts > 0 else "No perk points to spend.")
        actions.append(common.action("meditate", "Meditate - breath already full" if full else "Meditate (1 pt)",
                                     "🧘", "secondary" if full or not pts else "primary",
                                     disabled=full or pts <= 0, hint=hint))
    opts = [common.option(key, f"{perk['name']} ({E.perk_rank(profile, key)}/{perk['ranks']})", perk["desc"],
                          perk["emoji"])
            for key, perk in D.PERKS.items() if E.perk_rank(profile, key) < perk["ranks"]]
    if pts > 0 and opts:
        selects.append(common.select("take", "Spend a perk point...", opts))
    return common.panel("perks", "📜 Perks", back="character",
                        blurb=[f"One point per level. Points to spend: **{pts}**"],
                        stats=[common.stat("Perk points", pts, "📜")], sections=sections,
                        actions=actions, selects=selects)


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
    if chosen:
        rows = []
        for sk in chosen:
            for ch in E.doctrine_keys(profile, sk):
                doc = D.DOCTRINES.get(sk, {}).get(ch)
                if doc:
                    rows.append(f"{doc['emoji']} **{doc['name']}** ({_SKILL_LABELS.get(sk, sk)}) - {doc['desc']}")
        sections.append(common.section("Your Doctrines", rows))
    stars = E.legendary_stars(profile)
    if stars:
        sections.append(common.section("Legendary skills", [f"⭐ **Legendary skills reset:** {stars}"]))
    open_choices = E.doctrine_choices_open(profile)
    if open_choices:
        rows = []
        for sk in open_choices:
            pair = "  vs  ".join(f"{D.DOCTRINES[sk][c]['emoji']} {D.DOCTRINES[sk][c]['name']}"
                                 for c in E.doctrine_options_open(profile, sk))
            rows.append(f"{_SKILL_LABELS.get(sk, sk)}: {pair}")
        sections.append(common.section("Doctrines to choose (a skill just hit 100)", rows))
    elif not chosen:
        sections.append(common.section("Doctrines", ["No skill at 100 yet. Master one and its Doctrine unlocks here."]))
    selects = []
    if open_choices:
        opts = []
        for sk in open_choices:
            for ch in E.doctrine_options_open(profile, sk):
                doc = D.DOCTRINES[sk][ch]
                opts.append(common.option(f"{sk}:{ch}", f"{_SKILL_LABELS.get(sk, sk)}: {doc['name']}",
                                          doc["desc"], doc["emoji"]))
        selects.append(common.select("doctrine", "Choose a Doctrine (permanent)...", opts))
    ready = E.legendary_ready(profile)
    if ready:
        opts = []
        for sk in ready:
            more = len(E.doctrine_options_open(profile, sk)) > 0
            desc = ("Resets to 15, keeps its Doctrine - re-master it to earn the other."
                    if more else "Resets this skill to 15. Keeps its Doctrines.")
            opts.append(common.option(sk, f"{_SKILL_LABELS.get(sk, sk)} → Legendary", desc, "⭐"))
        selects.append(common.select("legendary", "Make a skill Legendary (reset to 15 for a ⭐)...", opts))
    return common.panel(
        "masteries", "✨ Masteries", back="character",
        blurb=["Every skill you carry to **100** unlocks a permanent **Doctrine** - pick one of two. Make a "
               "mastered skill **Legendary** to reset it to 15 for a ⭐ (the Doctrine stays) - carry it back to 100 "
               "and the OTHER Doctrine is yours too. This is how two maxed Dragonborn end up fighting differently."],
        stats=[common.stat("Doctrines open", len(open_choices), "✨"), common.stat("Legendary stars", stars, "⭐")],
        sections=sections, selects=selects)


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

@view("collection")
def collection(profile, ctx):
    rows = []
    for emoji, label, done, total, _missing in E.collection_summary(profile):
        rows.append(f"{emoji} **{label}**  {_bar(done, 0, max(1, total), 6)}  {done}/{total}")
    return common.panel("collection", f"📦 The Collection Log - {E.collection_pct(profile)}%", back="character",
                        blurb=["Everything unique, ever. Fill the book."],
                        stats=[common.stat("Complete", f"{E.collection_pct(profile)}%", "📦")],
                        sections=[common.section("The book", rows)])


# ---- records -----------------------------------------------------------------------------------------------

@view("records")
def records(profile, ctx):
    r = E.records_of(profile)
    st = profile["stats"]
    bests = [("💰", "Richest satchel banked", r.get("satchel"), "septims"),
             ("⚔️", "Most kills in one delve", r.get("kills_delve"), "kills"),
             ("🩸", "Biggest single kill", r.get("kill_loot"), "septims"),
             ("💀", "Deepest Soul Cairn descent", r.get("depth"), "floors"),
             ("🔥", "Longest delve streak", r.get("streak"), "days"),
             ("🗡️", "Best Pit rank", r.get("pit_rank"), None)]
    best_rows = []
    for emoji, label, val, unit in bests:
        if val:
            shown = E.pit_title(val) if label.startswith("Best Pit") else f"{val:,}{' ' + unit if unit else ''}"
            best_rows.append(f"{emoji} **{label}**: {shown}")
        else:
            best_rows.append(f"{emoji} {label}: no mark set yet")
    career = [f"{st['delves']} delves · {st['clears']} cleared · {st['deaths']} deaths · "
              f"{st['kills']} kills · {st['dragons']} dragons · {st['sneaks']} sneaks · "
              f"{st['persuades']} persuasions · {st['sweetrolls']} sweetrolls · "
              f"{int(st.get('pact_clears', 0))} pact clears · {int(profile.get('meditations') or 0)} meditations"]
    if st.get("launched"):
        career.append(f"...and launched into low orbit by a giant, {st['launched']} time(s).")
    sections = [common.section("Personal bests", best_rows), common.section("Career deeds", career)]
    try:
        from lib.features.skyrim import badges as B
        got, total, missing = B.progress(profile)
        if total:
            rows = [f"{got}/{total} earned (each pays UKPence the first time)"]
            if missing:
                rows.append("Still out there: " + "  ·  ".join(missing[:6])
                            + (f"  (+{len(missing) - 6} more)" if len(missing) > 6 else ""))
            sections.append(common.section("🎖️ Server badges", rows))
    except Exception:
        log.debug("skyrim badge progress line failed", exc_info=True)
    rivalry = E.rivalry_lines(profile)
    if rivalry:
        sections.append(common.section("The rivalry ledger (ghost duels)", rivalry[:6]))
    stats = [common.stat("Delves", st["delves"], "🗺️"), common.stat("Cleared", st["clears"], "🏰"),
             common.stat("Kills", st["kills"], "⚔️"), common.stat("Dragons", st["dragons"], "🐉"),
             common.stat("Deaths", st["deaths"], "💀")]
    return common.panel("records", "🎖️ Hall of Records", back="character",
                        blurb=["Personal bests, kept forever. Every delve is an attempt."],
                        stats=stats, sections=sections)


# ---- companion ---------------------------------------------------------------------------------------------

@view("companion")
def companion(profile, ctx):
    owned = profile.get("companions") or []
    rows = []
    if not owned:
        rows.append("The road has offered you no friends yet. Keep an eye out for the 🐾 **stray** - "
                    "something small may choose you.")
    for key in D.COMPANIONS:
        pet = D.COMPANIONS[key]
        if key in owned:
            tick = "🐾" if profile.get("companion") == key else "▫️"
            rows.append(f"{tick} {pet['emoji']} **{pet['name']}** ({pet['species']}) - {pet['passive']}")
        else:
            rows.append("❔ Someone out there hasn't found you yet...")
    selects = []
    if len(owned) > 1:
        opts = [common.option(k, D.COMPANIONS[k]["name"], D.COMPANIONS[k]["passive"], D.COMPANIONS[k]["emoji"],
                              chosen=profile.get("companion") == k)
                for k in owned if k in D.COMPANIONS]
        selects.append(common.select("choose", "Who walks with you today?", opts))
    active = E.active_companion(profile)
    return common.panel("companion", "🐾 Companions", art=active.get("art") if active else None, back="character",
                        blurb=["Strays found on the road, kept forever. One walks with you at a time."],
                        stats=[common.stat("Found", f"{len(owned)}/{len(D.COMPANIONS)}", "🐾")],
                        sections=[common.section("Your companions", rows)], selects=selects)


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


def _hall_sections(profile) -> list:
    lg = E.legacy(profile)
    hall = E.H.ensure(profile)
    sections = []
    owned = []
    for b in lg.get("boons", []) + hall["boons"]:
        boon = D.BOONS.get(b) or D.HALL_BOONS.get(b)
        if boon:
            owned.append(f"{boon['emoji']} **{boon['name']}** - {boon['desc']}")
    sections.append(common.section("Your boons (forever)", owned))
    deeds = []
    if E.legacy_rank(profile) < D.LEGACY_MAX:
        deeds.append("Deeds can be earned now; their boons awaken at Legend 5.")
    for chapter in D.HALL_CHAPTERS:
        boon = D.HALL_BOONS[chapter["boon"]]
        deeds.append(f"**{chapter['name']}** → {boon['emoji']} {boon['name']}")
        for key in chapter["deeds"]:
            deeds.append(f"{'✅' if key in hall['deeds'] else '▫️'} {D.HALL_DEEDS[key]}")
    sections.append(common.section("Hall deeds (Skyrim records, earned automatically)", deeds))
    stories = []
    for key, saved in hall["stories"].items():
        if key in D.FACTION_STORIES:
            spec = D.FACTION_STORIES[key]
            stories.append(f"📜 **{spec['name']}** · {spec['title'] if saved.get('complete') else 'first chapter remembered'}")
    sections.append(common.section("Stories", stories))
    legends = []
    for i, ep in enumerate(lg.get("epitaphs") or [], start=1):
        boon = D.BOONS.get(ep.get("boon"), {})
        legends.append(f"⭐ **Legend {i} - {ep.get('name', '?')}**: {ep.get('days', 0)} days, "
                       f"level {ep.get('level', '?')}, {ep.get('dragons', 0)} dragons, Alduin x{ep.get('alduin', 0)}."
                       + (f" Took {boon['name']}." if boon else ""))
        if ep.get("line"):
            legends.append(ep["line"])
    sections.append(common.section("Your legends", legends))
    others = []
    for uid, other in E.all_profiles().items():
        if int(uid) == int(profile["user_id"]):
            continue
        for i, ep in enumerate((other.get("legacy") or {}).get("epitaphs") or [], start=1):
            others.append((other.get("name", "?"), i, ep))
    seats = []
    for name, i, ep in others[-8:]:
        boon = D.BOONS.get(ep.get("boon"), {})
        seats.append(f"⭐ **{name}**, Legend {i} - {ep.get('days', 0)} days, level {ep.get('level', '?')}, "
                     f"{ep.get('dragons', 0)} dragons, Alduin x{ep.get('alduin', 0)}."
                     + (f" Took {boon['name']}." if boon else ""))
    sections.append(common.section("The other seats", seats))
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
    sections = _hall_sections(profile)
    selects, actions = [], []
    blurb = ["Retire a champion and begin again. Level, skills, gear, gold, perks and the Voice reset to a fresh "
             "start; your collection, records, wonders, companions and the estate persist. The first five legends "
             "choose a boon; later lives add a new seat, with three more boons earned through Hall deeds."]
    if ready:
        sections.append(common.section("Ready", [
            "🏛️ **The Hall is ready for you.** "
            + ("Choose a boon, then retire." if offer else "Retire again to begin another life.")
            + " There is no undoing it.",
            "🪨 The one who wakes on the cart is a stranger: you may take a **different Guardian Stone** on the "
            "way out."]))
        if offer:
            selects.append(common.select("boon", "🏛️ Choose your legend's boon...", [
                common.option(k, D.BOONS[k]["name"], D.BOONS[k]["desc"], D.BOONS[k]["emoji"], chosen=k == boon_key)
                for k in offer]))
        if inheritance:
            sections.append(common.section("Inherited ability", [
                chosen_inh["label"] if chosen_inh else "Choose one below before retiring."]))
            selects.append(common.select("inherit", "Carry one learned ability into your next life", [
                common.option(f"{i['skill']}:{i['choice']}", i["label"], chosen=i is chosen_inh)
                for i in inheritance[:25]]))
        selects.append(common.select("stone", "🪨 Wake under a different Guardian Stone...", [
            common.option(k, s["name"], s["blurb"], s["emoji"], chosen=k == stone_key)
            for k, s in D.STONES.items()]))
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
    else:
        sections.append(common.section("Next retirement", [f"The next retirement asks: {req_line}."]))
    lg_rank = E.legacy_rank(profile)
    stats = [common.stat("Seats", lg_rank, "🏛️"),
             common.stat("Next retirement", "ready" if ready else req_line, "🎯")]
    if lg_rank >= D.LEGACY_MAX:
        h = E.H.ensure(profile)
        stats.append(common.stat("Hall deeds", f"{len(h['deeds'])}/{len(D.HALL_DEEDS)}", "▫️"))
    return common.panel("hall", "🏛️ The Hall of Legends", art="hall_of_legends", back="character",
                        blurb=blurb, stats=stats, sections=sections, actions=actions, selects=selects)


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
