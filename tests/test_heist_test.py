"""TEMP: /heisttest (lib/activities/heist_test.py). Every Bank Job game is on the list, each one opens as a one-thing
practice job straight into that game, plays and scores through the real job, is ticked off with its score, and
nothing moves or is posted. Rip out with heist_test.py."""

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
    assert T.choices(OTHER) == []
    with pytest.raises(H.Refuse):
        T.open_test(OTHER, "hacker:keypad:s")
    with pytest.raises(H.Refuse):
        T.open_test(TESTER, "no such game")
    assert not H._rooms


def test_the_list_and_the_checklist(clock):
    first = T.choices(TESTER)
    assert first[0][1] == T.ALL and len(first) == 25
    assert [v for _, v in T.choices(TESTER, "lance")] == ["loud:safecracker:s", "loud:safecracker:big"]
    assert T.find("Hacker: LIGHTS OUT (vault door)") == "hacker:lights:big" and T.find("lights") is None
    T._tick(TESTER, "hacker:lights:big", 0.8)
    title, body = T.checklist(TESTER)
    assert title == "Bank Job test: 1 of 76 played" and "✓ LIGHTS OUT (vault door): best 80%" in body
    assert len(body) <= 4096 and "—" not in body and "–" not in body
    assert T.choices(TESTER, "lights")[-1] == ("✓ Hacker: LIGHTS OUT (vault door) (80%)", "hacker:lights:big")


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


def test_the_command_is_registered_with_its_game_list():
    from lib.activities import launcher
    if not launcher._heisttest_on():
        pytest.skip("no /heist in this config")
    bot = launcher.Launcher()
    cmd = bot.tree.get_command(T.COMMAND)
    assert launcher._wanted()[T.COMMAND]["options"] == T.OPTIONS
    assert launcher._options(cmd.to_dict(bot.tree)["options"]) == launcher._options(T.OPTIONS)
    assert "\u2014" not in T.DESCRIPTION and len(T.DESCRIPTION) <= 100
