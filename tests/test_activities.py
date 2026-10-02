"""The ukplace activities API: sessions can't be forged, the channel lock holds, the answer
stays secret, and a solve through the activity pays exactly once."""

import asyncio
import datetime
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OPENAI_TOKEN", "mock-token")

from aiohttp.test_utils import TestClient, TestServer

import config
from lib.activities import auth, server, wordle_api
from lib.features import wordle as W

SECRET = "test-secret"
DAY = datetime.date(2026, 10, 2)
WORKSHOP = 1141037835445616640


# --- sessions ---------------------------------------------------------------------------
def test_a_session_round_trips_and_carries_the_channel():
    tok = auth.make_session(42, WORKSHOP, secret=SECRET)
    assert auth.read_session(tok, secret=SECRET) == {"uid": 42, "ch": WORKSHOP}


def test_a_tampered_or_foreign_session_is_refused():
    tok = auth.make_session(42, WORKSHOP, secret=SECRET)
    body, sig = tok.split(".")
    forged = auth._b64(b'{"uid":"7","ch":"","exp":9999999999}')
    assert auth.read_session(f"{forged}.{sig}", secret=SECRET) is None
    assert auth.read_session(tok, secret="someone-else") is None
    assert auth.read_session("nonsense", secret=SECRET) is None


def test_an_old_session_expires():
    tok = auth.make_session(42, None, secret=SECRET, now=1_000)
    assert auth.read_session(tok, secret=SECRET, now=1_000 + auth.SESSION_TTL + 1) is None


def test_the_channel_only_counts_if_discord_says_you_are_in_the_instance():
    inst = {"users": ["42", "43"], "location": {"channel_id": str(WORKSHOP)}}
    assert auth.instance_channel(inst, 42) == WORKSHOP
    assert auth.instance_channel(inst, 99) is None
    assert auth.instance_channel(None, 42) is None


# --- the API ------------------------------------------------------------------------------
def _setup(monkeypatch, tmp_path):
    monkeypatch.setenv("ACTIVITIES_SESSION_SECRET", SECRET)
    monkeypatch.setattr(config, "WORDLE_STATE_FILE", str(tmp_path / "wordle.json"))
    monkeypatch.setattr(config, "ACTIVITIES_ALLOWED_CHANNELS", [WORKSHOP])
    monkeypatch.setattr(server, "_today", lambda: DAY)
    monkeypatch.setattr(server, "GUESS_GAP", 0)
    monkeypatch.setattr(W, "_todays_word", lambda _d: "ghost")
    monkeypatch.setattr(W, "_run_solve_checks", lambda *a, **k: None)
    from lib.economy import reserve_policy
    monkeypatch.setattr(reserve_policy, "scale_reward", lambda amount, reserves=None: amount)
    paid = []
    monkeypatch.setattr(W, "add_bb", lambda uid, amount, **kw: paid.append((uid, amount, kw["reason"])) or True)
    from lib.core import restrictions
    monkeypatch.setattr(restrictions, "is_blocked", lambda uid, cmd: None)
    guild = SimpleNamespace(get_member=lambda uid: object() if uid != 666 else None)
    client = SimpleNamespace(maintenance_mode=False, session=None, get_guild=lambda gid: guild)
    return client, paid


def _run(client, scenario):
    async def go():
        async with TestClient(TestServer(server.build_app(client))) as http:
            return await scenario(http)
    return asyncio.run(go())


def _auth(uid, ch=WORKSHOP):
    return {"Authorization": f"Bearer {auth.make_session(uid, ch, secret=SECRET)}"}


def test_no_session_no_game(monkeypatch, tmp_path):
    client, _ = _setup(monkeypatch, tmp_path)

    async def scenario(http):
        r = await http.get("/api/wordle")
        return r.status
    assert _run(client, scenario) == 401


def test_sign_in_outside_bot_workshop_is_refused(monkeypatch, tmp_path):
    client, _ = _setup(monkeypatch, tmp_path)

    async def fake_exchange(_s, _c):
        return "access"

    async def fake_user(_s, _t):
        return {"id": "42", "username": "spookyfox"}

    async def fake_instance(_s, iid):
        ch = WORKSHOP if iid == "in-workshop" else 123
        return {"users": ["42"], "location": {"channel_id": str(ch)}}

    monkeypatch.setattr(auth, "exchange_code", fake_exchange)
    monkeypatch.setattr(auth, "fetch_user", fake_user)
    monkeypatch.setattr(auth, "fetch_instance", fake_instance)

    async def scenario(http):
        elsewhere = await http.post("/token", json={"code": "c", "instance_id": "general"})
        workshop = await http.post("/token", json={"code": "c", "instance_id": "in-workshop"})
        return elsewhere.status, workshop.status, await workshop.json()
    elsewhere, workshop, body = _run(client, scenario)
    assert elsewhere == 403 and workshop == 200
    assert auth.read_session(body["session"], secret=SECRET) == {"uid": 42, "ch": WORKSHOP}


def test_non_members_cannot_play(monkeypatch, tmp_path):
    client, _ = _setup(monkeypatch, tmp_path)

    async def scenario(http):
        return (await http.get("/wordle", headers=_auth(666))).status
    assert _run(client, scenario) == 403


def test_a_game_through_the_activity_hides_the_answer_and_pays_once(monkeypatch, tmp_path):
    client, paid = _setup(monkeypatch, tmp_path)

    async def scenario(http):
        first = await (await http.get("/api/wordle", headers=_auth(42))).json()
        bad = await http.post("/api/wordle/guess", json={"guess": "zzzzz"}, headers=_auth(42))
        mid = await (await http.post("/api/wordle/guess", json={"guess": "crane"}, headers=_auth(42))).json()
        won = await (await http.post("/api/wordle/guess", json={"guess": "ghost"}, headers=_auth(42))).json()
        again = await (await http.post("/api/wordle/guess", json={"guess": "ghost"}, headers=_auth(42))).json()
        return first, bad.status, mid, won, again
    first, bad, mid, won, again = _run(client, scenario)
    assert first["answer"] is None and first["next"] == config.WORDLE_REWARDS[0]
    assert bad == 422
    assert mid["answer"] is None and mid["rows"][0]["score"] == ["absent"] * 5
    assert won["solved"] and won["answer"] == "GHOST" and won["paid"] == config.WORDLE_REWARDS[1]
    assert again["done"]
    assert paid == [(42, config.WORDLE_REWARDS[1], "HMS Wordle solve")]


def test_progress_is_shared_with_the_slash_command(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    W._submit_guess(42, DAY.isoformat(), "ghost", "crane")      # played via /wordle
    assert [r["word"] for r in wordle_api.state(42, DAY)["rows"]] == ["CRANE"]


# --- the typed launch command -----------------------------------------------------------
def test_the_launch_command_only_opens_in_allowed_channels(monkeypatch):
    from lib.activities import launcher
    monkeypatch.setattr(config, "ACTIVITIES_ALLOWED_CHANNELS", [WORKSHOP])
    assert launcher._allowed(WORKSHOP)
    assert not launcher._allowed(123)
    assert not launcher._allowed(None)
    assert f"<#{WORKSHOP}>" in launcher._refusal()
    monkeypatch.setattr(config, "ACTIVITIES_ALLOWED_CHANNELS", [])
    assert launcher._allowed(123)


def test_the_launcher_builds_its_command():
    from lib.activities import launcher
    bot = launcher.Launcher()
    names = [c.name for c in bot.tree.get_commands()]
    assert names == [config.ACTIVITIES_LAUNCH_COMMAND]


def test_the_solve_message_shows_colours_not_letters():
    board = {"rows": [{"word": "CRANE", "score": ["absent"] * 5},
                      {"word": "GHOST", "score": ["correct"] * 5}], "paid": 140}
    msg = wordle_api.solve_message(42, board)
    assert msg.startswith("<@42> solved today's **HMS Wordle** in **2/6** · +140 UKP")
    assert "⬛⬛⬛⬛⬛\n🟩🟩🟩🟩🟩" in msg
    assert "CRANE" not in msg and "GHOST" not in msg


def test_a_solve_through_the_activity_is_announced_in_its_channel(monkeypatch, tmp_path):
    client, _ = _setup(monkeypatch, tmp_path)
    from lib.activities import launcher
    posted = []

    async def fake_announce(ch, text):
        posted.append((ch, text))
    monkeypatch.setattr(launcher, "announce", fake_announce)

    async def scenario(http):
        await http.post("/wordle/guess", json={"guess": "crane"}, headers=_auth(42))
        await http.post("/wordle/guess", json={"guess": "ghost"}, headers=_auth(42))
        await asyncio.sleep(0)
    _run(client, scenario)
    assert len(posted) == 1 and posted[0][0] == WORKSHOP and "in **2/6**" in posted[0][1]


def test_a_remembered_session_reopens_only_where_discord_says_you_are(monkeypatch, tmp_path):
    client, _ = _setup(monkeypatch, tmp_path)

    async def fake_instance(_s, iid):
        users = ["42"] if iid != "someone-elses" else ["7"]
        ch = WORKSHOP if iid != "general" else 123
        return {"users": users, "location": {"channel_id": str(ch)}}
    monkeypatch.setattr(auth, "fetch_instance", fake_instance)

    async def scenario(http):
        ok = await http.post("/api/resume", json={"instance_id": "here"}, headers=_auth(42))
        not_in_it = await http.post("/api/resume", json={"instance_id": "someone-elses"}, headers=_auth(42))
        wrong_channel = await http.post("/api/resume", json={"instance_id": "general"}, headers=_auth(42))
        no_session = await http.post("/api/resume", json={"instance_id": "here"})
        return ok.status, (await ok.json()), not_in_it.status, wrong_channel.status, no_session.status
    ok, body, not_in_it, wrong_channel, no_session = _run(client, scenario)
    assert ok == 200 and body["wordle"]["game"] == "wordle"
    assert auth.read_session(body["session"], secret=SECRET) == {"uid": 42, "ch": WORKSHOP}
    assert (not_in_it, wrong_channel, no_session) == (401, 403, 401)
