"""TEMP: /heisttest (lib/activities/heist_test.py). It opens the activity on TEST MODE: every Bank Job game is on its
list, each one opens as a one-thing practice job straight into that game, plays and scores through the real job, is
ticked off with its score, the page can call it off to go back to the list, and nothing moves or is posted. Rip out
with heist_test.py."""

import copy
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OPENAI_TOKEN", "mock-token")

import config  # noqa: E402
from lib.activities import heist as H  # noqa: E402
from lib.activities import heist_games as G  # noqa: E402
from lib.activities import heist_test as T  # noqa: E402

TESTER, OTHER = 111, 999


@pytest.fixture
def clock(tmp_path, monkeypatch):
    monkeypatch.setattr(H, "_FILE", str(tmp_path / "heist.json"))
    monkeypatch.setattr(T, "_FILE", str(tmp_path / "heist_test.json"))
    for d in (H._rooms, H._posts, H._bonus, H._met, T._played):
        d.clear()
    H._heat.update(v=0.0, at=0.0)
    monkeypatch.setattr(H, "_loaded", True)
    monkeypatch.setattr(T, "_loaded", True)
    monkeypatch.setattr(config, "HEIST_TESTERS", [TESTER], raising=False)
    now = [1_000_000.0]
    monkeypatch.setattr(H.time, "time", lambda: now[0])
    monkeypatch.setattr(T.time, "time", lambda: now[0])
    monkeypatch.setattr(H, "max_stake", lambda target: 0)        # (the bank's reserves: not in a test job)
    posted = []
    monkeypatch.setattr(H, "listeners", [lambda ev, room: posted.append(ev)])

    def no_money(*a, **k):
        raise AssertionError("a test job moved money")
    from commands.economy import casino_base
    from lib.economy import economy_manager
    monkeypatch.setattr(casino_base, "credit_from_bank", no_money)
    monkeypatch.setattr(economy_manager, "remove_bb", no_money)
    monkeypatch.setattr(economy_manager, "add_bb", no_money)
    yield now, posted
    for d in (H._rooms, H._met, T._played):
        d.clear()


def tick(now, s):
    now[0] += s
    H.sweep(now[0])


def test_every_game_is_on_the_list():
    want = {f"{role}:{game}:{size}" for role in G.ROLES for game in G.keys(role)
            for size in (["loot"] if role == "muscle" else ["s", "big"] if role in ("hacker", "safecracker", "wheelman")
                         else ["s"])}
    want |= {f"loud:{role}:s" for role in G.ROLES} | {f"loud:{r}:big" for r in ("hacker", "safecracker", "wheelman")}
    want |= {"loose:atm", "loose:till", "loose:box", "grab"}
    assert set(T.GAMES) == want and len(T.GAMES) == 76
    for e in T.GAMES.values():
        assert "—" not in e["label"] and "–" not in e["label"] and len(e["label"]) <= 90


def test_only_a_tester_gets_it(clock):
    assert T.refusal(OTHER) == "This is for the Bank Job testers." and T.refusal(TESTER) is None
    with pytest.raises(H.Refuse):
        T.open_test(OTHER, "hacker:keypad:s")
    with pytest.raises(H.Refuse):
        T.close(OTHER)
    with pytest.raises(H.Refuse):
        T.open_test(TESTER, "no such game")
    assert not H._rooms


def test_the_picker_lists_every_game_with_its_tick(clock):
    p = T.picker(TESTER)
    assert p["total"] == 76 and p["played"] == 0 and [g["key"] for g in p["games"]] == list(T.GAMES)
    groups = list(dict.fromkeys(g["group"] for g in p["games"]))
    assert groups == ["Hacker", "Safecracker", "Lookout", "Wheelman", "Face", "Muscle", "Loud", "Loose cash", "The vault"]
    assert all(g["best"] is None and g["n"] == 0 for g in p["games"])
    assert T.find("Hacker: LIGHTS OUT (vault door)") == "hacker:lights:big" and T.find("lights") is None
    T._tick(TESTER, "hacker:lights:big", 0.8)
    T._tick(TESTER, "hacker:lights:big", 0.5)
    p = T.picker(TESTER)
    g = next(g for g in p["games"] if g["key"] == "hacker:lights:big")
    assert p["played"] == 1 and g["name"] == "LIGHTS OUT (vault door)" and g["best"] == 0.8 and g["last"] == 0.5 and g["n"] == 2
    loud = next(g for g in p["games"] if g["key"] == "loud:safecracker:big")
    assert loud["name"] == loud["label"] and "THERMAL LANCE" in loud["name"]
    assert T.picker(OTHER)["played"] == 0
    for g in p["games"]:
        assert "—" not in g["name"] and "–" not in g["name"]


def test_a_real_crew_keeps_them_out(clock):
    H.open_room(TESTER, 0, "post")
    assert T.refusal(TESTER) == "You're in a real crew. Finish or leave it first."
    with pytest.raises(H.Refuse):
        T.open_test(TESTER, "face:cover:s")
    T.close(TESTER)                                   # (back to the list never touches a real crew)
    assert not H._seated(TESTER)["over"]


def test_back_to_the_list_calls_the_test_job_off(clock):
    now, posted = clock
    r = T.open_test(TESTER, "hacker:keypad:s")
    v = H.view(r, TESTER, lambda u: "Tester", now[0])
    assert v["test"] == "hacker:keypad:s" and v["practice"]
    T.close(TESTER)
    assert r["over"] and r["how"] == "closed" and H._seated(TESTER) is None
    T.close(TESTER)                                   # (nothing to call off: fine)
    p = H.open_room(TESTER, 0, "post", practice=True)  # an ordinary practice job isn't a test's to call off
    T.close(TESTER)
    assert not p["over"] and "test" not in H.view(p, TESTER, lambda u: "Tester", now[0])
    assert posted == []


def _play_it_out(now, rid, uid):
    """GOT IT, then through the report and the vault, out at once."""
    r = H._rooms[rid]
    for _ in range(600):
        if r["state"] == "vault" and now[0] >= r["vault"]["start"] + 0.5:
            break
        tick(now, 1.0)
    assert r["state"] == "vault" and not r["over"], r["state"]
    H.leg_it(uid, rid)
    assert r["over"] and r["results"][str(uid)]["payout"] == 0


@pytest.mark.parametrize("key", [k for k in T.GAMES if k != "grab"])
def test_each_game_opens_alone_straight_into_it_and_is_ticked(clock, key):
    now, posted = clock
    e = T.GAMES[key]
    r = T.open_test(TESTER, key)
    rid = r["id"]
    assert r["practice"] and r["stake"] == 0 and r["state"] == "job" and r["players"] == [TESTER] and not r["bots"]
    assert H.active() == 0
    things = [th for _, th in H._things(r)]
    assert len(things) == 1
    th = things[0]
    assert th["who"] == TESTER and th["state"] == "active"
    tick(now, H.MOVE_SECONDS)
    v = H.view(r, TESTER, lambda u: "Tester", now[0])
    assert v["puzzle"]["ob"] == th["id"] and "briefing" in v["puzzle"]
    if e["kind"] == "loud":
        assert v["puzzle"]["briefing"]["key"] == f"loud:{e['role']}:{th['p']['kind']}"
    elif e["kind"] == "loose":
        assert v["puzzle"]["briefing"]["key"] == f"loose:{key[6:]}"
    else:
        assert v["puzzle"]["briefing"]["key"] == f"{e['role']}:{e['game']}"
    H.play(TESTER, rid, {"ob": th["id"], "go": True, "brief": True})
    v = H.view(r, TESTER, lambda u: "Tester", now[0])
    assert "briefing" not in v["puzzle"] and v["puzzle"]["game"] == (e["game"] if e["kind"] == "quiet" else
                                                                      G.get(th["role"], th["p"]["game"]).key)
    g = G.get(th["role"], th["p"].get("game"))
    if g is not None:
        if hasattr(g, "secs"):
            tick(now, g.secs(th["p"]) + 0.1)          # a timed task's timeline can't be ahead of its clock
        for mv in g.solve(copy.deepcopy(th["p"])):
            H.play(TESTER, rid, {**json.loads(json.dumps(mv)), "ob": th["id"]})
        assert th["state"] == "done" and th["score"] == pytest.approx(1.0)
        tick(now, 0.1)
        assert r["state"] == "report" and r["score"] == pytest.approx(1.0)
        assert T.played(TESTER)[key]["best"] == pytest.approx(1.0)
    _play_it_out(now, rid, TESTER)
    assert key in T.played(TESTER)
    assert posted == []


def test_the_grab_goes_straight_to_the_vault_and_is_ticked_when_its_over(clock):
    now, posted = clock
    r = T.open_test(TESTER, "grab")
    assert r["state"] == "report" and not list(H._things(r))
    assert "grab" not in T.played(TESTER)
    tick(now, H.REPORT_SECONDS + r["vault"]["safe"] + 0.5)
    H.grab(TESTER, r["id"], [r["vault"]["safe"] + x / 4 for x in range(-6, 1)])
    H.leg_it(TESTER, r["id"])
    assert r["over"] and "grab" in T.played(TESTER) and posted == []


def test_a_new_test_calls_off_the_last_but_never_a_real_crew(clock):
    a = T.open_test(TESTER, "face:cover:s")
    b = T.open_test(TESTER, "loud:face:s")
    assert a["over"] and a["how"] == "closed" and not b["over"]
    assert H._seated(TESTER) is b
    b.update(over=True)
    H.open_room(TESTER, 0, "post")
    with pytest.raises(H.Refuse):
        T.open_test(TESTER, "face:cover:s")


def test_the_lobby_opens_straight_on_it(clock, monkeypatch):
    monkeypatch.setattr(H, "most_wanted", lambda name_of, now=None: [])
    monkeypatch.setattr(H, "_reserves", lambda: None)
    r = T.open_test(TESTER, "wheelman:route:big")
    assert H.lobby(TESTER, lambda u: "Tester", 0)["room"]["id"] == r["id"]
    assert H.lobby(OTHER, lambda u: "Other", 0)["open"] == []


def test_the_command_just_opens_test_mode():
    from lib.activities import launcher
    if not launcher._heisttest_on():
        pytest.skip("no /heist in this config")
    bot = launcher.Launcher()
    cmd = bot.tree.get_command(T.COMMAND)
    assert launcher._wanted()[T.COMMAND]["options"] == []
    assert not cmd.to_dict(bot.tree).get("options")
    assert "\u2014" not in T.DESCRIPTION and len(T.DESCRIPTION) <= 100


def test_the_activity_opens_on_test_mode(clock, monkeypatch):
    from lib.activities import launcher, server
    monkeypatch.setattr(H, "most_wanted", lambda name_of, now=None: [])
    monkeypatch.setattr(H, "_reserves", lambda: None)
    monkeypatch.setattr(server, "_heist_lobby", lambda client, uid: H.lobby(uid, lambda u: "Tester", 0))
    monkeypatch.setattr(server, "_casino_open", lambda channel: True)
    launcher.want(TESTER, "heist:test")
    game = server._game_for(TESTER, {})
    assert game == "heist:test"
    out = server._opening(None, TESTER, game)
    assert out["game"] == "heist" and out["heist"]["test"]["total"] == 76
    H.open_room(TESTER, 0, "post")                    # from a real crew: just the lobby, with the crew
    out = server._opening(None, TESTER, game)
    assert "test" not in out["heist"] and out["heist"]["room"]


def test_the_routes(clock, monkeypatch):
    """GET /heist/test lists them, POST /heist/test {game} opens one (the lobby, with the job in it, and the list),
    POST /heist/test/done calls it off and answers with the list; anyone else is turned away."""
    import asyncio
    from types import SimpleNamespace
    from aiohttp.test_utils import TestClient, TestServer
    from lib.activities import auth, server
    from lib.core import restrictions
    monkeypatch.setenv("ACTIVITIES_SESSION_SECRET", "s3cret")
    monkeypatch.setattr(config, "ACTIVITIES_CASINO_CHANNELS", [], raising=False)
    monkeypatch.setattr(restrictions, "is_blocked", lambda uid, cmd: None)
    monkeypatch.setattr(server, "_heist_lobby", lambda client, uid: H.lobby(uid, lambda u: "Tester", 0))
    member = SimpleNamespace(display_name="Tester")
    client = SimpleNamespace(maintenance_mode=False, session=None, get_guild=lambda gid: SimpleNamespace(get_member=lambda u: member))

    async def go():
        out = []
        async with TestClient(TestServer(server.build_app(client))) as http:
            for uid, method, path, body in [(TESTER, "GET", "/api/heist/test", None),
                                            (TESTER, "POST", "/api/heist/test", {"game": "lookout:patrol:s"}),
                                            (TESTER, "POST", "/api/heist/test", {"game": "nope"}),
                                            (TESTER, "POST", "/api/heist/test/done", {}),
                                            (OTHER, "GET", "/api/heist/test", None),
                                            (OTHER, "POST", "/api/heist/test", {"game": "lookout:patrol:s"})]:
                headers = {"Authorization": f"Bearer {auth.make_session(uid, 1, secret='s3cret')}"}
                r = await http.request(method, path, headers=headers, json=body)
                out.append((r.status, await r.json()))
        return out
    monkeypatch.setattr(H, "allowed", lambda uid: True)           # (Bank Job open to all, so only the test's own check)
    (s1, listed), (s2, opened), (s3, _), (s4, back), (s5, _), (s6, _) = asyncio.run(go())
    assert s1 == 200 and listed["total"] == 76
    assert s2 == 200 and opened["room"]["test"] == "lookout:patrol:s" and opened["room"]["practice"] and opened["test"]["total"] == 76
    assert s3 == 422
    assert s4 == 200 and back["total"] == 76 and H._seated(TESTER) is None
    assert s5 == 403 and s6 == 422
    assert clock[1] == []
