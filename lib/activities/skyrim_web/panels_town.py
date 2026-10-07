"""The town panels that aren't the shop or the character sheet: Notice Board, Daily board, the Pit, Factions,
Holdings, Rankings and Help. Each is a port of its views.py screen (same rules, same engine calls, same wording)
as SkPanel data. Where views.py builds a block of text, this reuses that builder rather than copying the words,
so the Discord and activity screens can't drift apart.
"""

from __future__ import annotations

import re
from pathlib import Path

import config
from lib.activities.skyrim_web import common
from lib.activities.skyrim_web.common import Refuse
from lib.activities.skyrim_web.registry import act, view
from lib.features.skyrim import data as D
from lib.features.skyrim import engine as E
from lib.features.skyrim import progression as P
from lib.features.skyrim import views as V

_ART_DIR = Path(__file__).resolve().parents[3] / "data" / "skyrim"
_MIN_PIT = 5


def have_art(key: str | None) -> bool:
    """Whether a scene exists on disk (views.py falls back to another scene when the art isn't dropped yet)."""
    return bool(key) and any((_ART_DIR / f"{key}.{ext}").exists() for ext in ("webp", "png"))


def _values(body: dict) -> list[str]:
    """What a select posted: {"values": [...]} or {"value": "..."}."""
    body = body or {}
    if body.get("values") is not None:
        return [str(v) for v in body["values"]]
    if body.get("value") not in (None, ""):
        return [str(body["value"])]
    return []


def _one(body: dict, what: str = "choice") -> str:
    vals = _values(body)
    if not vals:
        raise Refuse(f"Pick a {what} first.")
    return vals[0]


def _refuse_if(err):
    """The engine reports a refusal as a string; the API answers it as 422."""
    if err:
        raise Refuse(common.clean(err))


def _by_headings(text: str) -> tuple[list[str], list[dict]]:
    """views.py text with '### ' sub-headings -> (lines before the first one, [section])."""
    pre: list[str] = []
    sections: list[dict] = []
    cur = None
    for i, raw in enumerate(text.split("\n")):
        if i == 0 and raw.startswith("## "):
            continue
        if raw.startswith("### "):
            cur = {"title": raw[4:], "body": []}
            sections.append(cur)
        elif cur is None:
            pre.append(raw)
        else:
            cur["body"].append(raw)
    return pre, [common.section(s["title"], s["body"]) for s in sections]


def _by_blocks(text: str) -> list[dict]:
    """views.py text split on blank lines; a block that opens with a bold '**Title:**' line is titled by it."""
    blocks, cur = [], []
    for i, raw in enumerate(text.split("\n")):
        if i == 0 and raw.startswith("## "):
            continue
        if raw.strip():
            cur.append(raw)
        elif cur:
            blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)
    out = []
    for b in blocks:
        m = re.fullmatch(r"\*\*(.+?):?\*\*:?", b[0].strip())
        out.append(common.section(m.group(1) if m else "", b[1:] if m else b))
    return out


def _toast(text) -> str:
    return common.clean(str(text).split("\n")[0]) if text else ""


# ---- notice board ----------------------------------------------------------------------------------------

def _notice_panel(profile: dict, extra: list | None = None) -> dict:
    text = V._notice_text(profile)
    E.save_profile(profile)      # keep the weekly tracker rollover, as views does
    pre, sections = _by_headings(text)
    store = E.world_boss()
    boss = E.wb_boss(store)
    pts, total = E.task_points(profile)
    acts = []
    if E.daily_available(profile):
        acts.append(common.action("daily", "Brave the daily", "📅", "success", nav="adventure:daily",
                                  hint=E.daily_location()["name"]))
    acts.append(common.action("daily_board", "Daily board", "📋", nav="daily_board"))
    if E.tasks_claimable(profile):
        acts.append(common.action("claim", "Claim bounties", "🎁", "success"))
    if E.wb_share_waiting(profile):
        acts.append(common.action("spoils", "Claim spoils", "🏆", "success"))
    selects = []
    if E.wb_available(profile):
        selects.append(common.select("march", "📯 March on it: choose your part", [
            common.option(role, spec["label"], spec["hint"], spec["emoji"]) for role, spec in P.HUNT_ROLES.items()]))
    secs = list(extra or []) + sections
    return common.panel(
        "notice", "The Notice Board", art="notice_board", back="town", blurb=pre,
        stats=[common.stat("Task points", f"{pts}/{total}", "📋"),
               common.stat(boss["name"], f"{store['hp']}/{store['max']} hearts", boss["emoji"]),
               common.stat("Wave", store.get("wave", 1), "🌊")],
        sections=secs, actions=acts, selects=selects)


# ---- the week's hunt, as a fight the activity can stage ----

# where each hunt is fought: one of the delve backdrops (public/skyrim/stage)
_WB_STAGE = {"marauder_king": "camp", "pale_lady": "vale", "risen_legion": "fort", "red_hand": "peak",
             "white_terror": "ice_cave", "otar": "barrow", "greymaw": "vale", "iron_colossus": "dwemer",
             "tide_mother": "cave", "sky_shadow": "peak"}
_HIT = re.compile(r"\(\*\*-(\d+)\*\*\)\.?$")
_ANSWER = re.compile(r"\(((?:❤️)+|💀 none) left\)\.?$")


def march_beats(lines, boss: dict) -> list[dict]:
    """wb_march's story, line by line, each with the cue that stages it: a blow landing or missing, the boss's
    answer, the hunt's turning points, the fall. Lines that are only story carry no cue."""
    misses = {m.rstrip(".") for m in boss.get("miss") or []} | {f"{boss['name']} turns your blow aside"}
    out = []
    for raw in lines:
        line = common.clean(raw)
        cue = None
        hit, answer = _HIT.search(line), _ANSWER.search(line)
        if hit:
            d = int(hit.group(1))
            cue = {"t": "hit", "dmg": d, "crit": d >= 2 or "CLEAN" in line, "style": "blade"}
        elif answer:
            left = 0 if "none" in answer.group(1) else answer.group(1).count("❤")
            cue = {"t": "hurt", "hearts": 2 if "CRUSHING" in line else 1, "crushing": "CRUSHING" in line, "left": left}
        elif line.rstrip(".") in misses:
            cue = {"t": "miss", "style": "blade"}
        elif "**The hunt turns**" in line or "**It is nearly done**" in line:
            cue = {"t": "beat"}
        elif "shield-bearers drag you clear" in line:
            cue = {"t": "down"}
        elif line.startswith("🏆") and boss["slain"] in line:
            cue = {"t": "kill", "crit": True, "boss": True}
        elif line.startswith("🌩️"):
            cue = {"t": "rise"}
        out.append({"line": line, "cue": cue})
    return out


async def _post_march_report(ctx, profile, boss, role, lines, dealt, slain, store):
    """The march told in the channel, the same report the Discord game posts. Best-effort."""
    client, ch_id = ctx.get("client"), ctx.get("ch")
    if client is None or not ch_id:
        return
    try:
        import discord
        ch = client.get_channel(int(ch_id))
        if ch is None or not hasattr(ch, "send"):
            return
        report, files, _head = V.march_report(int(profile["user_id"]), boss, role, lines, dealt, slain, store)
        await ch.send(view=report, files=files, allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        import logging
        logging.getLogger(__name__).warning("skyrim: couldn't post the activity march report", exc_info=True)


@view("notice")
def notice(profile, ctx):
    return _notice_panel(profile)


@act("notice")
async def notice_act(profile, ctx, action, body):
    toast, cues, extra = None, [], None
    if action == "claim":
        res = E.claim_tasks(profile)
        if not res:
            raise Refuse("Nothing to claim yet.")
        E.save_profile(profile)
        toast = f"🎁 {res}"
    elif action == "spoils":
        res = E.wb_claim(profile)        # saves the profile itself
        if not res:
            raise Refuse("Your share is already claimed.")
        toast = res
    elif action == "march":
        role = _one(body, "part")
        if role not in P.HUNT_ROLES:
            raise Refuse("Choose attack, expose or protect.")
        if not E.wb_available(profile):
            raise Refuse("No march is available right now.")
        before = E.world_boss()
        key, boss = before["boss"], E.wb_boss(before)     # the boss being fought: a killing blow summons the next wave
        pool = {"hp": int(before["hp"]), "max": int(before["max"]), "wave": int(before.get("wave", 1))}
        try:
            lines, dealt, slain, store = E.wb_march(profile, role=role)
        except ValueError as e:
            raise Refuse(str(e))
        E.save_profile(profile)
        spec = P.HUNT_ROLES[role]
        head = [f"{spec['emoji']} {spec['label']} · **{dealt} damage**",
                "🏆 Defeated · 0 HP" if slain else f"❤️ {store['hp']}/{store['max']} remain"]
        extra = [common.section(f"{boss['emoji']} {boss['name']}", head + list(lines)
                                + (["🏆 The wave falls. A stronger wave rises; your spoils are ready."] if slain else []))]
        march = {"boss": {"key": key, "name": common.clean(boss["name"]), "cut": f"enemy_wb_{key}", "art": boss["art"],
                          "stage": _WB_STAGE.get(key, "camp"), "dragon": boss.get("type") == "dragon"},
                 "role": {"key": role, "label": spec["label"]}, "hearts": E.heart_max(profile), "pool": pool,
                 "beats": march_beats(lines, boss), "dealt": int(dealt), "slain": bool(slain),
                 "after": {"hp": int(store["hp"]), "max": int(store["max"]), "wave": int(store.get("wave", 1)),
                           "next": common.clean(E.wb_boss(store)["name"]) if slain else None,
                           "nextKey": store["boss"] if slain else None}}
        await _post_march_report(ctx, profile, boss, role, lines, dealt, slain, store)
        await common.after(ctx, profile)
        out = common.result(_notice_panel(profile, extra), profile)
        out["march"] = march
        return out
    else:
        raise Refuse("You can't do that here.")
    await common.after(ctx, profile)
    return common.result(_notice_panel(profile, extra), profile, toast=toast, cues=cues)


# ---- daily board -----------------------------------------------------------------------------------------

@view("daily_board")
def daily_board(profile, ctx):
    loc = E.daily_location()
    blurb = common.lines(
        f"-# {E.weather_line()}  ·  same rooms for everyone, one attempt each, {E.DAILY_CLEAR_MULT:g}x clear bonus"
        + V._daily_marked_line() + V._daily_mood_line())
    results = E.daily_results()
    medals = ["🥇", "🥈", "🥉"]
    out = []
    if not results:
        out.append("No attempts yet today. The dungeon waits.")
    else:
        def sort_key(r):
            cleared = r["state"] == "cleared"
            return (not cleared, -r["satchel"] if cleared else -r["rooms"], -r["kills"])
        for i, r in enumerate(sorted(results.values(), key=sort_key)[:12]):
            cls = D.STONES.get(r.get("stone", r.get("class")), D.STONES["warrior"])
            rank = medals[i] if i < len(medals) else f"{i + 1}."
            if r["state"] == "cleared":
                outcome = f"✅ cleared  ·  💰 {r['satchel']:,}"
            elif r["state"] == "dead":
                outcome = f"💀 died in room {r['rooms'] + 1}"
            elif r["state"] == "launched":
                outcome = "🦣 launched into orbit"
            else:
                outcome = f"🚪 left after room {r['rooms']}"
            out.append(f"{rank} {cls['emoji']} **{r['name']}** - {outcome}  ·  ⚔️ {r['kills']}")
    return common.panel("daily_board", f"📅 Daily Delve - {loc['emoji']} {loc['name']}", art="notice_board",
                        back="notice", blurb=blurb, sections=[common.section("Today's board", out)])


# ---- the Pit ---------------------------------------------------------------------------------------------

def pit_art(rank: int) -> str:
    """The next champion's portrait, or the arena until their art is dropped."""
    if rank < len(D.PIT_CHAMPS):
        key = D.PIT_CHAMPS[rank].get("art")
        if have_art(key):
            return key
    return "pit"


def _pit_panel(profile: dict) -> dict:
    s = E.pit_state(profile)
    rank = int(s.get("rank", 0))
    lvl = E.level(profile)
    blurb = ["-# Fight while you win: each victory offers the next rung, but fatigue mounts (-6% per extra bout) "
             "and a loss ends your day. No satchel at stake - glory only. The board wipes clean each Monday (UK)."]
    if lvl < _MIN_PIT:
        blurb.append("-# 🔒 The Pit doesn't book novices (level 5+).")
    stand = [f"**Your standing:** {E.pit_title(rank)} (rank {rank}/{len(D.PIT_CHAMPS)})"
             + (f"  ·  best ever: {E.pit_title(int(s.get('best', 0)))}" if s.get("best") else "")]
    if rank < len(D.PIT_CHAMPS):
        champ = D.PIT_CHAMPS[rank]
        stand.append(f"**Next bout:** {champ['name']} - known for {champ['style']}.")
        stand.append(f"-# ⚠️ Word in the stands: {champ['quirk_desc']}.")
    else:
        stand.append("👑 **You ARE the Pit Champion.** Nothing left but to hold the title until Monday - "
                     "defend it next week.")
    board = sorted(((E.pit_state(p).get("rank", 0), p["name"]) for p in E.all_profiles().values()), reverse=True)
    board = [(r, n) for r, n in board if r > 0][:6]
    sections = [common.section("Your standing", stand)]
    if board:
        sections.append(common.section("This week's board",
                                       [f"**{n}** {E.pit_title(r)} ({r})" for r, n in board]))
    tales = [t for t in (profile.get("ghost_log") or [])[-2:]]
    if tales:
        sections.append(common.section("Word from the circle", tales))
    acts, selects = [], []
    if E.pit_bout_active(profile):
        acts.append(common.action("resume", "Return to your bout", "🗡️", "danger", nav="bout:pit"))
    elif lvl >= _MIN_PIT and E.pit_available(profile):
        acts.append(common.action("step_in", "Step into the Pit", "🗡️", "danger",
                                  hint=f"Bout {rank + 1}: {D.PIT_CHAMPS[rank]['name']}"))
    elif lvl >= _MIN_PIT and rank < len(D.PIT_CHAMPS):
        ending = {"lost": "Your day in the Pit ended on a loss.",
                  "draw": "Your day in the Pit ended in a stubborn draw."}
        sections.append(common.section("Rest", [
            f"-# 💤 {ending.get(s.get('last'), 'Your day in the Pit is spent.')} "
            f"Fresh legs at dawn - the crowd expects you tomorrow."]))
    duel = profile.get("duel") or {}
    if duel.get("bout"):
        acts.append(common.action("resume_duel", "Return to your duel", "⚔️", "danger", nav="bout:duel"))
    elif lvl >= _MIN_PIT:
        rivals = E.duel_rivals(profile)
        if rivals:
            opts = []
            for r in rivals[:25]:
                g = D.GHOST_QUIRKS.get(r.get("stone"), D.GHOST_QUIRKS["warrior"])
                h2h = (profile.get("rivals") or {}).get(str(r["user_id"]))
                tag = f"  ·  you {h2h['w']}-{h2h['l']}" if h2h else ""
                opts.append(common.option(str(r["user_id"]), f"{r['name']} (Lv {E.level(r)}){tag}", g["desc"],
                                          D.STONES[r["stone"]]["emoji"]))
            selects.append(common.select("duel", "⚔️ Duel a rival's ghost (once each per day)...", opts))
    return common.panel("pit", "The Pit - Windhelm", art=pit_art(rank), back="town", blurb=blurb,
                        stats=[common.stat("Rank", f"{rank}/{len(D.PIT_CHAMPS)}", "🗡️"),
                               common.stat("Title", E.pit_title(rank), "🏅")],
                        sections=sections, actions=acts, selects=selects)


@view("pit")
def pit(profile, ctx):
    return _pit_panel(profile)


@act("pit")
async def pit_act(profile, ctx, action, body):
    if action == "step_in":
        if E.pit_bout_active(profile):
            return common.result(_pit_panel(profile), profile, nav="bout:pit")
        if not (E.level(profile) >= _MIN_PIT and E.pit_available(profile)):
            raise Refuse("The Pit isn't open to you right now.")
        E.pit_begin(profile)
        E.save_profile(profile)
        await common.after(ctx, profile)
        return common.result(_pit_panel(profile), profile, nav="bout:pit")
    if action == "duel":
        if E.level(profile) < _MIN_PIT:
            raise Refuse("The duelling circle doesn't book novices (level 5+).")
        if profile.get("duel"):
            return common.result(_pit_panel(profile), profile, nav="bout:duel")
        uid = _one(body, "rival")
        if not uid.isdigit() or int(uid) == int(profile["user_id"]):
            raise Refuse("Pick a rival from the list.")
        rival = E.get_profile(int(uid))
        if rival is None:
            raise Refuse("That rival has gone.")
        if int(uid) in E._duel_day(profile)["fought"]:
            raise Refuse("You've already faced that ghost today.")
        E.duel_begin(profile, rival)
        E.save_profile(profile)
        await common.after(ctx, profile)
        return common.result(_pit_panel(profile), profile, nav="bout:duel")
    raise Refuse("You can't do that here.")


# ---- factions --------------------------------------------------------------------------------------------

def _factions_panel(profile: dict, confirm_key: str | None = None) -> dict:
    text = V._factions_text(profile)
    E.save_profile(profile)      # keep the week rollover
    fac_key = profile.get("allegiance")
    blocks = _by_blocks(text)
    intro = blocks[0]["lines"] if blocks else []
    sections = blocks[1:]
    stats = []
    mission = P.promotion(profile)
    acts, selects = [], []
    if mission:
        state = ("ready to claim" if mission["claimable"] else
                 f"needs {mission.get('favour_needed', 0)} favour" if not mission["eligible"] else "in progress")
        sections.insert(0, common.section(f"Promotion: {mission['label']}",
                                          [f"{mission['progress']}/{mission['goal']} · {state}"]))
        if mission["claimable"]:
            acts.append(common.action("promote", "Claim promotion", "🏅", "success"))
    if fac_key in D.FACTIONS:
        fac = D.FACTIONS[fac_key]
        goal, prog, _done = E.faction_progress(profile)
        stats = [common.stat(fac["name"], E.faction_rank(profile), fac["emoji"]),
                 common.stat("Favour", E.faction_favour(profile), "🏅"),
                 common.stat("This week", f"{prog}/{goal} {fac['verb']}", "📜")]
        if E.faction_claimable(profile):
            acts.append(common.action("claim", "Claim favour", "🏅", "success"))
    if E.level(profile) >= int(getattr(config, "SKYRIM_DRAGON_MIN_LEVEL", 8)):
        sworn = fac_key in D.FACTIONS
        opts = []
        for k, fac in D.FACTIONS.items():
            if k == fac_key:
                continue
            held = E.faction_favour(profile, k)
            blurb = f"Task: {fac['goal']} {fac['verb']}" + (f" · {E.faction_rank(profile, k)} there already" if held else "")
            opts.append(common.option(k, fac["name"], blurb, fac["emoji"]))
        selects.append(common.select("join", "🏰 Take your oath elsewhere..." if sworn else "Swear an allegiance...",
                                     opts))
    panel = common.panel("factions", "Factions of Skyrim", art=None, back="town", blurb=intro, stats=stats,
                         sections=sections, actions=acts, selects=selects)
    if confirm_key:
        panel = _confirm_state(profile, panel, confirm_key)
    return panel


def _confirm_state(profile: dict, panel: dict, key: str) -> dict:
    """_faction_confirm: switching guilds costs the rest of this week, so it asks first."""
    old_key = profile.get("allegiance")
    old = D.FACTIONS.get(old_key)
    new = D.FACTIONS[key]
    goal, prog, _done = E.faction_progress(profile)
    held = E.faction_favour(profile, key)
    lines = [f"Leave {old['emoji']} **{old['name']}** for {new['emoji']} **{new['name']}**?" if old
             else f"Swear to {new['emoji']} **{new['name']}**?"]
    if old:
        lines.append(f"-# Your **{E.faction_rank(profile, old_key)}** standing with {old['name']} "
                     f"(favour {E.faction_favour(profile, old_key)}) is kept - go back any time and it's waiting.")
    if old and prog and prog < goal:
        lines.append(f"-# ⚠️ This week's **{prog}/{goal} {old['verb']}** is lost. {new['name']} counts from zero.")
    lines.append(f"-# {new['seat']} sets you **{new['goal']} {new['verb']}**, over and over."
                 + (f" You're already **{E.faction_rank(profile, key)}** there." if held else ""))
    lines.append(f"-# The week's favour allowance is shared across all guilds: "
                 f"**{E.faction_claims_left(profile)}** left to claim, wherever you serve.")
    panel["sections"] = [common.section("A word with the guildmaster", lines)]
    panel["stats"] = []
    panel["selects"] = []
    panel["actions"] = [
        common.action(f"swear:{key}", "Swear the oath", "🤝", "primary",
                      confirm=common.clean(f"Swear to {new['name']}?")),
        common.action("stay", "Stay put", "⬅️", nav="factions")]
    return panel


@view("factions")
def factions(profile, ctx):
    return _factions_panel(profile)


@act("factions")
async def factions_act(profile, ctx, action, body):
    toast = None
    if action == "claim":
        if not E.faction_claimable(profile):
            raise Refuse("There's no favour to claim yet.")
        res = E.claim_faction(profile)
        E.save_profile(profile)
        toast = f"🏅 {res}" if res else None
    elif action == "promote":
        mission = P.promotion(profile)
        if not (mission and mission["claimable"]):
            raise Refuse("No promotion is waiting.")
        err = P.claim_promotion(profile)
        E.save_profile(profile)
        toast = err or "Your promotion is earned."
    elif action == "join":
        key = _one(body, "guild")
        if key not in D.FACTIONS:
            raise Refuse("No such faction.")
        if profile.get("allegiance") in D.FACTIONS:
            return common.result(_factions_panel(profile, confirm_key=key), profile)
        _refuse_if(E.join_faction(profile, key))
        E.save_profile(profile)
        toast = f"🤝 You run with {D.FACTIONS[key]['name']} now."
    elif action.startswith("swear:"):
        key = action.split(":", 1)[1]
        if key not in D.FACTIONS:
            raise Refuse("No such faction.")
        _refuse_if(E.join_faction(profile, key))
        E.save_profile(profile)
        toast = f"🤝 You run with {D.FACTIONS[key]['name']} now."
    else:
        raise Refuse("You can't do that here.")
    await common.after(ctx, profile)
    return common.result(_factions_panel(profile), profile, toast=toast)


# ---- holdings --------------------------------------------------------------------------------------------

def _holdings_art(profile: dict) -> str | None:
    built = E.homestead(profile)["built"]
    if "great_hall" in built:
        key = "homestead_3"
    elif len(built) >= 3:
        key = "homestead_2"
    elif built:
        key = "homestead_1"
    else:
        return None
    return key if have_art(key) else None


def _holdings_panel(profile: dict, finished: str | None = None) -> dict:
    hs = E.homestead(profile)
    pre, sections = _by_headings(V._holdings_text(profile))
    if finished:
        sections.insert(0, common.section("News from the estate", [finished]))
    acts, selects = [], []
    if "land" not in hs["built"]:
        land = D.HOMESTEAD["land"]
        acts.append(common.action("deed", "Buy the deed", "🏞️", "primary", hint=f"{land['septims']:,} septims"))
    if E.homestead_yield_days(profile):
        acts.append(common.action("collect", "Collect yields", "🎁", "success"))
    slots = E.expedition_slots(profile)
    for slot in slots:
        if E.expedition_ready(profile, slot):
            acts.append(common.action(
                f"haul:{slot}", "Collect the haul" if len(slots) == 1 else f"Collect haul {slot}", "🧭", "success"))
    buildable = E.homestead_buildable(profile)
    if "land" in hs["built"] and buildable and not hs.get("building"):
        opts = []
        for k in buildable[:25]:
            r = D.HOMESTEAD[k]
            opts.append(common.option(k, f"{r['name']} ({r['septims']:,} septims" + (f", {r['hours']}h" if r["hours"] else "") + ")",
                                      r["desc"], r["emoji"]))
        selects.append(common.select("build", "🔨 Commission the builders...", opts))
    can = E.level(profile) >= int(getattr(config, "SKYRIM_DRAGON_MIN_LEVEL", 8))
    if can and [s for s in slots if not E.expedition(profile, s)]:
        selects.append(common.select("expedition", "🧭 Send an expedition...", [
            common.option(k, f"{x['name']} ({x['days']}d)", x["desc"], x["emoji"]) for k, x in D.EXPEDITIONS.items()]))
    if E.homestead_built(profile, "hall"):
        selects.append(common.select("banner", "🚩 Raise a house banner...", [
            common.option(k, b["name"], b["line"], b["emoji"], hs.get("banner") == k)
            for k, b in D.HOUSE_BANNERS.items()]))
    if E.homestead_built(profile, "shrine_wing"):
        selects.append(common.select("shrine", "🕯️ Kneel at the shrine...", [
            common.option(k, b["name"], b["desc"], b["emoji"], hs.get("shrine") == k)
            for k, b in D.SHRINE_BLESSINGS.items()]))
    stats = [common.stat("Septims", f"{profile['septims']:,}", "💰"),
             common.stat("Rooms built", len(hs["built"]), "🏠")]
    if hs.get("building"):
        stats.append(common.stat(D.HOMESTEAD[hs["building"]]["name"], f"{E.homestead_hours_left(profile)}h left", "🔨"))
    return common.panel("holdings", "Holdings", art=_holdings_art(profile), back="town", blurb=pre, stats=stats,
                        sections=sections, actions=acts, selects=selects)


@view("holdings")
def holdings(profile, ctx):
    finished = E.homestead_check(profile)
    E.save_profile(profile)
    return _holdings_panel(profile, finished)


@act("holdings")
async def holdings_act(profile, ctx, action, body):
    toast = None
    if action == "deed":
        _refuse_if(E.start_building(profile, "land"))
        toast = "🏞️ The deed is yours. The lake is lovely this time of year."
    elif action == "collect":
        res = E.collect_homestead(profile)
        if not res:
            raise Refuse("Nothing waiting.")
        toast = res
    elif action == "haul" or action.startswith("haul:"):
        # the slot rides on the action ("haul:2"), or in the body from an older page
        vals = [action.split(":", 1)[1]] if ":" in action else _values(body)
        try:
            slot = int(vals[0]) if vals else E.expedition_slots(profile)[0]
        except ValueError:
            raise Refuse("That isn't one of your housecarls.")
        if slot not in E.expedition_slots(profile) or not E.expedition_ready(profile, slot):
            raise Refuse("No haul is waiting there.")
        res = E.collect_expedition(profile, slot)
        if not res:
            raise Refuse("Nothing waiting.")
        toast = f"🎁 {res}"
    elif action == "build":
        key = _one(body, "room")
        _refuse_if(E.start_building(profile, key))
        r = D.HOMESTEAD[key]
        toast = f"🔨 The builders begin on {r['name']} - ready in {r['hours']}h."
    elif action == "expedition":
        key = _one(body, "errand")
        _refuse_if(E.start_expedition(profile, key))
        toast = f"🧭 Off they go on {D.EXPEDITIONS[key]['name']}."
    elif action == "banner":
        key = _one(body, "banner")
        _refuse_if(E.set_banner(profile, key))
        b = D.HOUSE_BANNERS[key]
        toast = f"🚩 {b['emoji']} {b['name']} takes the wind above the hall."
    elif action == "shrine":
        key = _one(body, "blessing")
        _refuse_if(E.set_shrine(profile, key))
        b = D.SHRINE_BLESSINGS[key]
        toast = f"🕯️ {b['emoji']} {b['name']} settles over the estate."
    else:
        raise Refuse("You can't do that here.")
    E.save_profile(profile)
    await common.after(ctx, profile)
    return common.result(_holdings_panel(profile), profile, toast=toast)


# ---- rankings --------------------------------------------------------------------------------------------

def _rankings_panel(profile: dict, board: str = "legends") -> dict:
    if board not in V._RANK_BOARDS:
        board = "legends"
    emoji, title, sub = V._RANK_BOARDS[board]
    hunt_store = E.world_boss()
    scored = []
    for uid, p in E.all_profiles().items():
        value, detail = V._rank_metric(board, uid, p, hunt_store)
        scored.append((value, int(p["xp"]), str(uid), p, detail))
    scored.sort(key=lambda r: (-r[0], -r[1]))
    shown = [r for r in scored if board == "legends" or r[0] > 0][:10]
    out = []
    if not shown:
        out.append("No names on this board yet. The ruins wait.")
    medals = ["🥇", "🥈", "🥉"]
    for i, (_v, _xp, _uid, p, detail) in enumerate(shown):
        cls = D.STONES[p["stone"]]
        rank = medals[i] if i < len(medals) else f"{i + 1}."
        if board == "legends":
            out.append(f"{rank} {cls['emoji']} **{p['name']}**{detail}")
        else:
            out.append(f"{rank} {cls['emoji']} **{p['name']}** - Lv {E.level(p)}  ·  {detail}")
    me = str(profile["user_id"])
    pos = next((i for i, r in enumerate(scored) if r[2] == me), None)
    if pos is not None and pos >= len(shown):
        out += ["", f"-# Your seat: #{pos + 1} of {len(scored)}."]
    secs = [common.section(f"{emoji} {title}", out)]
    if board == "legends":
        obit = E.latest_obituary()
        if obit:
            secs.append(common.section("Fallen adventurers", [obit]))
    opts = [common.option(k, t, s, e, k == board) for k, (e, t, s) in V._RANK_BOARDS.items()]
    return common.panel("rankings", "Rankings", art=None, back="town", blurb=[f"-# {sub}"], sections=secs,
                        selects=[common.select("board", "🏅 Another board...", opts)])


@view("rankings")
def rankings(profile, ctx):
    return _rankings_panel(profile)


@act("rankings")
async def rankings_act(profile, ctx, action, body):
    if action != "board":
        raise Refuse("You can't do that here.")
    board = _one(body, "board")
    if board not in V._RANK_BOARDS:
        raise Refuse("There's no such board.")
    return common.result(_rankings_panel(profile, board), profile)


# ---- help ------------------------------------------------------------------------------------------------

# the activity's own first page: the whole loop in seven short steps, for the screen in front of you (the Discord
# chapters behind it are the full rules, written for the bot's buttons)
_GUIDE = [
    "**1. Go adventuring.** Tap Adventure, pick a banner on the map and set out. Green is easy, red is hard.",
    "**2. Fight.** Tap Blade, Bow or Fire. The number on each is your chance to hit.",
    "**3. Read the foe.** The sign over it is its next move. The glowing button answers it best.",
    "**4. Stay alive.** Run out of hearts and your satchel's loot stays behind. Drink a potion, or Bank to walk out with it.",
    "**5. Spend it in town.** Better gear at Belethor's, perk points on your Character, tasks on the Notice Board.",
    "**6. Not sure what next?** Follow the gold marker in town.",
    "Adventures refill through the day, up to 12. The Pit opens at level 5.",
]


def _help_panel(profile: dict, page: str = "guide") -> dict:
    opts = [common.option("guide", "How to play", "", "🧭", page == "guide")] + [
        common.option(k, lab, "", em or "📖", k == page) for k, (em, lab, _t) in V.HELP_PAGES.items()]
    if page not in V.HELP_PAGES:
        return common.panel("help", "How to play", art="help", back="town", sections=[common.section("", _GUIDE)],
                            selects=[common.select("page", "📖 More chapters...", opts)])
    emoji, label, text = V.HELP_PAGES[page]
    foot = (f"-# A delve returns at {', '.join(f'{h:02d}:00' for h in E._slot_hours())} UK and they stack up to "
            f"{getattr(config, 'SKYRIM_DELVE_MAX_STORED', 12)} while you're away - no midnight rush. "
            f"No UKPence involved anywhere - glory only.")
    blocks = _by_blocks(text)
    if blocks:
        blocks[0]["title"] = blocks[0]["title"] or label
    blocks.append(common.section("", [foot]))
    return common.panel("help", f"{emoji} {label}", art=None, back="town", sections=blocks,
                        selects=[common.select("page", "📖 More chapters...", opts)])


@view("help")
def help_(profile, ctx):
    return _help_panel(profile)


@act("help")
async def help_act(profile, ctx, action, body):
    if action != "page":
        raise Refuse("You can't do that here.")
    page = _one(body, "chapter")
    if page not in V.HELP_PAGES and page != "guide":
        raise Refuse("There's no such chapter.")
    return common.result(_help_panel(profile, page), profile)
