"""The town panels and bouts of the /skyrim activity API, driven headless through the real engine."""
import asyncio
import random

import pytest

import config
from lib.activities.skyrim_web import bouts, common, panels_town  # noqa: F401  (registers the panels)
from lib.activities.skyrim_web.registry import PANELS
from lib.core import file_operations as F
from lib.features.skyrim import data as D
from lib.features.skyrim import engine as E

PANEL_KEYS = ["notice", "daily_board", "pit", "factions", "holdings", "rankings", "help"]


@pytest.fixture(autouse=True)
def game(tmp_path, monkeypatch):
    for k in ("SKYRIM_PROFILES_FILE", "SKYRIM_DAILY_FILE", "SKYRIM_GRAVEYARD_FILE", "SKYRIM_WORLDBOSS_FILE",
              "PERSISTENT_VIEWS_FILE"):
        monkeypatch.setattr(config, k, str(tmp_path / f"{k}.json"))
    monkeypatch.setattr(F, "PERSISTENT_VIEWS_FILE", str(tmp_path / "PERSISTENT_VIEWS_FILE.json"))
    random.seed(7)
    E.create_profile(1, "Tester", "warrior")
    E.drain_log()


def ctx(uid=1):
    return {"uid": uid, "name": "Tester", "client": None, "ch": None}


def run(coro):
    return asyncio.run(coro)


def level_up(uid=1, lvl=12):
    p = E.get_profile(uid)
    p["xp"] = sum(D.xp_needed(i) for i in range(1, lvl))
    p["skills"] = {s: 60 for s in E.SKILLS}
    E.save_profile(p)
    return p


def view(key, uid=1):
    return PANELS[key]["view"](E.get_profile(uid), ctx(uid))


def do(key, action, body=None, uid=1):
    return run(PANELS[key]["act"](E.get_profile(uid), ctx(uid), action, body or {}))


TILE_KEYS = {"title", "icon", "value", "cost", "sub", "meter", "pips", "state", "act", "body", "nav", "confirm", "badge", "info"}


def check_tile(t):
    """A tile is read at a glance: a short name, a few words of sub, a known state."""
    assert set(t) == TILE_KEYS and t["title"] and len(t["title"].split()) <= 5
    assert t["sub"] is None or len(t["sub"].split()) <= 7
    assert t["state"] in (None, "ready", "locked", "done", "max")
    assert t["icon"] is None or t["icon"][:2] in ("a:", "s:", "c:", "i:")


def check_panel(p):
    assert set(p) == {"key", "title", "art", "back", "blurb", "stats", "sections", "actions", "selects"}
    assert p["title"] and isinstance(p["blurb"], list)
    for s in p["sections"]:
        assert s["lines"] or s["tiles"]
        for t in s["tiles"]:
            check_tile(t)
    for a in p["actions"]:
        assert set(a) == {"id", "label", "emoji", "style", "disabled", "hint", "nav", "confirm"}
    for s in p["selects"]:
        assert s["options"] and {"id", "placeholder", "min", "max", "options"} == set(s)


def tiles(p):
    return [t for s in p["sections"] for t in s["tiles"]]


def tile_for(p, **want):
    """The tiles of a panel whose fields match (title, act, nav, state ...)."""
    return [t for t in tiles(p) if all(t.get(k) == v for k, v in want.items())]


@pytest.mark.parametrize("key", PANEL_KEYS)
def test_every_panel_builds(key):
    level_up()
    p = view(key)
    check_panel(p)
    assert p["key"] == key
    assert p["back"] == ("notice" if key == "daily_board" else "town")


def test_notice_claim_refuses_then_pays():
    with pytest.raises(common.Refuse):
        do("notice", "claim")
    p = E.get_profile(1)
    key = E.task_progress(p)[0][0]
    p["tasks"]["prog"][key] = D.TASKS[key]["n"]
    E.save_profile(p)
    ready = tile_for(view("notice"), act="claim")
    assert len(ready) == 1 and ready[0]["state"] == "ready" and ready[0]["cost"] and ready[0]["sub"] == "Claim"
    before = E.get_profile(1)["septims"]
    r = do("notice", "claim")
    assert E.get_profile(1)["septims"] > before and r["toast"]
    done = [t for t in tiles(r["panel"]) if t["state"] == "done" and t["cost"]]
    assert len(done) == 1 and done[0]["sub"] == "Claimed" and not tile_for(r["panel"], act="claim")
    with pytest.raises(common.Refuse):
        do("notice", "claim")


def test_notice_tasks_are_short_tiles_with_progress():
    p = view("notice")
    tasks = next(s for s in p["sections"] if s["title"].startswith("Tasks"))["tiles"]
    assert len(tasks) == len(E.task_progress(E.get_profile(1)))
    for t, (key, task, done, _comp, _claimed) in zip(tasks, E.task_progress(E.get_profile(1))):
        assert len(t["title"].split()) <= 3 and t["cost"] == D.TASK_REWARDS[task["band"]][0]
        assert t["meter"] == ([done, task["n"]] if task["n"] > 1 else None)
        assert t["info"] == task["name"] and t["state"] is None


def test_notice_daily_nav_and_spoils_refuse():
    p = view("notice")
    daily = tile_for(p, nav="adventure:daily")[0]
    assert daily["state"] == "ready" and daily["sub"] == E.daily_location()["name"]
    assert tile_for(p, nav="daily_board")
    assert not tile_for(p, act="spoils")
    with pytest.raises(common.Refuse):
        do("notice", "spoils")
    with pytest.raises(common.Refuse):
        do("notice", "bogus")


def test_notice_hunt_tile_gates_and_marches():
    hunt = lambda p: next(s for s in p["sections"] if s["title"] == "The hunt")["tiles"][0]
    p = view("notice")
    store = E.world_boss()
    h = hunt(p)
    assert h["icon"].startswith(f"c:enemy_wb_{store['boss']}") and h["meter"] == [store["hp"], store["max"]]
    assert h["state"] == "locked" and not tile_for(p, act="march")           # level 5 first
    level_up()
    p = view("notice")
    assert hunt(p)["state"] == "ready" and hunt(p)["sub"].startswith("Wave")
    marches = tile_for(p, act="march")
    assert [t["body"]["value"] for t in marches] == ["attack", "expose", "protect"]
    assert all(t["state"] == "ready" and len(t["sub"].split()) <= 3 for t in marches)
    do("notice", "march", {"value": "attack"})
    p = view("notice")
    assert hunt(p)["state"] == "done" and not tile_for(p, act="march")


def test_march_needs_level_then_hits_the_boss():
    with pytest.raises(common.Refuse):
        do("notice", "march", {"value": "attack"})
    level_up()
    assert [t["body"]["value"] for t in tile_for(view("notice"), act="march")] == ["attack", "expose", "protect"]
    with pytest.raises(common.Refuse):
        do("notice", "march", {"value": "nonsense"})
    r = do("notice", "march", {"value": "attack"})
    m = r["march"]
    assert r["hero"]["name"] == "Tester" and m["role"]["key"] == "attack"
    assert m["boss"]["cut"] == f"enemy_wb_{m['boss']['key']}"
    assert m["slain"] or m["after"]["hp"] == m["pool"]["hp"] - m["dealt"]     # a kill brings the next wave's pool
    beats = m["beats"]
    assert beats[0]["line"] == D.WORLD_BOSSES[m["boss"]["key"]]["arrive"] and beats[0]["cue"] is None
    # the staged blows add up to what the engine dealt, and every exchange is cued
    assert sum(b["cue"]["dmg"] for b in beats if b["cue"] and b["cue"]["t"] == "hit") == m["dealt"]
    assert any(b["cue"] and b["cue"]["t"] in ("hit", "miss") for b in beats)
    assert not tile_for(r["panel"], act="march")      # one march a day
    with pytest.raises(common.Refuse):
        do("notice", "march", {"values": ["attack"]})


def test_daily_board_lists_attempts():
    p = view("daily_board")
    assert p["sections"][0]["lines"] == ["No attempts yet."] and not tiles(p)


def test_daily_board_is_a_podium(monkeypatch):
    def row(name, state, rooms, kills, satchel=0, stone="warrior"):
        return {"name": name, "state": state, "rooms": rooms, "kills": kills, "satchel": satchel, "stone": stone}
    monkeypatch.setattr(E, "daily_results", lambda: {
        "1": row("Tester", "dead", 2, 3), "2": row("Ace", "cleared", 5, 9, 700, "mage"),
        "3": row("Bee", "cleared", 5, 7, 300, "thief"), "4": row("Cy", "left", 1, 1), "5": row("Di", "launched", 0, 0)})
    p = view("daily_board")
    podium, rest = p["sections"]
    assert [t["title"] for t in podium["tiles"]] == ["Ace", "Bee", "Tester"] and podium["cols"] == 3
    assert [t["value"] for t in podium["tiles"]] == ["1st", "2nd", "3rd"]
    assert podium["tiles"][0]["cost"] == 700 and podium["tiles"][0]["sub"] == "Cleared"
    assert podium["tiles"][2]["state"] == "done" and podium["tiles"][2]["sub"] == "Died room 3"      # me, highlighted
    assert [t["sub"] for t in rest["tiles"]] == ["Left room 1", "Launched"]
    assert podium["tiles"][0]["icon"].startswith("c:hero_mage_idle")


def test_pit_gates_and_step_in():
    with pytest.raises(common.Refuse):
        do("pit", "step_in")
    with pytest.raises(common.Refuse):
        do("pit", "duel", {"value": "2"})
    level_up()
    p = view("pit")
    assert p["art"] == panels_town.pit_art(0)
    ladder = next(s for s in p["sections"] if s["title"] == "Ladder")["tiles"][0]
    assert ladder["meter"] == [0, len(D.PIT_CHAMPS)]
    nxt = tile_for(p, act="step_in")[0]
    champ = D.PIT_CHAMPS[0]
    assert nxt["title"] == champ["name"] and nxt["state"] == "ready" and nxt["sub"] == champ["style"]
    assert nxt["icon"] == f"s:{panels_town.pit_art(0)}" and nxt["info"]
    r = do("pit", "step_in")
    assert r["nav"] == "bout:pit" and E.pit_bout_active(E.get_profile(1))
    again = do("pit", "step_in")                # a live bout just points back at itself
    assert again["nav"] == "bout:pit"
    resume = tile_for(again["panel"], nav="bout:pit")
    assert resume and resume[0]["state"] == "ready" and not tile_for(again["panel"], act="step_in")


def test_pit_bout_plays_to_the_end():
    level_up(1, 30)
    p = E.get_profile(1)
    p["skills"] = {s: 100 for s in E.SKILLS}
    p["weapon_tier"] = 3
    E.save_profile(p)
    assert bouts.bout(ctx(), "pit")["state"] == "none"
    do("pit", "step_in")
    b = bouts.bout(ctx(), "pit")
    assert b["state"] == "playing" and b["arena"] == "pit" and b["round"] == 1
    assert b["foe"]["hp"] == b["foe"]["maxHp"] > 0 and b["me"]["cut"] == "hero_%s_idle" % p["stone"]
    assert [a["id"] for a in b["actions"]] == ["strike", "power", "guard"]
    seen = set()
    for _ in range(40):
        b = run(bouts.bout_action(ctx(), "pit", random.choice(["strike", "power", "guard"]), {}))
        seen.update(c["t"] for c in b["cues"])
        assert b["lines"]
        if b["state"] != "playing":
            break
    assert b["state"] in ("won", "lost", "draw")
    assert b["cues"][-1] == {"t": "end", "state": b["state"]}
    assert b["actions"][0]["disabled"]
    assert seen & {"hit", "miss", "guard"}
    assert E.get_profile(1)["arena_log"]
    assert bouts.bout(ctx(), "pit")["state"] == "none"
    with pytest.raises(common.Refuse):
        run(bouts.bout_action(ctx(), "pit", "dance", {}))
    if b["state"] == "won":
        assert [n["id"] for n in b["next"]] == ["fight_on", "bank"]
        nxt = run(bouts.bout_action(ctx(), "pit", "fight_on", {}))
        assert nxt["state"] == "playing" and nxt["lines"]
    else:
        assert b["next"] == []
        with pytest.raises(common.Refuse):
            run(bouts.bout_action(ctx(), "pit", "fight_on", {}))


def test_pit_win_offers_fight_on_then_bank(monkeypatch):
    level_up(1, 30)
    do("pit", "step_in")
    p = E.get_profile(1)
    p["pit"]["bout"]["foe"] = 1
    E.save_profile(p)
    monkeypatch.setattr(E.random, "random", lambda: 0.0)       # every swing lands
    p = E.get_profile(1)
    p["pit"]["bout"]["me"] = 99
    E.save_profile(p)
    b = run(bouts.bout_action(ctx(), "pit", "strike", {}))
    assert b["state"] == "won" and b["foe"]["hp"] == 0
    assert {"t": "kill", "crit": False, "boss": False} in b["cues"]
    assert [n["id"] for n in b["next"]] == ["fight_on", "bank"]
    bank = run(bouts.bout_action(ctx(), "pit", "bank", {}))
    assert bank["state"] == "none" and bank["lines"]


def test_duel_a_second_profile():
    level_up()
    E.create_profile(2, "Rival", "mage")
    level_up(2, 8)
    p = view("pit")
    rivals = tile_for(p, act="duel")
    assert [t["body"] for t in rivals] == [{"value": "2"}] and rivals[0]["title"] == "Rival" and rivals[0]["state"] == "ready"
    assert rivals[0]["value"] == "Lv 8" and rivals[0]["icon"].startswith("c:hero_mage_idle")
    with pytest.raises(common.Refuse):
        do("pit", "duel", {"value": "1"})
    with pytest.raises(common.Refuse):
        do("pit", "duel", {"value": "99"})
    r = do("pit", "duel", {"value": "2"})
    assert r["nav"] == "bout:duel"
    assert not tile_for(r["panel"], act="duel") and tile_for(r["panel"], nav="bout:duel")
    b = bouts.bout(ctx(), "duel")
    assert b["state"] == "playing" and "Rival" in b["foe"]["name"] and b["foe"]["art"] in ("duel_circle", "pit")
    for _ in range(40):
        b = run(bouts.bout_action(ctx(), "duel", random.choice(["strike", "power", "guard"]), {}))
        if b["state"] != "playing":
            break
    assert b["state"] in ("won", "lost", "draw")
    assert b["cues"][-1]["t"] == "end"
    assert b["next"] == []
    rival = E.get_profile(2)
    if b["state"] != "draw":
        assert rival["rivals"]["1"] and rival["ghost_log"]       # E._h2h saved the other side
    assert bouts.bout(ctx(), "duel")["state"] == "none"
    with pytest.raises(common.Refuse):
        do("pit", "duel", {"value": "2"})                         # once per rival per day
    with pytest.raises(common.Refuse):
        run(bouts.bout_action(ctx(), "nowhere", "strike", {}))


def test_stale_round_is_harmless():
    b = run(bouts.bout_action(ctx(), "duel", "strike", {}))
    assert b["state"] == "none"


def test_factions_join_confirm_and_claim():
    with pytest.raises(common.Refuse):
        do("factions", "join", {"value": "companions"})
    with pytest.raises(common.Refuse):
        do("factions", "claim")
    locked = tile_for(view("factions"), state="locked")                # below level 8
    assert len(locked) == len(D.FACTIONS) and all(t["info"] and not t["nav"] for t in locked)
    level_up()
    p = view("factions")
    joins = tile_for(p, act="join")
    assert [t["body"]["value"] for t in joins] == list(D.FACTIONS) and all(t["icon"].startswith("a:faction_") for t in joins)
    r = do("factions", "join", {"values": ["companions"]})        # unsworn: straight in, as views does
    assert E.get_profile(1)["allegiance"] == "companions" and r["toast"]
    sworn = tile_for(r["panel"], state="done")[0]
    assert sworn["title"] == "Companions" and sworn["sub"] == "Initiate" and sworn["meter"] and not sworn["act"]
    week = next(s for s in r["panel"]["sections"] if s["title"] == "This week")["tiles"][0]
    assert week["meter"] == [0, D.FACTIONS["companions"]["goal"]] and week["state"] is None and not week["act"]
    keys = [k for k in D.FACTIONS if k != "companions"]
    r = do("factions", "join", {"value": keys[0]})                 # sworn: asks first
    swear = tile_for(r["panel"], act=f"swear:{keys[0]}")[0]
    assert swear["confirm"] and swear["state"] == "ready"
    assert tile_for(r["panel"], nav="factions") and E.get_profile(1)["allegiance"] == "companions"
    r = do("factions", f"swear:{keys[0]}")
    assert E.get_profile(1)["allegiance"] == keys[0]
    with pytest.raises(common.Refuse):
        do("factions", "swear:nope")
    with pytest.raises(common.Refuse):
        do("factions", "promote")


def test_factions_claim_is_a_ready_tile():
    level_up()
    do("factions", "join", {"value": "companions"})
    p = E.get_profile(1)
    p["stats"][D.FACTIONS["companions"]["stat"]] = p["stats"].get(D.FACTIONS["companions"]["stat"], 0) + 999
    E.save_profile(p)
    claim = tile_for(view("factions"), act="claim")
    assert len(claim) == 1 and claim[0]["state"] == "ready" and claim[0]["meter"][0] == claim[0]["meter"][1]
    assert do("factions", "claim")["toast"]


def test_holdings_deed_build_and_gates():
    level_up()
    p = view("holdings")
    deed = tile_for(p, act="deed")
    assert len(deed) == 1 and deed[0]["cost"] == D.HOMESTEAD["land"]["septims"] and deed[0]["state"] == "ready"
    assert not tile_for(p, act="build")
    with pytest.raises(common.Refuse):
        do("holdings", "build", {"value": "garden"})
    with pytest.raises(common.Refuse):
        do("holdings", "banner", {"value": "stag"})
    with pytest.raises(common.Refuse):
        do("holdings", "collect")
    with pytest.raises(common.Refuse):
        do("holdings", "haul", {"value": "1"})
    pr = E.get_profile(1)
    pr["septims"] = 10 ** 6
    E.save_profile(pr)
    do("holdings", "deed")
    assert E.homestead_built(E.get_profile(1), "land")
    with pytest.raises(common.Refuse):
        do("holdings", "deed")
    p = view("holdings")
    assert p["art"] in (None, "homestead_1")
    built = tile_for(p, state="done")
    assert built and all(t["sub"] is None for t in built)
    builds = tile_for(p, act="build")
    assert builds and all(t["cost"] for t in builds)
    first = builds[0]["body"]["value"]
    r = do("holdings", "build", {"value": first})
    assert E.get_profile(1)["homestead"]["building"] == first and r["toast"]
    sends = tile_for(view("holdings"), act="expedition")
    assert [t["body"]["value"] for t in sends] == list(D.EXPEDITIONS) and sends[0]["icon"].startswith("a:expedition_")
    r = do("holdings", "expedition", {"values": [next(iter(D.EXPEDITIONS))]})
    assert E.expedition(E.get_profile(1)) and r["toast"]
    assert not tile_for(r["panel"], act="build") and not tile_for(r["panel"], act="expedition")   # one build, one housecarl
    assert [t["sub"] for t in tile_for(r["panel"], title="Patrol the roads")] == ["1 day left"]
    with pytest.raises(common.Refuse):
        do("holdings", "expedition", {"value": next(iter(D.EXPEDITIONS))})   # housecarl is out
    with pytest.raises(common.Refuse):
        do("holdings", "expedition", {})


def test_rankings_boards_and_help_pages():
    level_up()
    r = do("rankings", "board", {"value": "wealth"})
    boards = tile_for(r["panel"], act="board")
    assert len(boards) == 8 and [t["body"]["value"] for t in boards if t["state"] == "done"] == ["wealth"]
    rows = r["panel"]["sections"][1]
    assert rows["title"].endswith("The Coffers") and rows["tiles"][0]["value"] == "1st" and rows["tiles"][0]["cost"] is not None
    assert rows["tiles"][0]["title"] == "You" and rows["tiles"][0]["state"] == "done"
    with pytest.raises(common.Refuse):
        do("rankings", "board", {"value": "nope"})
    for page in __import__("lib.features.skyrim.views", fromlist=["x"]).HELP_PAGES:
        r = do("help", "page", {"value": page})
        check_panel(r["panel"])
        assert [o["value"] for o in r["panel"]["selects"][0]["options"] if o["chosen"]] == [page]
    with pytest.raises(common.Refuse):
        do("rankings", "other")
    with pytest.raises(common.Refuse):
        do("help", "page", {"value": "zzz"})
    with pytest.raises(common.Refuse):
        do("help", "other")


def test_help_guide_is_six_icon_tiles():
    p = view("help")
    ts = tiles(p)
    assert [t["icon"] for t in ts] == ["i:door", "i:sword", "i:eye", "i:heart", "i:coin", "i:star"]
    assert all(len(t["title"].split()) <= 3 and len(t["sub"].split()) <= 4 and t["info"] for t in ts)
    assert [o["value"] for o in p["selects"][0]["options"] if o["chosen"]] == ["guide"]
    assert len(p["selects"][0]["options"]) > 6               # the chapters stay reachable


def test_march_beats_cue_every_kind_of_exchange():
    from lib.activities.skyrim_web.panels_town import march_beats
    boss = D.WORLD_BOSSES["white_terror"]
    beats = march_beats([boss["arrive"], f"-# 💥 A CLEAN strike - {boss['hit'][0]} (**-2**).", f"-# {boss['miss'][0]}.",
                         f"-# {boss['answer'][0]} - 💥 a CRUSHING blow (❤️ left).", "-# ⚔️ **The hunt turns** - half its hearts are spent.",
                         f"-# {boss['answer'][1]} (💀 none left).", "The shield-bearers drag you clear. Your wounds mend by morning.",
                         f"🏆 **{boss['slain']}**", "🌩️ **The hold barely draws breath** - something rises."], boss)
    assert [b["cue"] and b["cue"]["t"] for b in beats] == [None, "hit", "miss", "hurt", "beat", "hurt", "down", "kill", "rise"]
    assert beats[1]["cue"] == {"t": "hit", "dmg": 2, "crit": True, "style": "blade"}
    assert beats[3]["cue"]["crushing"] and beats[3]["cue"]["left"] == 1 and beats[5]["cue"]["left"] == 0
