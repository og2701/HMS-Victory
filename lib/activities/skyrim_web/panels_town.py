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


def _ord(n: int) -> str:
    n = int(n)
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _short(name: str, words: int = 3) -> str:
    """A player's name for a tile title: the first few words."""
    return " ".join(str(name).split()[:words]) or "?"


def _hero(stone: str | None) -> str:
    """A player's portrait for a tile."""
    return f"c:hero_{stone}_idle|i:sword" if stone in D.STONES else "i:sword"


def _toast(text) -> str:
    return common.clean(str(text).split("\n")[0]) if text else ""


# ---- notice board ----------------------------------------------------------------------------------------

# a few words for each weekly task (the full name shows on tap)
_TASK_SHORT = {
    "clears_3": "Clear 3 delves", "kills_20": "Slay 20 foes", "chests_5": "Loot 5 chests", "daily_2": "Daily twice",
    "sneaks_3": "Sneak 3 foes", "blade_12": "12 Blade kills", "bow_12": "12 Bow kills", "fire_12": "12 Fire kills",
    "clear_hard": "Clear Hard", "clear_dry": "Dry clear", "bounties_2": "2 bounties", "persuades_4": "4 parleys",
    "pit_wins_2": "2 Pit wins", "blade_only": "Blade only", "bow_only": "Bow only", "fire_only": "Fire only",
    "dragon_dry": "Dry dragon", "clear_stirred": "Stirred clear", "deep_clear": "Deep clear",
    "pit_unwounded": "Flawless Pit", "dragons_3": "3 dragons",
}
_TASK_ICON = (("clear", "i:door"), ("delve", "i:door"), ("deep", "i:door"), ("kill", "i:skull"), ("chest", "i:coin"),
              ("daily", "i:star"), ("sneak", "i:hood"), ("blade", "i:sword"), ("bow", "i:bow"), ("fire", "i:flame"),
              ("bount", "i:skull"), ("persuade", "i:speech"), ("pit", "i:shield"), ("dragon", "i:wing"))


def _task_icon(key: str) -> str:
    return next((icon for frag, icon in _TASK_ICON if frag in key), "i:star")


def _notice_tasks(profile: dict) -> list[dict]:
    """The week's tasks as tiles: what, how far, what it pays. A finished one is tapped to claim."""
    out = []
    for key, t, done, comp, claimed in E.task_progress(profile):
        septims, _xp = D.TASK_REWARDS[t["band"]]
        short = _TASK_SHORT.get(key) or " ".join(t["name"].split()[:3])
        meter = (min(done, t["n"]), t["n"]) if t["n"] > 1 else None
        if claimed:
            out.append(common.tile(short, icon=_task_icon(key), cost=septims, meter=meter, state="done", sub="Claimed",
                                   info=t["name"]))
        elif comp:
            out.append(common.tile(short, icon=_task_icon(key), cost=septims, meter=meter, state="ready", sub="Claim",
                                   act="claim"))
        else:
            out.append(common.tile(short, icon=_task_icon(key), cost=septims, meter=meter, info=t["name"]))
    return out


def _notice_hunt(profile: dict, store: dict, boss: dict) -> list[dict]:
    """The week's hunt as one big tile (the boss, what's left of it) and, when you can march, the three parts."""
    wave = int(store.get("wave", 1))
    name = boss["name"].split(",")[0]
    if E.level(profile) < E.WB_MIN_LEVEL:
        state, sub, info = "locked", "Level 5", f"The hunt opens at level {E.WB_MIN_LEVEL}."
    elif E.wb_marched_today(profile, store):
        state, sub, info = "done", "Marched today", "The line re-forms at dawn."
    else:
        state, sub, info = "ready", f"Wave {wave}", common.clean(boss["blurb"])
    return [common.tile(name, icon=f"c:enemy_wb_{store['boss']}|i:skull", meter=(store["hp"], store["max"]),
                        sub=sub, state=state, info=info)]


def _notice_panel(profile: dict) -> dict:
    tasks = _notice_tasks(profile)
    E.save_profile(profile)      # keep the weekly tracker rollover, as views does
    store = E.world_boss()
    boss = E.wb_boss(store)
    pts, total = E.task_points(profile)
    today = []
    if E.daily_available(profile):
        today.append(common.tile("Daily delve", icon="s:notice_board|i:door", sub=E.daily_location()["name"],
                                 state="ready", nav="adventure:daily"))
    else:
        today.append(common.tile("Daily delve", icon="s:notice_board|i:door", state="done", sub="Done today",
                                 info="Back tomorrow."))
    today.append(common.tile("Daily board", icon="i:crown", nav="daily_board"))
    if E.wb_share_waiting(profile):
        today.append(common.tile("Spoils", icon="i:coin", sub="Hunt share", state="ready", act="spoils"))
    sections = [common.section("Today", tiles=today),
                common.section(f"Tasks {pts}/{total}", tiles=tasks),
                common.section("The hunt", tiles=_notice_hunt(profile, store, boss), cols=1)]
    if E.wb_available(profile):
        hint = {"attack": "Full damage", "expose": "Next ally hits", "protect": "Next ally guards"}
        icon = {"attack": "i:sword", "expose": "i:eye", "protect": "i:shield"}
        sections.append(common.section("March", tiles=[
            common.tile(spec["label"], icon=icon[role], sub=hint[role], state="ready", act="march", body={"value": role},
                        info=spec["hint"])
            for role, spec in P.HUNT_ROLES.items()], cols=3))
    return common.panel(
        "notice", "The Notice Board", art="notice_board", back="town",
        blurb=["-# Tasks pay when finished. One march a day on the week's hunt.", common.clean(boss["blurb"])],
        sections=sections)


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
    toast = None
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
        march = {"boss": {"key": key, "name": common.clean(boss["name"]), "cut": f"enemy_wb_{key}", "art": boss["art"],
                          "stage": _WB_STAGE.get(key, "camp"), "dragon": boss.get("type") == "dragon"},
                 "role": {"key": role, "label": spec["label"]}, "hearts": E.heart_max(profile), "pool": pool,
                 "beats": march_beats(lines, boss), "dealt": int(dealt), "slain": bool(slain),
                 "after": {"hp": int(store["hp"]), "max": int(store["max"]), "wave": int(store.get("wave", 1)),
                           "next": common.clean(E.wb_boss(store)["name"]) if slain else None,
                           "nextKey": store["boss"] if slain else None}}
        await _post_march_report(ctx, profile, boss, role, lines, dealt, slain, store)
        await common.after(ctx, profile)
        out = common.result(_notice_panel(profile), profile)
        out["march"] = march
        return out
    else:
        raise Refuse("You can't do that here.")
    await common.after(ctx, profile)
    return common.result(_notice_panel(profile), profile, toast=toast)


# ---- daily board -----------------------------------------------------------------------------------------

@view("daily_board")
def daily_board(profile, ctx):
    loc = E.daily_location()
    blurb = common.lines(
        f"-# {E.weather_line()}  ·  same rooms for everyone, one attempt each, {E.DAILY_CLEAR_MULT:g}x clear bonus"
        + V._daily_marked_line() + V._daily_mood_line())
    results = E.daily_results()
    me = str(profile["user_id"])
    sections = []
    if not results:
        sections.append(common.section("Today's board", ["No attempts yet."]))
    else:
        def sort_key(r):
            cleared = r["state"] == "cleared"
            return (not cleared, -r["satchel"] if cleared else -r["rooms"], -r["kills"])
        ranked = sorted(results.items(), key=lambda kv: sort_key(kv[1]))[:12]
        tiles = []
        for i, (uid, r) in enumerate(ranked):
            stone = r.get("stone", r.get("class"))
            cost, kills = None, f"{r['kills']} kills"
            if r["state"] == "cleared":
                outcome, cost = "Cleared", r["satchel"]
            elif r["state"] == "dead":
                outcome = f"Died room {r['rooms'] + 1}"
            elif r["state"] == "launched":
                outcome = "Launched"
            else:
                outcome = f"Left room {r['rooms']}"
            tiles.append(common.tile(_short(r["name"]), icon=_hero(stone), value=_ord(i + 1), cost=cost, sub=outcome,
                                     state="done" if str(uid) == me else None, info=f"{outcome} - {kills}"))
        sections.append(common.section("Podium", tiles=tiles[:3], cols=3))
        if tiles[3:]:
            sections.append(common.section("The rest", tiles=tiles[3:]))
    return common.panel("daily_board", f"Daily - {loc['name']}", art="notice_board", back="notice", blurb=blurb,
                        sections=sections)


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
    n = len(D.PIT_CHAMPS)
    blurb = ["-# Fight while you win: each victory offers the next rung, but fatigue mounts (-6% per extra bout) "
             "and a loss ends your day. No satchel at stake - glory only. The board wipes clean each Monday (UK)."]
    if lvl < _MIN_PIT:
        blurb.append("-# The Pit doesn't book novices (level 5+).")
    ladder = [common.tile(E.pit_title(rank), icon="i:crown", value=f"{rank}/{n}", meter=(rank, n),
                          sub=f"Best {E.pit_title(int(s['best']))}" if s.get("best") else None,
                          state="max" if rank >= n else None)]
    sections = [common.section("Ladder", tiles=ladder, cols=1)]
    # the next bout
    if E.pit_bout_active(profile):
        bout = [common.tile("Return to bout", icon=f"s:{pit_art(rank)}", sub="Still fighting", state="ready",
                            nav="bout:pit")]
    elif rank >= n:
        bout = [common.tile("Pit Champion", icon="s:pit_master|i:crown", state="done", sub="Hold the title",
                            info="Nothing left but to hold the title until Monday.")]
    else:
        champ = D.PIT_CHAMPS[rank]
        info = common.clean(f"Word in the stands: {champ['quirk_desc']}.")
        if lvl < _MIN_PIT:
            bout = [common.tile(champ["name"], icon=f"s:{pit_art(rank)}", sub="Level 5", state="locked",
                                info="The Pit doesn't book novices (level 5+).")]
        elif E.pit_available(profile):
            bout = [common.tile(champ["name"], icon=f"s:{pit_art(rank)}", value=f"Bout {rank + 1}", sub=champ["style"],
                                state="ready", act="step_in", info=info)]
        else:
            bout = [common.tile(champ["name"], icon=f"s:{pit_art(rank)}", sub="Back at dawn", state="locked",
                                info="Your day in the Pit is spent. Fresh legs at dawn.")]
    sections.append(common.section("Next bout", tiles=bout, cols=1))
    # ghost duels
    duel = profile.get("duel") or {}
    if duel.get("bout"):
        sections.append(common.section("Ghost duel", tiles=[
            common.tile("Return to duel", icon="s:duel_circle", sub="Still fighting", state="ready", nav="bout:duel")]))
    elif lvl >= _MIN_PIT:
        rivals = E.duel_rivals(profile)
        if rivals:
            tiles = []
            for r in rivals[:12]:
                g = D.GHOST_QUIRKS.get(r.get("stone"), D.GHOST_QUIRKS["warrior"])
                h2h = (profile.get("rivals") or {}).get(str(r["user_id"]))
                tiles.append(common.tile(_short(r["name"]), icon=_hero(r.get("stone")), value=f"Lv {E.level(r)}",
                                         sub=f"You {h2h['w']}-{h2h['l']}" if h2h else g["quirk"].capitalize(),
                                         state="ready", act="duel", body={"value": str(r["user_id"])},
                                         info=common.clean(g["desc"])))
            sections.append(common.section("Ghost duel", tiles=tiles))
    # the week's board
    board = sorted(((E.pit_state(p).get("rank", 0), p["name"], str(p["user_id"])) for p in E.all_profiles().values()),
                   reverse=True)
    board = [b for b in board if b[0] > 0][:6]
    if board:
        me = str(profile["user_id"])
        sections.append(common.section("This week", tiles=[
            common.tile(_short(nm), icon="i:shield", value=_ord(i + 1), sub=E.pit_title(r),
                        state="done" if uid == me else None, info=f"Rank {r}/{n}")
            for i, (r, nm, uid) in enumerate(board)], cols=3))
    for tale in (profile.get("ghost_log") or [])[-2:]:
        blurb.append(common.clean(tale))
    return common.panel("pit", "The Pit - Windhelm", art=pit_art(rank), back="town", blurb=blurb, sections=sections)


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

def _fac_icon(key: str) -> str:
    return f"a:faction_{key}|i:shield"


def _factions_panel(profile: dict, confirm_key: str | None = None) -> dict:
    text = V._factions_text(profile)
    E.save_profile(profile)      # keep the week rollover
    fac_key = profile.get("allegiance")
    blurb = []
    for blk in _by_blocks(text):
        blurb += ([f"**{blk['title']}**"] if blk["title"] else []) + blk["lines"]
    gate = int(getattr(config, "SKYRIM_DRAGON_MIN_LEVEL", 8))
    can_join = E.level(profile) >= gate
    guilds = []
    for k, fac in D.FACTIONS.items():
        name = fac["name"].replace("The ", "")
        held = E.faction_favour(profile, k)
        if k == fac_key:
            idx = P.faction_rank_index(profile, k)
            guilds.append(common.tile(name, icon=_fac_icon(k), state="done", sub=E.faction_rank(profile, k),
                                      meter=(held, max(held, 2 * (idx + 1))), info=common.clean(fac["blurb"])))
        elif not can_join:
            guilds.append(common.tile(name, icon=_fac_icon(k), state="locked", sub=f"Level {gate}",
                                      info=f"Factions open at level {gate}."))
        else:
            guilds.append(common.tile(name, icon=_fac_icon(k), sub=E.faction_rank(profile, k) if held else "Swear oath",
                                      act="join", body={"value": k},
                                      info=common.clean(f"{fac['blurb']} Task: {fac['goal']} {fac['verb']}.")))
    sections = [common.section("Guilds", tiles=guilds, cols=3)]
    weekly = []
    if fac_key in D.FACTIONS:
        fac = D.FACTIONS[fac_key]
        goal, prog, done = E.faction_progress(profile)
        if E.faction_claimable(profile):
            weekly.append(common.tile(f"{goal} {fac['verb']}", icon="i:star", meter=(goal, goal), state="ready",
                                      sub="Claim favour", act="claim"))
        else:
            weekly.append(common.tile(f"{goal} {fac['verb']}", icon="i:star", meter=(min(prog, goal), goal),
                                      state="done" if done else None, sub="Done" if done else "This week",
                                      info="This week's favour allowance is spent." if done else None))
    mission = P.promotion(profile)
    if mission:
        if mission["claimable"]:
            weekly.append(common.tile("Promotion", icon="i:crown", meter=(mission["progress"], mission["goal"]),
                                      state="ready", sub="Claim", act="promote", info=mission["label"]))
        elif not mission["eligible"]:
            weekly.append(common.tile("Promotion", icon="i:crown", meter=(mission["progress"], mission["goal"]),
                                      state="locked", sub=f"Needs {mission.get('favour_needed', 0)} favour",
                                      info=mission["label"]))
        else:
            weekly.append(common.tile("Promotion", icon="i:crown", meter=(mission["progress"], mission["goal"]),
                                      sub=" ".join(mission["label"].split()[:3]),
                                      info=mission["label"]))
    if weekly:
        sections.append(common.section("This week", tiles=weekly))
    panel = common.panel("factions", "Factions of Skyrim", art=None, back="town", blurb=blurb, sections=sections)
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
    lines = [f"Leave {old['name']} for {new['name']}?" if old else f"Swear to {new['name']}?"]
    if old:
        lines.append(f"Your {E.faction_rank(profile, old_key)} standing with {old['name']} "
                     f"(favour {E.faction_favour(profile, old_key)}) is kept - go back any time and it's waiting.")
    if old and prog and prog < goal:
        lines.append(f"This week's {prog}/{goal} {old['verb']} is lost. {new['name']} counts from zero.")
    lines.append(f"{new['seat']} sets you {new['goal']} {new['verb']}, over and over."
                 + (f" You're already {E.faction_rank(profile, key)} there." if held else ""))
    lines.append(f"The week's favour allowance is shared across all guilds: "
                 f"{E.faction_claims_left(profile)} left to claim, wherever you serve.")
    lost = f"Loses {prog}/{goal}" if old and prog and prog < goal else None
    panel["blurb"] = lines
    panel["sections"] = [common.section("Swear?", tiles=[
        common.tile(new["name"].replace("The ", ""), icon=_fac_icon(key), sub=lost or "Swear oath", state="ready",
                    act=f"swear:{key}", confirm=common.clean(f"Swear to {new['name']}?")),
        common.tile("Stay put", icon="i:door", nav="factions")])]
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


_ROOM_ICON = {"land": "i:star", "hall": "i:door", "garden": "i:heartGreen", "brewery": "i:flask", "watchtower": "i:eye",
              "trophy_wing": "i:crown", "shrine_wing": "i:blessed", "quarters": "i:hood", "great_hall": "i:crown",
              "stables": "i:run", "greenhouse": "i:heartGreen", "cellar": "i:flask", "library": "i:spell",
              "observatory": "i:eye", "armoury": "i:shield", "war_room": "i:sword"}
_BANNER_ICON = {"wolf": "i:hood", "bear": "i:shield", "dragon": "i:wing", "hawk": "i:eye", "moons": "i:diamond",
                "blades": "i:sword"}
_SHRINE_ICON = {"battle": "i:sword", "warding": "i:shield", "learning": "i:spell"}


def _room_name(key: str) -> str:
    return D.HOMESTEAD[key]["name"].replace("The ", "", 1)


def _days_left(date_str: str) -> int:
    import datetime
    return max(1, (datetime.date.fromisoformat(date_str) - datetime.date.fromisoformat(E._today_str())).days)


def _holdings_panel(profile: dict, finished: str | None = None) -> dict:
    hs = E.homestead(profile)
    gate = int(getattr(config, "SKYRIM_DRAGON_MIN_LEVEL", 8))
    sections = []
    if finished:
        sections.append(common.section("News", [finished]))
    pts = profile["septims"]
    if "land" not in hs["built"]:
        land = D.HOMESTEAD["land"]
        sections.append(common.section("The estate", tiles=[
            common.tile("Lakeview deed", icon="s:homestead_1|i:star", cost=land["septims"], state="ready",
                        act="deed", info=common.clean(land["desc"]))], cols=1))
    else:
        built = [common.tile(_room_name(k), icon=_ROOM_ICON.get(k, "i:star"), state="done",
                             info=common.clean(D.HOMESTEAD[k]["desc"])) for k in D.HOMESTEAD if k in hs["built"]]
        if hs.get("building"):
            k = hs["building"]
            built.append(common.tile(_room_name(k), icon=_ROOM_ICON.get(k, "i:star"), sub=f"{E.homestead_hours_left(profile)}h left",
                                     value="...", info=common.clean(D.HOMESTEAD[k]["desc"])))
        sections.append(common.section("The estate", tiles=built, cols=3))
        days = E.homestead_yield_days(profile)
        if days:
            sections.append(common.section("Yields", tiles=[
                common.tile("Collect yields", icon="i:heartGreen", sub=f"{days} day{'s' if days != 1 else ''}",
                            state="ready", act="collect")], cols=1))
        buildable = E.homestead_buildable(profile)
        if buildable and not hs.get("building"):
            tiles = []
            for k in buildable[:25]:
                r = D.HOMESTEAD[k]
                bits = ([f"{r['hours']}h"] if r["hours"] else []) + (["+ mats"] if r["mats"] else [])
                tiles.append(common.tile(_room_name(k), icon=_ROOM_ICON.get(k, "i:star"), cost=r["septims"],
                                         sub=" ".join(bits) or None, act="build", body={"value": k},
                                         state=None if pts >= r["septims"] else "locked",
                                         info=common.clean(r["desc"]) if pts >= r["septims"]
                                         else f"Needs {r['septims']:,} septims."))
            sections.append(common.section("Build", tiles=tiles))
    # expeditions
    slots = E.expedition_slots(profile)
    can = E.level(profile) >= gate
    exp_tiles = []
    for slot in slots:
        e = E.expedition(profile, slot)
        if not e:
            continue
        x = D.EXPEDITIONS[e["key"]]
        icon = f"a:expedition_{e['key']}|i:run"
        if E.expedition_ready(profile, slot):
            exp_tiles.append(common.tile(x["name"], icon=icon, state="ready", sub="Collect", act=f"haul:{slot}"))
        else:
            n = _days_left(e["return"])
            exp_tiles.append(common.tile(x["name"], icon=icon, sub=f"{n} day{'s' if n != 1 else ''} left",
                                         info=common.clean(x["desc"])))
    if exp_tiles:
        sections.append(common.section("Expeditions", tiles=exp_tiles))
    if [sl for sl in slots if not E.expedition(profile, sl)]:
        if can:
            sections.append(common.section("Send out", tiles=[
                common.tile(x["name"], icon=f"a:expedition_{k}|i:run", sub=f"{x['days']} day{'s' if x['days'] != 1 else ''}",
                            act="expedition", body={"value": k}, info=common.clean(x["desc"]))
                for k, x in D.EXPEDITIONS.items()], cols=3))
        else:
            sections.append(common.section("Send out", tiles=[
                common.tile("Housecarl", icon="i:run", state="locked", sub=f"Level {gate}",
                            info=f"You earn a housecarl to send at level {gate}.")], cols=1))
    if E.homestead_built(profile, "hall"):
        sections.append(common.section("House banner", tiles=[
            common.tile(b["name"].replace("The ", ""), icon=_BANNER_ICON.get(k, "i:star"),
                        state="done" if hs.get("banner") == k else None, act="banner", body={"value": k},
                        info=common.clean(b["line"]))
            for k, b in D.HOUSE_BANNERS.items()], cols=3))
    if E.homestead_built(profile, "shrine_wing"):
        sections.append(common.section("Shrine", tiles=[
            common.tile(b["name"].replace("Blessing of ", ""), icon=_SHRINE_ICON.get(k, "i:blessed"), sub=b["desc"],
                        state="done" if hs.get("shrine") == k else None, act="shrine", body={"value": k})
            for k, b in D.SHRINE_BLESSINGS.items()], cols=3))
    blurb = ["-# The lakeside estate your legends leave behind - built room by room over real days, providing while "
             "you're away. Expeditions run from here too."]
    return common.panel("holdings", "Holdings", art=_holdings_art(profile), back="town", blurb=blurb, sections=sections)


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

_BOARD_SHORT = {"legends": ("Legends", "i:crown"), "wealth": ("Coffers", "i:coin"), "slayers": ("Slayers", "i:wing"),
                "hunters": ("Hunt", "i:sword"), "duellists": ("Duels", "i:shield"), "streaks": ("Streaks", "i:flame"),
                "depths": ("Depths", "i:skull"), "wonders": ("Wonders", "i:diamond")}


def _rank_cell(board: str, value: int, p: dict) -> tuple[int | None, str]:
    """(septims, a couple of words) for one row of a board."""
    plural = lambda n, w: f"{n} {w}" + ("" if n == 1 else "s")
    if board == "wealth":
        return int(value), "Septims"
    if board == "slayers":
        return None, plural(value, "dragon")
    if board == "hunters":
        return None, f"{value} dmg"
    if board == "duellists":
        return None, plural(value, "ghost")
    if board == "streaks":
        return None, plural(value, "day")
    if board == "depths":
        return None, f"Depth {value}"
    if board == "wonders":
        return None, plural(value, "wonder")
    return None, f"Lv {E.level(p)}"


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
    me = str(profile["user_id"])
    rows = []
    for i, (v, _xp, uid, p, _detail) in enumerate(shown):
        cost, words = _rank_cell(board, v, p)
        rows.append(common.tile(_short(p["name"]), icon=_hero(p.get("stone")), value=_ord(i + 1), cost=cost, sub=words,
                                state="done" if uid == me else None))
    pos = next((i for i, r in enumerate(scored) if r[2] == me), None)
    if pos is not None and pos >= len(shown):
        cost, words = _rank_cell(board, scored[pos][0], profile)
        rows.append(common.tile("You", icon=_hero(profile.get("stone")), value=_ord(pos + 1), cost=cost, sub=words,
                                state="done"))
    secs = [common.section("Boards", tiles=[
                common.tile(_BOARD_SHORT[k][0], icon=_BOARD_SHORT[k][1], state="done" if k == board else None,
                            act="board", body={"value": k}) for k in V._RANK_BOARDS], cols=4),
            common.section(title, ["No names yet."] if not rows else (), tiles=rows)]
    blurb = [f"-# {sub}"]
    if board == "legends":
        obit = E.latest_obituary()
        if obit:
            blurb.append(f"Fallen adventurers: {common.clean(obit)}")
    return common.panel("rankings", "Rankings", art=None, back="town", blurb=blurb, sections=secs)


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

# the activity's own first page: the whole loop as six tiles (the full sentence on tap); the Discord chapters behind
# it are the full rules, written for the bot's buttons
_GUIDE = [
    ("Go adventuring", "i:door", "Pick a banner",
     "Tap Adventure, pick a banner on the map and set out. Green is easy, red is hard."),
    ("Fight", "i:sword", "Blade, Bow, Fire", "Tap Blade, Bow or Fire. The number on each is your chance to hit."),
    ("Read the foe", "i:eye", "Answer its sign",
     "The sign over it is its next move. The glowing button answers it best."),
    ("Stay alive", "i:heart", "Potion or Bank",
     "Run out of hearts and your satchel's loot stays behind. Drink a potion, or Bank to walk out with it."),
    ("Spend in town", "i:coin", "Gear, perks, tasks",
     "Better gear at Belethor's, perk points on your Character, tasks on the Notice Board."),
    ("What's next?", "i:star", "Follow the gold", "Not sure what next? Follow the gold marker in town."),
]


def _help_panel(profile: dict, page: str = "guide") -> dict:
    opts = [common.option("guide", "How to play", "", "🧭", page == "guide")] + [
        common.option(k, lab, "", em or "📖", k == page) for k, (em, lab, _t) in V.HELP_PAGES.items()]
    if page not in V.HELP_PAGES:
        return common.panel("help", "How to play", art="help", back="town",
                            blurb=["-# Adventures refill through the day, up to 12. The Pit opens at level 5."],
                            sections=[common.section("", tiles=[common.tile(t, icon=i, sub=sub, info=info)
                                                                for t, i, sub, info in _GUIDE])],
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
