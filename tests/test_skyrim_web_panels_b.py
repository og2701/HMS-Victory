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


def check_panel(p):
    assert set(p) == {"key", "title", "art", "back", "blurb", "stats", "sections", "actions", "selects"}
    assert p["title"] and isinstance(p["blurb"], list)
    for s in p["sections"]:
        assert s["lines"]
    for a in p["actions"]:
        assert set(a) == {"id", "label", "emoji", "style", "disabled", "hint", "nav", "confirm"}
    for s in p["selects"]:
        assert s["options"] and {"id", "placeholder", "min", "max", "options"} == set(s)


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
    ids = [a["id"] for a in view("notice")["actions"]]
    assert "claim" in ids and "daily_board" in ids
    before = E.get_profile(1)["septims"]
    r = do("notice", "claim")
    assert E.get_profile(1)["septims"] > before and r["toast"]
    with pytest.raises(common.Refuse):
        do("notice", "claim")


def test_notice_daily_nav_and_spoils_refuse():
    acts = {a["id"]: a for a in view("notice")["actions"]}
    assert acts["daily"]["nav"] == "adventure:daily" and acts["daily_board"]["nav"] == "daily_board"
    with pytest.raises(common.Refuse):
        do("notice", "spoils")
    with pytest.raises(common.Refuse):
        do("notice", "bogus")


def test_march_needs_level_then_hits_the_boss():
    with pytest.raises(common.Refuse):
        do("notice", "march", {"value": "attack"})
    level_up()
    sel = {s["id"]: s for s in view("notice")["selects"]}
    assert [o["value"] for o in sel["march"]["options"]] == ["attack", "expose", "protect"]
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
    assert "march" not in {s["id"] for s in r["panel"]["selects"]}      # one march a day
    with pytest.raises(common.Refuse):
        do("notice", "march", {"values": ["attack"]})


def test_daily_board_lists_attempts():
    p = view("daily_board")
    assert p["sections"][0]["lines"] == ["No attempts yet today. The dungeon waits."]


def test_pit_gates_and_step_in():
    with pytest.raises(common.Refuse):
        do("pit", "step_in")
    with pytest.raises(common.Refuse):
        do("pit", "duel", {"value": "2"})
    level_up()
    p = view("pit")
    assert p["art"] == panels_town.pit_art(0)
    assert "step_in" in [a["id"] for a in p["actions"]]
    r = do("pit", "step_in")
    assert r["nav"] == "bout:pit" and E.pit_bout_active(E.get_profile(1))
    again = do("pit", "step_in")                # a live bout just points back at itself
    assert again["nav"] == "bout:pit"
    assert "resume" in [a["id"] for a in again["panel"]["actions"]]


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
    sel = {s["id"]: s for s in p["selects"]}
    assert [o["value"] for o in sel["duel"]["options"]] == ["2"]
    with pytest.raises(common.Refuse):
        do("pit", "duel", {"value": "1"})
    with pytest.raises(common.Refuse):
        do("pit", "duel", {"value": "99"})
    r = do("pit", "duel", {"value": "2"})
    assert r["nav"] == "bout:duel"
    assert "duel" not in {s["id"] for s in r["panel"]["selects"]}
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
    level_up()
    p = view("factions")
    assert "join" in [s["id"] for s in p["selects"]]
    r = do("factions", "join", {"values": ["companions"]})        # unsworn: straight in, as views does
    assert E.get_profile(1)["allegiance"] == "companions" and r["toast"]
    keys = [k for k in D.FACTIONS if k != "companions"]
    r = do("factions", "join", {"value": keys[0]})                 # sworn: asks first
    acts = {a["id"]: a for a in r["panel"]["actions"]}
    assert f"swear:{keys[0]}" in acts and acts[f"swear:{keys[0]}"]["confirm"] and acts["stay"]["nav"] == "factions"
    assert E.get_profile(1)["allegiance"] == "companions"
    r = do("factions", f"swear:{keys[0]}")
    assert E.get_profile(1)["allegiance"] == keys[0]
    with pytest.raises(common.Refuse):
        do("factions", "swear:nope")
    with pytest.raises(common.Refuse):
        do("factions", "promote")


def test_holdings_deed_build_and_gates():
    level_up()
    p = view("holdings")
    assert {a["id"] for a in p["actions"]} == {"deed"}
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
    build = {s["id"]: s for s in p["selects"]}["build"]
    first = build["options"][0]["value"]
    r = do("holdings", "build", {"value": first})
    assert E.get_profile(1)["homestead"]["building"] == first and r["toast"]
    r = do("holdings", "expedition", {"values": [next(iter(D.EXPEDITIONS))]})
    assert E.expedition(E.get_profile(1)) and r["toast"]
    with pytest.raises(common.Refuse):
        do("holdings", "expedition", {"value": next(iter(D.EXPEDITIONS))})   # housecarl is out
    with pytest.raises(common.Refuse):
        do("holdings", "expedition", {})


def test_rankings_boards_and_help_pages():
    level_up()
    r = do("rankings", "board", {"value": "wealth"})
    assert r["panel"]["sections"][0]["title"].endswith("The Coffers")
    chosen = [o["value"] for o in r["panel"]["selects"][0]["options"] if o["chosen"]]
    assert chosen == ["wealth"] and len(r["panel"]["selects"][0]["options"]) == 8
    with pytest.raises(common.Refuse):
        do("rankings", "board", {"value": "nope"})
    for page in __import__("lib.features.skyrim.views", fromlist=["x"]).HELP_PAGES:
        r = do("help", "page", {"value": page})
        check_panel(r["panel"])
        assert [o["value"] for o in r["panel"]["selects"][0]["options"] if o["chosen"]] == [page]
    with pytest.raises(common.Refuse):
        do("help", "page", {"value": "zzz"})
    with pytest.raises(common.Refuse):
        do("help", "other")


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
