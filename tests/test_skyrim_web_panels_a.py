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
    panels_character._PICKS.clear()
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


def check_panel(p, key):
    assert set(p) == {"key", "title", "art", "back", "blurb", "stats", "sections", "actions", "selects"}
    assert p["key"] == key and isinstance(p["title"], str) and p["title"]
    assert p["art"] is None or isinstance(p["art"], str)
    assert p["back"] is None or isinstance(p["back"], str)
    assert all(isinstance(x, str) for x in p["blurb"])
    for s in p["stats"]:
        assert set(s) == {"label", "value", "icon"} and isinstance(s["value"], str)
    for s in p["sections"]:
        assert isinstance(s["title"], str) and s["lines"] and all(isinstance(x, str) for x in s["lines"])
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
    ids = [a["id"] for a in p["actions"]]
    assert ids == ["masteries", "perks", "collection", "records", "companion"]
    assert all(a["nav"] == a["id"] for a in p["actions"])
    prof["alduin_slain"] = 1
    assert "hall" in [a["id"] for a in PANELS["character"]["view"](prof, CTX)["actions"]]


def test_perks_take_and_meditate(prof):
    refused("perks", "take", {"value": "stalwart"})            # level 1: no points
    refused("perks", "meditate")
    level_up(prof, 6)
    p = PANELS["perks"]["view"](E.get_profile(UID), CTX)
    assert select(p, "take")["options"]
    r = run("perks", "take", {"value": "stalwart"})
    check_result(r, "perks")
    assert E.get_profile(UID)["perks"]["stalwart"] == 1
    refused("perks", "take", {"values": ["nonsense"]})
    refused("perks", "meditate")                                # no Voice yet
    q = E.get_profile(UID)
    q["words"] = 2
    q["voice"] = {"charges": 0, "date": E._today_str()}
    E.save_profile(q)
    check_result(run("perks", "meditate"), "perks")
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
    opts = select(p, "doctrine")["options"]
    assert opts and ":" in opts[0]["value"]
    assert select(p, "legendary")["options"][0]["value"] == "blade"
    check_result(run("masteries", "doctrine", {"value": opts[0]["value"]}), "masteries")
    assert E.get_profile(UID)["doctrines"]["blade"]
    refused("masteries", "doctrine", {"value": opts[1]["value"]})   # slot already used
    check_result(run("masteries", "legendary", {"values": ["blade"]}), "masteries")
    q = E.get_profile(UID)
    assert q["skills"]["blade"] == 15 and q["legendary"]["blade"] == 1


def test_collection_and_records(prof):
    p = PANELS["collection"]["view"](prof, CTX)
    assert p["sections"][0]["lines"]
    prof["records"] = {"satchel": 1200, "pit_rank": 2}
    p = PANELS["records"]["view"](prof, CTX)
    text = " ".join(l for s in p["sections"] for l in s["lines"])
    assert "1,200 septims" in text and "no mark set yet" in text


def test_companion_choose(prof):
    assert not PANELS["companion"]["view"](prof, CTX)["selects"]
    refused("companion", "choose", {"value": "meeko"})          # not found yet
    prof["companions"] = ["meeko", "vix"]
    prof["companion"] = "meeko"
    E.save_profile(prof)
    p = PANELS["companion"]["view"](E.get_profile(UID), CTX)
    assert p["art"] == "pet_meeko"
    assert [o["chosen"] for o in select(p, "choose")["options"]] == [True, False]
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
    refused("hall", "retire")
    refused("hall", "boon", {"value": "old_soul"})
    make_ready_to_retire(prof)
    q = E.get_profile(UID)
    p = PANELS["hall"]["view"](q, CTX)
    offer = [o["value"] for o in select(p, "boon")["options"]]
    assert offer == E.boon_offer(q) and len(offer) == 3
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
    assert next(o for o in select(r["panel"], "stone")["options"] if o["value"] == other)["chosen"]
    r = run("hall", "retire")
    check_result(r, "hall")
    after = E.get_profile(UID)
    assert E.legacy_rank(after) == 1 and offer[0] in E.legacy(after)["boons"]
    assert after["stone"] == other and E.level(after) == 1
    refused("hall", "retire")                                   # nothing to retire any more


def test_hall_inherit(prof):
    prof["skills"]["blade"] = 100
    E.save_profile(prof)
    run("masteries", "doctrine", {"value": select(PANELS["masteries"]["view"](E.get_profile(UID), CTX),
                                                  "doctrine")["options"][0]["value"]})
    make_ready_to_retire(E.get_profile(UID))
    p = PANELS["hall"]["view"](E.get_profile(UID), CTX)
    inh = select(p, "inherit")["options"]
    assert inh
    boon = select(p, "boon")["options"][0]["value"]
    run("hall", "boon", {"value": boon})
    refused("hall", "retire")                                   # inheritance not chosen
    refused("hall", "inherit", {"value": "blade:nonsense"})
    check_result(run("hall", "inherit", {"value": inh[0]["value"]}), "hall")
    assert E.get_profile(UID)["inheritance"]
    run("hall", "retire")
    assert E.get_profile(UID)["doctrines"]


def test_shop_actions(prof):
    p = PANELS["shop"]["view"](prof, CTX)
    ids = [a["id"] for a in p["actions"]]
    assert ids == ["potion", "weapon", "armour", "style", "property", "grindstone", "alchemy", "rumours"]
    assert {a["id"]: a["nav"] for a in p["actions"] if a["nav"]} == {
        k: k for k in ("property", "grindstone", "alchemy", "rumours")}
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
    assert next(a for a in r["panel"]["actions"] if a["id"] == "style")["label"] == "Go heavy"
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
    w = next(a for a in p["actions"] if a["id"] == "weapon")
    assert last["dragons"] and w["disabled"] and "dragons" in w["hint"]
    refused("shop", "weapon")


def test_property_buy(prof):
    p = PANELS["property"]["view"](prof, CTX)
    assert [o["value"] for o in select(p, "buy")["options"]] == ["breezehome"]   # the rest need the house first
    refused("property", "buy", {"value": "alchemy_lab"})
    refused("property", "buy", {"value": "breezehome"})         # no coin
    prof["septims"] = 100_000
    E.save_profile(prof)
    r = run("property", "buy", {"value": "breezehome"})
    check_result(r, "property")
    assert E.home_owned(E.get_profile(UID), "breezehome")
    assert "alchemy_lab" in [o["value"] for o in select(r["panel"], "buy")["options"]]
    refused("property", "buy", {"value": "breezehome"})


def test_rumours_buy(prof):
    key = next(iter(D.RUMOURS))
    r = D.RUMOURS[key]
    p = PANELS["rumours"]["view"](prof, CTX)
    assert not p["selects"]                                     # level too low
    refused("rumours", "buy", {"value": key})
    level_up(prof, r["min_level"])
    q = E.get_profile(UID)
    q["septims"] = r["price"]
    E.save_profile(q)
    p = PANELS["rumours"]["view"](E.get_profile(UID), CTX)
    assert key in [o["value"] for o in select(p, "buy")["options"]]
    check_result(run("rumours", "buy", {"value": key}), "rumours")
    q = E.get_profile(UID)
    assert q["septims"] == 0 and E.rumours_of(q)[key] == "heard"
    refused("rumours", "buy", {"value": key})
    refused("rumours", "buy", {"value": "nope"})


def test_alchemy_brew(prof):
    p = PANELS["alchemy"]["view"](prof, CTX)
    assert not p["selects"] and any("Alchemy Lab" in l for s in p["sections"] for l in s["lines"])
    refused("alchemy", "brew", {"value": "vigor"})
    prof["home"] = ["alchemy_lab", "breezehome"]
    prof["ingredients"] = {"troll_fat": 1, "blue_flower": 1}
    E.save_profile(prof)
    p = PANELS["alchemy"]["view"](E.get_profile(UID), CTX)
    assert "vigor" in [o["value"] for o in select(p, "brew")["options"]]
    r = run("alchemy", "brew", {"value": "vigor"})
    check_result(r, "alchemy")
    q = E.get_profile(UID)
    assert E.elixir_stock(q) == {"vigor": 1} and not q["ingredients"]
    assert "shelf" in r["toast"]
    refused("alchemy", "brew", {"value": "vigor"})              # out of ingredients


def test_grindstone_temper(prof):
    p = PANELS["grindstone"]["view"](prof, CTX)
    assert [a["id"] for a in p["actions"]] == ["weapon", "armour"]
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
    assert next(a for a in p["actions"] if a["id"] == "armour")["disabled"]
    refused("grindstone", "armour")


def test_pacts_swear(prof):
    p = PANELS["pacts"]["view"](prof, CTX)
    assert not p["selects"]                                     # locked below the level
    refused("pacts", "swear", {"values": ["boethiah"]})
    level_up(prof, E.PACT_MIN_LEVEL)
    p = PANELS["pacts"]["view"](E.get_profile(UID), CTX)
    sel = select(p, "swear")
    assert sel["min"] == 0 and sel["max"] == len(D.PACTS)
    r = run("pacts", "swear", {"values": ["boethiah", "namira", "bogus"]})
    check_result(r, "pacts")
    assert E.get_profile(UID)["nextpacts"] == ["boethiah", "namira"]
    assert [o["value"] for o in select(r["panel"], "swear")["options"] if o["chosen"]] == ["boethiah", "namira"]
    r = run("pacts", "swear", {"values": []})
    assert E.get_profile(UID)["nextpacts"] == [] and r["toast"]
