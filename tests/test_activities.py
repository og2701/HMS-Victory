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
