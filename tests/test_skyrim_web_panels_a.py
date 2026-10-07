"""The character and shop panels of the /skyrim activity API: each view matches the SkPanel shape, each action
changes and saves the profile, and each refusal is a clean common.Refuse."""
import asyncio

import pytest

import config
from lib.activities.skyrim_web import common, panels_character, panels_shop
from lib.activities.skyrim_web.registry import PANELS
from lib.core import file_operations as F
from lib.features.skyrim import data as D
from lib.features.skyrim import engine as E

UID = 4242
CTX = {"uid": UID, "name": "Tester", "client": None, "ch": None}
KEYS = ["character", "perks", "masteries", "collection", "records", "companion", "hall",
        "shop", "property", "rumours", "alchemy", "grindstone", "pacts"]


@pytest.fixture
def prof(tmp_path, monkeypatch):
    for k in ("SKYRIM_PROFILES_FILE", "SKYRIM_DAILY_FILE", "SKYRIM_GRAVEYARD_FILE", "SKYRIM_WORLDBOSS_FILE",
              "PERSISTENT_VIEWS_FILE"):
        monkeypatch.setattr(config, k, str(tmp_path / (k + ".json")))
    monkeypatch.setattr(F, "PERSISTENT_VIEWS_FILE", str(tmp_path / "PERSISTENT_VIEWS_FILE.json"))
    p = E.create_profile(UID, "Tester", "warrior")
    E.drain_log()
    return p


def level_up(p, level):
    p["xp"] = sum(D.xp_needed(i) for i in range(1, level))
    E.save_profile(p)


def run(key, action, body=None):
    profile = E.get_profile(UID)
    return asyncio.run(PANELS[key]["act"](profile, CTX, action, body or {}))


def refused(key, action, body=None):
    with pytest.raises(common.Refuse):
        run(key, action, body)


TILE_KEYS = {"title", "icon", "value", "cost", "sub", "meter", "pips", "state", "act", "body", "nav", "confirm", "badge", "info"}


def check_tile(t):
    """A tile is read at a glance: a short name, a few words of sub, a known state."""
    assert set(t) == TILE_KEYS and t["title"] and len(t["title"].split()) <= 5
    assert t["sub"] is None or len(t["sub"].split()) <= 7
    assert t["state"] in (None, "ready", "locked", "done", "max")
    assert t["icon"] is None or t["icon"][:2] in ("a:", "s:", "c:", "i:")


def check_panel(p, key):
    assert set(p) == {"key", "title", "art", "back", "blurb", "stats", "sections", "actions", "selects"}
    assert p["key"] == key and isinstance(p["title"], str) and p["title"]
    assert p["art"] is None or isinstance(p["art"], str)
    assert p["back"] is None or isinstance(p["back"], str)
    assert all(isinstance(x, str) for x in p["blurb"])
    for s in p["stats"]:
        assert set(s) == {"label", "value", "icon"} and isinstance(s["value"], str)
    for s in p["sections"]:
        assert isinstance(s["title"], str) and (s["lines"] or s["tiles"]) and all(isinstance(x, str) for x in s["lines"])
        for t in s["tiles"]:
            check_tile(t)
    for a in p["actions"]:
        assert set(a) == {"id", "label", "emoji", "style", "disabled", "hint", "nav", "confirm"}
        assert a["style"] in ("primary", "success", "danger", "secondary")
    for s in p["selects"]:
        assert set(s) == {"id", "placeholder", "min", "max", "options"} and s["options"]
        for o in s["options"]:
            assert set(o) == {"value", "label", "blurb", "emoji", "chosen"}


def check_result(r, key):
    assert set(r) == {"panel", "toast", "cues", "nav", "hero"}
    check_panel(r["panel"], key)
    assert r["toast"] and r["hero"]["name"]


def select(p, sid):
    return next(s for s in p["selects"] if s["id"] == sid)


def test_every_panel_builds(prof):
    for key in KEYS:
        p = PANELS[key]["view"](E.get_profile(UID), CTX)
        check_panel(p, key)
    assert PANELS["shop"]["view"](prof, CTX)["back"] == "town"
    assert PANELS["grindstone"]["view"](prof, CTX)["back"] == "shop"
    assert PANELS["perks"]["view"](prof, CTX)["back"] == "character"
    assert PANELS["hall"]["view"](prof, CTX)["art"] == "hall_of_legends"


def test_character_nav_and_hall_gate(prof):
    p = PANELS["character"]["view"](prof, CTX)
    navs = [t["nav"] for t in tiles_of(p) if t["nav"]]
    assert navs == ["perks", "masteries", "collection", "records", "companion"]
    skills = [t for t in tiles_of(p) if t["meter"]]
    assert len(skills) == 6 and all(t["meter"][1] == 100 and t["icon"].startswith("a:skill_") for t in skills)
    assert any(t["badge"] == "Blessed" for t in skills)
    assert {t["icon"] for t in tiles_of(p)} >= {"a:weapon_iron|i:sword", "a:armour_iron|i:shield"}
    prof["alduin_slain"] = 1
    assert "hall" in [t["nav"] for t in tiles_of(PANELS["character"]["view"](prof, CTX))]
    level_up(prof, 6)
    perks_tile = next(t for t in tiles_of(PANELS["character"]["view"](E.get_profile(UID), CTX)) if t["nav"] == "perks")
    assert perks_tile["state"] == "ready" and perks_tile["badge"]


def test_perks_take_and_meditate(prof):
    refused("perks", "take", {"value": "stalwart"})            # level 1: no points
    refused("perks", "meditate")
    level_up(prof, 6)
    p = PANELS["perks"]["view"](E.get_profile(UID), CTX)
    stalwart = next(t for t in tiles_of(p) if t["act"] == "take" and t["body"] == {"value": "stalwart"})
    assert stalwart["state"] == "ready" and stalwart["pips"] == [0, 2] and stalwart["icon"] == "a:perk_stalwart|i:star"
    assert not p["selects"]
    r = run("perks", "take", {"value": "stalwart"})
    check_result(r, "perks")
    assert E.get_profile(UID)["perks"]["stalwart"] == 1
    assert next(t for t in tiles_of(r["panel"]) if t["title"] == "Stalwart Heart")["pips"] == [1, 2]
    refused("perks", "take", {"values": ["nonsense"]})
    refused("perks", "meditate")                                # no Voice yet
    q = E.get_profile(UID)
    q["words"] = 2
    q["voice"] = {"charges": 0, "date": E._today_str()}
    E.save_profile(q)
    mt = next(t for t in tiles_of(PANELS["perks"]["view"](E.get_profile(UID), CTX)) if t["title"] == "Meditate")
    assert mt["act"] == "meditate" and mt["state"] == "ready"
    r = run("perks", "meditate")
    check_result(r, "perks")
    mt = next(t for t in tiles_of(r["panel"]) if t["title"] == "Meditate")
    assert mt["state"] == "locked" and mt["act"] is None
    assert E.get_profile(UID)["meditations"] == 1
    assert E.voice_charges(E.get_profile(UID)) == 2
    refused("perks", "meditate")                                # breath full
    refused("perks", "bogus")


def test_masteries_doctrine_and_legendary(prof):
    refused("masteries", "doctrine", {"value": "blade:x"})
    refused("masteries", "legendary", {"value": "blade"})
    prof["skills"]["blade"] = 100
    E.save_profile(prof)
    p = PANELS["masteries"]["view"](E.get_profile(UID), CTX)
    opts = [t for t in tiles_of(p) if t["act"] == "doctrine"]
    assert len(opts) == 2 and ":" in opts[0]["body"]["value"] and all(t["confirm"] for t in opts)
    leg = next(t for t in tiles_of(p) if t["act"] == "legendary")
    assert leg["body"] == {"value": "blade"} and leg["confirm"]
    r = run("masteries", "doctrine", dict(opts[0]["body"]))
    check_result(r, "masteries")
    assert E.get_profile(UID)["doctrines"]["blade"]
    assert any(t["state"] == "done" and t["title"] == D.DOCTRINES["blade"][opts[0]["body"]["value"].split(":")[1]]["name"]
               for t in tiles_of(r["panel"]))
    refused("masteries", "doctrine", dict(opts[1]["body"]))   # slot already used
    check_result(run("masteries", "legendary", {"values": ["blade"]}), "masteries")
    q = E.get_profile(UID)
    assert q["skills"]["blade"] == 15 and q["legendary"]["blade"] == 1


def test_collection_and_records(prof):
    p = PANELS["collection"]["view"](prof, CTX)
    wonders = next(s for s in p["sections"] if s["title"] == "Wonders")
    assert wonders["cols"] == 4 and len(wonders["tiles"]) == len(D.WONDERS)
    assert all(t["state"] == "locked" and t["info"] == "Not found yet." for t in wonders["tiles"])
    assert p["sections"][0]["tiles"][0]["meter"] is not None
    prof["wonders"] = ["golden_sweetroll"]
    E.save_profile(prof)
    p = PANELS["collection"]["view"](E.get_profile(UID), CTX)
    w = next(s for s in p["sections"] if s["title"] == "Wonders")["tiles"]
    assert w[0]["state"] == "done" and w[0]["icon"] == "a:wonder_golden_sweetroll|i:star" and w[1]["state"] == "locked"
    prof["records"] = {"satchel": 1200, "pit_rank": 2}
    p = PANELS["records"]["view"](prof, CTX)
    ts = {t["title"]: t for t in tiles_of(p)}
    assert ts["Best satchel"]["cost"] == 1200 and ts["Pit rank"]["value"] == E.pit_title(2)
    assert ts["Delve kills"]["state"] == "locked" and ts["Delve kills"]["info"]
    assert ts["Delves"]["value"] == "0" and {"Kills", "Dragons", "Deaths", "Cleared"} <= set(ts)


def test_companion_choose(prof):
    p = PANELS["companion"]["view"](prof, CTX)
    assert all(t["state"] == "locked" and not t["act"] for t in tiles_of(p))
    refused("companion", "choose", {"value": "meeko"})          # not found yet
    prof["companions"] = ["meeko", "vix"]
    prof["companion"] = "meeko"
    E.save_profile(prof)
    p = PANELS["companion"]["view"](E.get_profile(UID), CTX)
    assert p["art"] == "pet_meeko"
    ts = tiles_of(p)
    assert [t["state"] for t in ts[:2]] == ["done", None] and ts[0]["act"] is None
    assert ts[1]["act"] == "choose" and ts[1]["body"] == {"value": "vix"} and ts[1]["icon"] == "s:pet_vix"
    assert ts[2]["state"] == "locked"
    r = run("companion", "choose", {"value": "vix"})
    check_result(r, "companion")
    assert E.get_profile(UID)["companion"] == "vix" and r["panel"]["art"] == "pet_vix"
    refused("companion", "choose", {"value": "corvus"})


def make_ready_to_retire(p):
    level_up(p, 22)
    q = E.get_profile(UID)
    q["alduin_slain"] = 1
    E.save_profile(q)
    assert E.retire_ready(E.get_profile(UID))[0]


def test_hall_flow(prof):
    p = PANELS["hall"]["view"](prof, CTX)
    assert not p["selects"] and not p["actions"]
    assert not any(t["act"] for t in tiles_of(p))
    assert next(t for t in tiles_of(p) if t["title"] == "Retire")["state"] == "locked"
    refused("hall", "retire")
    refused("hall", "boon", {"value": "old_soul"})
    make_ready_to_retire(prof)
    q = E.get_profile(UID)
    p = PANELS["hall"]["view"](q, CTX)
    offer = [t["body"]["value"] for t in tiles_of(p) if t["act"] == "boon"]
    assert offer == E.boon_offer(q) and len(offer) == 3
    assert next(t for t in tiles_of(p) if t["title"] == "Retire")["state"] == "ready"
    retire = next(a for a in p["actions"] if a["id"] == "retire")
    assert retire["disabled"] and retire["confirm"]
    refused("hall", "retire")                                   # no boon picked
    refused("hall", "boon", {"value": "not_a_boon"})
    refused("hall", "stone", {"value": "not_a_stone"})
    check_result(run("hall", "boon", {"value": offer[0]}), "hall")
    other = next(k for k in D.STONES if k != q["stone"])
    r = run("hall", "stone", {"value": other})
    retire = next(a for a in r["panel"]["actions"] if a["id"] == "retire")
    assert not retire["disabled"] and D.BOONS[offer[0]]["name"] in retire["confirm"]
    assert next(t for t in tiles_of(r["panel"]) if t["act"] == "stone" and t["body"]["value"] == other)["state"] == "done"
    assert next(t for t in tiles_of(r["panel"]) if t["act"] == "boon" and t["body"]["value"] == offer[0])["state"] == "done"
    r = run("hall", "retire")
    check_result(r, "hall")
    after = E.get_profile(UID)
    assert E.legacy_rank(after) == 1 and offer[0] in E.legacy(after)["boons"]
    assert after["stone"] == other and E.level(after) == 1
    refused("hall", "retire")                                   # nothing to retire any more


def test_hall_inherit(prof):
    prof["skills"]["blade"] = 100
    E.save_profile(prof)
    run("masteries", "doctrine", next(t for t in tiles_of(PANELS["masteries"]["view"](E.get_profile(UID), CTX))
                                      if t["act"] == "doctrine")["body"])
    make_ready_to_retire(E.get_profile(UID))
    p = PANELS["hall"]["view"](E.get_profile(UID), CTX)
    inh = [t for t in tiles_of(p) if t["act"] == "inherit"]
    assert inh
    boon = next(t for t in tiles_of(p) if t["act"] == "boon")["body"]["value"]
    run("hall", "boon", {"value": boon})
    refused("hall", "retire")                                   # inheritance not chosen
    refused("hall", "inherit", {"value": "blade:nonsense"})
    r = run("hall", "inherit", dict(inh[0]["body"]))
    check_result(r, "hall")
    assert next(t for t in tiles_of(r["panel"]) if t["act"] == "inherit" and t["body"] == inh[0]["body"])["state"] == "done"
    assert E.get_profile(UID)["inheritance"]
    run("hall", "retire")
    assert E.get_profile(UID)["doctrines"]


def tiles_of(p):
    return [t for s in p["sections"] for t in s["tiles"]]


def test_shop_actions(prof):
    p = PANELS["shop"]["view"](prof, CTX)
    ts = tiles_of(p)
    assert not p["actions"] and [t["act"] for t in ts if t["act"]] == ["weapon", "armour", "style"]   # pockets are full
    assert next(t for t in ts if t["title"] == "Potion")["state"] == "max"
    assert [t["nav"] for t in ts if t["nav"]] == ["property", "grindstone", "alchemy", "rumours"]
    refused("shop", "potion")                                   # pockets already full or no coin
    prof["potions"] = 0
    prof["septims"] = 1_000_000
    E.save_profile(prof)
    check_result(run("shop", "potion"), "shop")
    q = E.get_profile(UID)
    assert q["potions"] == 1 and q["septims"] == 1_000_000 - D.POTION_PRICE
    nxt = D.GEAR_TIERS[1]
    q["stats"]["dragons"] = 100
    E.save_profile(q)
    check_result(run("shop", "weapon"), "shop")
    assert E.get_profile(UID)["weapon_tier"] == 1
    check_result(run("shop", "armour"), "shop")
    assert E.get_profile(UID)["armour_tier"] == 1
    r = run("shop", "style")
    assert E.get_profile(UID)["armour_style"] == "light" and "light" in r["toast"]
    assert next(t for t in tiles_of(r["panel"]) if t["act"] == "style")["title"] == "Heavy"
    q = E.get_profile(UID)
    q["septims"] = 0
    E.save_profile(q)
    refused("shop", "weapon")
    refused("shop", "bogus")
    assert nxt


def test_shop_dragon_gate_disables(prof):
    prof["weapon_tier"] = len(D.GEAR_TIERS) - 2
    prof["septims"] = 10 ** 9
    E.save_profile(prof)
    last = D.GEAR_TIERS[-1]
    p = PANELS["shop"]["view"](E.get_profile(UID), CTX)
    w = next(t for t in tiles_of(p) if t["title"] == last["name"])
    assert last["dragons"] and w["state"] == "locked" and not w["act"] and "dragons" in w["info"]
    refused("shop", "weapon")


def test_property_buy(prof):
    p = PANELS["property"]["view"](prof, CTX)
    ts = {t["title"]: t for t in tiles_of(p)}
    assert not p["selects"] and set(ts) == {"Breezehome", "Alchemy Lab", "Trophy Room"}
    assert ts["Breezehome"]["act"] == "buy" and ts["Breezehome"]["body"] == {"value": "breezehome"}
    assert ts["Alchemy Lab"]["state"] == "locked" and not ts["Alchemy Lab"]["act"]   # the rest need the house first
    refused("property", "buy", {"value": "alchemy_lab"})
    refused("property", "buy", {"value": "breezehome"})         # no coin
    prof["septims"] = 100_000
    E.save_profile(prof)
    r = run("property", "buy", {"value": "breezehome"})
    check_result(r, "property")
    assert E.home_owned(E.get_profile(UID), "breezehome")
    ts = {t["title"]: t for t in tiles_of(r["panel"])}
    assert ts["Breezehome"]["state"] == "done" and ts["Alchemy Lab"]["act"] == "buy"
    refused("property", "buy", {"value": "breezehome"})


def test_rumours_buy(prof):
    key = next(iter(D.RUMOURS))
    r = D.RUMOURS[key]
    p = PANELS["rumours"]["view"](prof, CTX)
    ts = tiles_of(p)
    assert len(ts) == len(D.RUMOURS) and not p["selects"]
    assert next(t for t in ts if t["cost"] == r["price"] and t["state"] == "locked")["info"]   # level too low
    refused("rumours", "buy", {"value": key})
    level_up(prof, r["min_level"])
    q = E.get_profile(UID)
    q["septims"] = r["price"]
    E.save_profile(q)
    p = PANELS["rumours"]["view"](E.get_profile(UID), CTX)
    assert key in [t["body"]["value"] for t in tiles_of(p) if t["act"] == "buy"]
    r2 = run("rumours", "buy", {"value": key})
    check_result(r2, "rumours")
    assert "map" in [t["nav"] for t in tiles_of(r2["panel"])]   # heard: go and find it
    q = E.get_profile(UID)
    assert q["septims"] == 0 and E.rumours_of(q)[key] == "heard"
    refused("rumours", "buy", {"value": key})
    refused("rumours", "buy", {"value": "nope"})


def test_alchemy_brew(prof):
    p = PANELS["alchemy"]["view"](prof, CTX)
    lab = tiles_of(p)[-1]
    assert not p["selects"] and lab["title"] == "Alchemy Lab" and lab["state"] == "locked" and lab["nav"] == "property"
    refused("alchemy", "brew", {"value": "vigor"})
    prof["home"] = ["alchemy_lab", "breezehome"]
    prof["ingredients"] = {"troll_fat": 1, "blue_flower": 1}
    E.save_profile(prof)
    p = PANELS["alchemy"]["view"](E.get_profile(UID), CTX)
    ts = {t["title"]: t for t in tiles_of(p)}
    assert ts["Draught of Vigor"]["state"] == "ready" and ts["Draught of Vigor"]["body"] == {"value": "vigor"}
    assert ts["Potion of Healing"]["state"] == "locked" and "Bone Meal" in ts["Potion of Healing"]["info"]
    assert ts["Troll Fat"]["value"] == "x1"
    r = run("alchemy", "brew", {"value": "vigor"})
    check_result(r, "alchemy")
    q = E.get_profile(UID)
    assert E.elixir_stock(q) == {"vigor": 1} and not q["ingredients"]
    assert "shelf" in r["toast"]
    refused("alchemy", "brew", {"value": "vigor"})              # out of ingredients


def test_grindstone_temper(prof):
    p = PANELS["grindstone"]["view"](prof, CTX)
    ts = {t["title"]: t for t in tiles_of(p)}
    assert not p["actions"] and ts["Weapon"]["act"] == "weapon" and ts["Armour"]["act"] == "armour"
    assert ts["Weapon"]["pips"] == [0, E.TEMPER_MAX_GRADE] and ts["Weapon"]["cost"] == E.temper_cost(0)["septims"]
    refused("grindstone", "weapon")
    cost = E.temper_cost(0)
    prof["septims"] = cost["septims"]
    prof["ingredients"] = dict(cost["mats"])
    E.save_profile(prof)
    check_result(run("grindstone", "weapon"), "grindstone")
    q = E.get_profile(UID)
    assert q["temper"]["weapon"] == 1 and q["septims"] == 0
    refused("grindstone", "weapon")
    refused("grindstone", "armour")
    q["temper"]["armour"] = E.TEMPER_MAX_GRADE
    E.save_profile(q)
    p = PANELS["grindstone"]["view"](q, CTX)
    assert next(t for t in tiles_of(p) if t["title"] == "Armour")["state"] == "max"
    refused("grindstone", "armour")


def test_pacts_swear(prof):
    p = PANELS["pacts"]["view"](prof, CTX)
    assert not p["selects"] and all(t["state"] == "locked" and not t["act"] for t in tiles_of(p))   # below the level
    refused("pacts", "swear", {"values": ["boethiah"]})
    level_up(prof, E.PACT_MIN_LEVEL)
    p = PANELS["pacts"]["view"](E.get_profile(UID), CTX)
    ts = tiles_of(p)
    assert len(ts) == len(D.PACTS) and all(t["act"] == "swear" for t in ts)
    assert ts[0]["body"] == {"values": ["boethiah"]}
    r = run("pacts", "swear", {"values": ["boethiah", "namira", "bogus"]})
    check_result(r, "pacts")
    assert E.get_profile(UID)["nextpacts"] == ["boethiah", "namira"]
    ts = tiles_of(r["panel"])
    assert [t["state"] for t in ts] == ["done", "done", None, None]
    assert ts[0]["body"] == {"values": ["namira"]}               # tapping a sworn pact lifts it
    r = run("pacts", "swear", {"values": []})
    assert E.get_profile(UID)["nextpacts"] == [] and r["toast"]
