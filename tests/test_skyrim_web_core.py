"""The activity's /skyrim core (lib/activities/skyrim_web/core.py), driven headless against the real engine."""
import asyncio
import random

import pytest

import config
from lib.activities.skyrim_web import common, core
from lib.core import file_operations as F
from lib.features.skyrim import data as D
from lib.features.skyrim import engine as E
from lib.features.skyrim import sessions
from lib.features.skyrim import views as V

UID = 4242
CTX = {"uid": UID, "name": "Tester", "client": None, "ch": None}
ACT_KEYS = {"id", "kind", "label", "emoji", "pct", "hint", "disabled", "style"}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    files = {k: str(tmp_path / (k + ".json")) for k in (
        "SKYRIM_PROFILES_FILE", "SKYRIM_DAILY_FILE", "SKYRIM_GRAVEYARD_FILE", "SKYRIM_WORLDBOSS_FILE",
        "PERSISTENT_VIEWS_FILE")}
    for key, path in files.items():
        monkeypatch.setattr(config, key, path)
    monkeypatch.setattr(F, "PERSISTENT_VIEWS_FILE", files["PERSISTENT_VIEWS_FILE"])
    core._ENDED.clear()
    core._ABANDONING.clear()
    yield
    E.drain_log()


def run(coro):
    return asyncio.run(coro)


def check_delve(d):
    assert {"id", "rev", "kind", "state", "location", "depth", "room", "enemy", "you", "actions", "log",
            "result", "xpGained", "kills"} <= set(d)
    assert {"idx", "total", "kind", "key", "name", "boss", "art", "cut", "affix", "bounty", "story", "text"} <= set(d["room"])
    for a in d["actions"]:
        assert ACT_KEYS <= set(a)
    assert (d["result"] is not None) == (d["state"] != "playing")
    assert (d["enemy"] is not None) == (d["state"] == "playing" and d["room"]["kind"] == "enemy")


def start_tutorial():
    run(core.home(CTX))
    core.create(CTX, "warrior")
    return run(core.launch(CTX, "", "tutorial"))


def test_home_without_a_character_offers_the_class_pick():
    data = run(core.home(CTX))
    assert data["hero"] is None and data["goal"] is None and data["activeDelve"] is None
    pick = data["classPick"]
    assert [s["key"] for s in pick["stones"]] == list(D.STONES)
    assert all(s["perks"] and s["blurb"] and s["emoji"] for s in pick["stones"])
    assert pick["intro"] and set(data["town"]) >= {"adventure", "pit", "hall", "rankings"}
    assert data["weather"]["name"] and data["dragonOfWeek"]


def test_create_then_home_shows_the_town():
    data = core.create(CTX, "mage")
    assert data["hero"]["stone"] == "mage" and data["classPick"] is None
    assert data["tutorial"] is True
    assert data["town"]["adventure"]["label"] == "First adventure" and data["town"]["adventure"]["glow"]
    assert data["town"]["pit"]["locked"] and not data["town"]["pit"]["glow"]
    assert data["goal"]["action"] == "adventure"
    again = run(core.home({**CTX, "name": "New Name"}))
    assert again["hero"]["name"] == "New Name"


def test_create_refuses_unknown_stone_and_a_second_character():
    with pytest.raises(common.Refuse):
        core.create(CTX, "lord")
    core.create(CTX, "thief")
    with pytest.raises(common.Refuse):
        core.create(CTX, "warrior")
    assert E.get_profile(UID)["stone"] == "thief"


def test_offers_shape_and_actions():
    core.create(CTX, "warrior")
    data = core.offers(CTX)
    assert data["hero"]["name"] == "Tester" and data["resting"] is False and data["tutorial"] is True
    assert data["locations"] and all({"key", "name", "band", "blurb", "minLevel", "drops", "route",
                                      "routeTomorrow", "stirred", "kind", "locked"} <= set(loc)
                                     for loc in data["locations"])
    assert data["daily"]["available"] and not data["daily"]["done"]
    assert data["alduin"] is None and data["soulcairn"] is None and data["legends"] == []
    assert data["elixirs"] is None and data["pacts"] is None and data["canAbandon"] is False

    p = E.get_profile(UID)
    p["elixirs"] = {"vigor": 2}
    p["xp"] = sum(D.xp_needed(i) for i in range(1, E.PACT_MIN_LEVEL + 1))
    E.save_profile(p)
    data = core.offers_action(CTX, "elixirs", {"keys": ["vigor", "bogus"]})
    assert data["elixirs"]["chosen"] == ["vigor"] and data["elixirs"]["stock"][0]["count"] == 2
    data = core.offers_action(CTX, "pacts", {"keys": ["namira"]})
    assert data["pacts"]["open"] and data["pacts"]["sworn"] == ["namira"]

    p = E.get_profile(UID)
    p["stamina"]["charges"] = 0
    E.save_profile(p)
    data = core.offers(CTX)
    assert data["resting"] and data["restUntil"] > 0 and data["delvesLeft"] == 0
    assert all(loc["locked"] for loc in data["locations"])


def test_launch_tutorial_uses_a_negative_id_and_marks_the_profile():
    turn = start_tutorial()
    d = turn["delve"]
    check_delve(d)
    assert d["id"] < 0 and d["kind"] == "tutorial" and d["rev"] == 0 and d["state"] == "playing"
    assert E.get_profile(UID)["active_delve"] == d["id"]
    assert turn["cues"] == [{"t": "advance", "room": 0}]
    assert d["room"]["total"] == 4 and d["room"]["cut"].startswith("enemy_")
    assert {a["id"] for a in d["actions"]} >= {"atk:blade", "atk:marksman", "atk:destruction", "guard", "lve"}
    with pytest.raises(common.Refuse):
        run(core.launch(CTX, "", "tutorial"))


def play_out(seed):
    for key in ("SKYRIM_PROFILES_FILE", "PERSISTENT_VIEWS_FILE"):
        F.save_json_file(getattr(config, key), {})
    core._ENDED.clear()
    random.seed(seed)
    turn = start_tutorial()
    seen_advance = seen_end = False
    for _ in range(80):
        d = turn["delve"]
        check_delve(d)
        if d["state"] != "playing":
            break
        live = {a["id"]: a for a in d["actions"] if not a["disabled"]}
        if d["you"]["hearts"] <= 1 and "pot" in live:
            pick = "pot"
        elif d["room"]["kind"] == "enemy":
            pick = "atk:blade"
        else:
            pick = next(i for i in live if i.startswith("evt:"))
        idx = d["room"]["idx"]
        nxt = run(core.act(CTX, pick, d["rev"]))
        assert nxt["delve"]["rev"] == d["rev"] + 1
        kinds = [c["t"] for c in nxt["cues"]]
        warned = any("press Attack again" in line for line in nxt["delve"]["log"])
        if pick.startswith("atk") and not warned:
            assert kinds, f"an attack with no cues: {nxt['cues']}"
            assert {"hit", "miss"} & set(kinds)
        if nxt["delve"]["room"]["idx"] != idx and nxt["delve"]["state"] == "playing":
            assert {"t": "advance", "room": nxt["delve"]["room"]["idx"]} in nxt["cues"]
            seen_advance = True
        if nxt["delve"]["state"] != "playing":
            assert nxt["cues"][-1] == {"t": "end", "state": nxt["delve"]["state"]}
            seen_end = True
        assert nxt["hero"]["name"] == "Tester"
        turn = nxt
    return turn, seen_advance, seen_end


def test_tutorial_plays_to_the_end_with_cues():
    turn, advanced, ended = play_out(7)
    assert turn["delve"]["state"] != "playing" and ended
    res = turn["delve"]["result"]
    assert res["line"] and res["summary"]
    assert E.get_profile(UID)["active_delve"] is None
    assert E.load_delve(turn["delve"]["id"]) is None


def test_some_seed_advances_rooms():
    # a run that survives long enough to walk through a door shows the advance cue
    results = [play_out(s) for s in range(1, 8)]
    assert any(adv for _t, adv, _e in results)
    assert all(end for _t, _a, end in results)


def test_a_reload_just_after_the_end_shows_the_debrief_once():
    turn, _a, _e = play_out(3)
    again = core.current(CTX)
    assert again["delve"]["state"] == turn["delve"]["state"] and again["cues"] == []
    assert core.current(CTX) is None


def test_stale_rev_raises_stale_with_the_current_turn():
    turn = start_tutorial()
    rev = turn["delve"]["rev"]
    random.seed(1)
    run(core.act(CTX, "guard", rev))
    with pytest.raises(common.Stale) as err:
        run(core.act(CTX, "atk:blade", rev))
    assert err.value.turn["delve"]["rev"] == rev + 1


def test_unknown_action_is_a_refusal():
    turn = start_tutorial()
    with pytest.raises(common.Refuse):
        run(core.act(CTX, "xyz", turn["delve"]["rev"]))


def test_current_resumes_the_run_and_none_without_one():
    core.create(CTX, "warrior")
    assert core.current(CTX) is None
    turn = run(core.launch(CTX, "", "tutorial"))
    resumed = core.current(CTX)
    assert resumed["delve"] == turn["delve"] and resumed["cues"] == []
    assert run(core.home(CTX))["activeDelve"]["id"] == turn["delve"]["id"]
    assert run(core.home(CTX))["goal"]["action"] == "delve"


def test_abandon_hides_the_run_and_launching_leaves_it():
    start_tutorial()
    assert core.offers(CTX)["canAbandon"] is True
    assert core.offers_action(CTX, "abandon", {})["activeDelve"] is None
    random.seed(2)
    turn = run(core.launch(CTX, "embershard", "normal"))
    assert turn["delve"]["kind"] == "normal"
    assert E.get_profile(UID)["active_delve"] == turn["delve"]["id"]


def test_a_discord_launched_delve_can_be_continued():
    p = core.create(CTX, "warrior") and E.get_profile(UID)
    random.seed(5)
    pending = sessions.prepare(p, 1, "embershard", "tutorial")
    pending.commit(901)
    turn = core.current(CTX)
    assert turn["delve"]["id"] == 901
    nxt = run(core.act(CTX, "atk:marksman", turn["delve"]["rev"]))
    assert nxt["delve"]["id"] == 901 and nxt["delve"]["rev"] == 1 and nxt["cues"]


def test_someone_elses_delve_is_refused():
    start_tutorial()
    other = {**CTX, "uid": 99}
    with pytest.raises(common.Refuse):
        run(core.act(other, "guard", 0))


def test_reattach_skips_the_discord_view_for_an_activity_delve():
    class Client:
        def __init__(self):
            self.views = []

        def add_view(self, view, message_id=None):
            self.views.append((view, message_id))

    turn = start_tutorial()
    mid = turn["delve"]["id"]
    board = E.load_persistent_views()[str(mid)]
    client = Client()
    V.reattach_skyrim_view(client, str(mid), board)
    assert client.views == []
    assert E.load_delve(mid) is not None             # kept, not pruned
    # a finished one is still pruned
    E.get_profile(UID)
    p = E.get_profile(UID)
    p["active_delve"] = None
    E.save_profile(p)
    V.reattach_skyrim_view(client, str(mid), board)
    assert E.load_delve(mid) is None and client.views == []


def test_discord_hub_has_no_jump_link_for_an_activity_delve():
    start_tutorial()
    live = E.load_delve(E.get_profile(UID)["active_delve"])

    class Inter:
        guild_id, channel_id = 1, 2

    assert V._in_activity(live)
    assert V._delve_jump_url(Inter(), live) is None


# ---- every kind of road the map can send you down ----

def veteran(**extra):
    """A character past the tutorial, with whatever unlocks a test needs."""
    run(core.home(CTX))
    core.create(CTX, "warrior")
    p = E.get_profile(UID)
    p["tutorial_started"] = True
    p["stats"]["delves"] = 3
    p.update(extra)
    E.save_profile(p)
    return p


def test_the_daily_launches_on_todays_road():
    veteran()
    o = core.offers(CTX)
    assert o["daily"] and o["daily"]["available"]
    d = run(core.launch(CTX, "", "daily"))["delve"]
    check_delve(d)
    assert d["kind"] == "daily" and d["location"]["key"] == E.daily_location()["key"]


def test_a_heard_rumour_shows_as_a_legend_and_launches():
    rk = sorted(D.RUMOURS)[0]
    veteran(rumours={rk: "heard"}, xp=10 ** 7)        # legend lairs have level gates
    lair = D.RUMOURS[rk]["loc"]
    assert lair in [g["key"] for g in core.offers(CTX)["legends"]]
    d = run(core.launch(CTX, lair, "normal"))["delve"]
    check_delve(d)
    assert d["location"]["key"] == lair


def test_the_soul_cairn_opens_after_alduin_and_goes_by_depth():
    veteran(alduin_slain=1)
    o = core.offers(CTX)
    assert o["soulcairn"] and o["soulcairn"]["available"]
    d = run(core.launch(CTX, "", "soulcairn"))["delve"]
    check_delve(d)
    assert d["kind"] == "soulcairn" and d["room"]["total"] is None and d["depth"] >= 0


def test_alduin_waits_for_the_gates_then_launches():
    p = veteran(xp=10 ** 7)
    assert not core.offers(CTX)["alduin"]["available"]      # high level alone shows the path, still shut
    p["words"] = len(D.SHOUT_WORDS)
    p["stats"]["dragons"] = 50
    E.save_profile(p)
    assert core.offers(CTX)["alduin"]["available"]
    d = run(core.launch(CTX, "", "alduin"))["delve"]
    check_delve(d)
    assert d["kind"] == "alduin" and d["location"]["key"] == "skuldafn"
