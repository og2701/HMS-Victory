"""The bot action queue: the watcher skill drops a request file, the live bot runs one of a
fixed list of actions and writes a result. Covers the safety rules (whitelist, typed args,
stale and duplicate requests, rate limit), the pred actions doing what the slash commands
do (DB row plus live scheduler job), the slash commands still working after the refactor,
and the local helper's VM script talking to the queue end to end."""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import discord  # noqa: E402
import pytest  # noqa: E402

from lib.bot import bot_actions as ba  # noqa: E402
from lib.economy import prediction_system as ps  # noqa: E402


SCHEMA = """
CREATE TABLE scheduled_predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id TEXT NOT NULL,
    creator_id TEXT NOT NULL,
    title TEXT NOT NULL,
    opt1 TEXT NOT NULL,
    opt2 TEXT NOT NULL,
    options_json TEXT,
    duration_minutes INTEGER NOT NULL,
    scheduled_ts INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at INTEGER NOT NULL,
    cm_message_id TEXT
);
"""


@contextmanager
def _fresh_db():
    import database
    old_file, old_conn = database.DB_FILE, database.DatabaseManager._connection
    database.DatabaseManager._connection = None
    with tempfile.TemporaryDirectory() as tmp:
        database.DB_FILE = os.path.join(tmp, "test.db")
        conn = database.DatabaseManager.get_connection()
        conn.executescript(SCHEMA)
        conn.commit()
        try:
            yield database.DatabaseManager
        finally:
            conn.close()
            database.DatabaseManager._connection = old_conn
            database.DB_FILE = old_file


def _client(channels=None):
    channels = channels or {}
    client = MagicMock()
    client.get_channel = MagicMock(side_effect=lambda cid: channels.get(cid))
    client.fetch_channel = AsyncMock(side_effect=discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), "gone"))
    client.scheduler = MagicMock()
    client.predictions = {}
    return client


def _queue(tmp_path, client=None):
    base = str(tmp_path / "bot_actions")
    ba.ensure_dirs(base)
    return ba.BotActionQueue(client or _client(), base)


def _drop(q, req_id, action, args=None, ts=None, raw=None):
    path = os.path.join(q.inbox, f"{req_id}.json")
    with open(path, "w") as f:
        if raw is not None:
            f.write(raw)
        else:
            json.dump({"id": req_id, "action": action, "args": args or {},
                       "ts": time.time() if ts is None else ts}, f)
    return path


def _result(q, req_id):
    with open(os.path.join(q.outbox, f"{req_id}.json")) as f:
        return json.load(f)


@pytest.fixture
def fake_action():
    calls = []

    async def handler(client, args):
        calls.append(args)
        return {"echo": args}

    with patch.dict(ba.ACTIONS, {"echo": ({"n": ("int", False)}, handler)}):
        yield calls


# ------------------------------------------------------------------ arg checks

def test_check_args_refuses_unknown_missing_and_mistyped():
    spec = {"channel_id": ("int", True), "title": ("str", True), "options": ("str_list", False)}
    with pytest.raises(ba.ActionError, match="Unknown args: code"):
        ba.check_args(spec, {"channel_id": 1, "title": "x", "code": "import os"})
    with pytest.raises(ba.ActionError, match="Missing `title`"):
        ba.check_args(spec, {"channel_id": 1})
    with pytest.raises(ba.ActionError, match="whole number"):
        ba.check_args(spec, {"channel_id": "abc", "title": "x"})
    with pytest.raises(ba.ActionError, match="whole number"):
        ba.check_args(spec, {"channel_id": True, "title": "x"})
    with pytest.raises(ba.ActionError, match="list of text"):
        ba.check_args(spec, {"channel_id": 1, "title": "x", "options": ["a", 2]})
    with pytest.raises(ba.ActionError, match="must be an object"):
        ba.check_args(spec, ["channel_id"])


def test_check_args_coerces_digit_strings_for_ids():
    out = ba.check_args({"channel_id": ("int", True), "duration": ("str_or_int", True)},
                        {"channel_id": "1141037835445616640", "duration": 90})
    assert out == {"channel_id": 1141037835445616640, "duration": "90"}


# ------------------------------------------------------------------ the queue

def test_a_listed_action_runs_once_and_writes_its_result(tmp_path, fake_action):
    q = _queue(tmp_path)
    _drop(q, "req1", "echo", {"n": 3})
    assert asyncio.run(q.poll_once()) == 1
    res = _result(q, "req1")
    assert res["ok"] is True and res["result"] == {"echo": {"n": 3}}
    assert fake_action == [{"n": 3}]
    assert os.listdir(q.inbox) == [] and os.listdir(q.processing) == []
    # Nothing left to run a second time.
    assert asyncio.run(q.poll_once()) == 0
    assert len(fake_action) == 1


def test_unknown_action_is_refused(tmp_path):
    q = _queue(tmp_path)
    _drop(q, "req2", "exec_python", {"code": "print(1)"})
    asyncio.run(q.poll_once())
    res = _result(q, "req2")
    assert res["ok"] is False and "Unknown action" in res["error"]


def test_bad_args_are_refused_before_running(tmp_path, fake_action):
    q = _queue(tmp_path)
    _drop(q, "req3", "echo", {"n": 1, "extra": 2})
    asyncio.run(q.poll_once())
    assert _result(q, "req3")["ok"] is False
    assert fake_action == []


def test_stale_request_is_refused_unrun(tmp_path, fake_action):
    q = _queue(tmp_path)
    _drop(q, "old", "echo", {}, ts=time.time() - ba.STALE_SECONDS - 5)
    asyncio.run(q.poll_once())
    res = _result(q, "old")
    assert res["ok"] is False and "stale" in res["error"]
    assert fake_action == []


def test_bad_json_and_mismatched_id_get_errors(tmp_path, fake_action):
    q = _queue(tmp_path)
    _drop(q, "broken", None, raw="{not json")
    _drop(q, "named", None, raw=json.dumps({"id": "other", "action": "echo", "args": {}, "ts": time.time()}))
    asyncio.run(q.poll_once())
    assert "valid JSON" in _result(q, "broken")["error"]
    assert "match its file name" in _result(q, "named")["error"]
    assert fake_action == []


def test_bad_file_name_gets_no_result_and_no_run(tmp_path, fake_action):
    q = _queue(tmp_path)
    path = os.path.join(q.inbox, "bad name!.json")
    with open(path, "w") as f:
        json.dump({"id": "bad name!", "action": "echo", "args": {}, "ts": time.time()}, f)
    asyncio.run(q.poll_once())
    assert os.listdir(q.outbox) == [] and os.listdir(q.inbox) == []
    assert fake_action == []


def test_symlinked_request_is_refused(tmp_path, fake_action):
    q = _queue(tmp_path)
    target = tmp_path / "elsewhere.json"
    target.write_text(json.dumps({"id": "link", "action": "echo", "args": {}, "ts": time.time()}))
    os.symlink(target, os.path.join(q.inbox, "link.json"))
    asyncio.run(q.poll_once())
    assert fake_action == []
    assert target.exists()   # the target is never touched


def test_rate_limit_leaves_the_rest_for_a_later_poll(tmp_path, fake_action):
    q = _queue(tmp_path)
    for i in range(ba.RATE_LIMIT + 2):
        _drop(q, f"r{i}", "echo", {"n": i})
    assert asyncio.run(q.poll_once()) == ba.RATE_LIMIT
    assert len(os.listdir(q.inbox)) == 2
    assert len(fake_action) == ba.RATE_LIMIT


def test_a_crashing_action_becomes_an_error_and_the_queue_carries_on(tmp_path, fake_action):
    async def boom(client, args):
        raise RuntimeError("kaboom")

    q = _queue(tmp_path)
    with patch.dict(ba.ACTIONS, {"boom": ({}, boom)}):
        _drop(q, "a", "boom")
        os.utime(os.path.join(q.inbox, "a.json"), (1, 1))   # oldest, so it runs first
        _drop(q, "b", "echo", {"n": 1})
        asyncio.run(q.poll_once())
    assert _result(q, "a")["ok"] is False
    assert "kaboom" in _result(q, "a")["error"]
    assert _result(q, "b")["ok"] is True


def test_actions_are_logged_to_bot_usage_log(tmp_path, fake_action):
    log = SimpleNamespace(send=AsyncMock())
    q = _queue(tmp_path, _client({ba.CHANNELS.BOT_USAGE_LOG: log}))
    _drop(q, "logged", "echo", {"n": 7})
    asyncio.run(q.poll_once())
    text = log.send.await_args.args[0]
    assert "`echo`" in text and str(ba.ACTOR_ID) in text and '"n": 7' in text


def test_dirs_are_private(tmp_path):
    q = _queue(tmp_path)
    for d in (q.base, q.inbox, q.outbox, q.processing):
        assert os.stat(d).st_mode & 0o777 == 0o700


def test_watcher_starts_once(tmp_path):
    async def go():
        client = SimpleNamespace()
        with patch.object(ba, "ACTIONS_DIR", str(tmp_path / "q")):
            first = ba.start_bot_action_watcher(client)
            second = ba.start_bot_action_watcher(client)
            assert first is second
            first.cancel()
    asyncio.run(go())


# ------------------------------------------------------------------ pred actions

def test_pred_schedule_writes_the_row_and_adds_the_live_job(tmp_path):
    channel = SimpleNamespace(id=555, send=AsyncMock())
    cm = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=999)))
    client = _client({555: channel, ps.CHANNELS.COMMUNITY_MANAGEMENT: cm})
    q = _queue(tmp_path, client)
    with _fresh_db() as db:
        _drop(q, "s1", "pred_schedule", {"channel_id": 555, "title": " England vs Greece ",
                                          "options": ["England", "Draw", "Greece"],
                                          "when": "30", "duration": 90})
        asyncio.run(q.poll_once())
        res = _result(q, "s1")
        assert res["ok"] is True, res
        sched_id = res["result"]["sched_id"]
        row = db.fetch_one("SELECT channel_id, creator_id, title, options_json, duration_minutes, "
                           "status, cm_message_id FROM scheduled_predictions WHERE id = ?", (sched_id,))
    assert row == ("555", str(ba.ACTOR_ID), "England vs Greece",
                   json.dumps(["England", "Draw", "Greece"]), 90, "pending", "999")
    job = client.scheduler.add_job.call_args
    assert job.kwargs["id"] == f"scheduled_pred_{sched_id}"
    assert job.kwargs["args"] == [client, sched_id]
    assert cm.send.await_args.kwargs["embed"].title == "📅 Prediction Scheduled"


def test_pred_schedule_bad_input_is_refused_without_a_row(tmp_path):
    client = _client({555: SimpleNamespace(id=555, send=AsyncMock())})
    q = _queue(tmp_path, client)
    with _fresh_db() as db:
        _drop(q, "s2", "pred_schedule", {"channel_id": 555, "title": "x", "options": ["only one"],
                                          "when": "30", "duration": "90"})
        _drop(q, "s3", "pred_schedule", {"channel_id": 777, "title": "x", "options": ["a", "b"],
                                          "when": "30", "duration": "90"})
        asyncio.run(q.poll_once())
        assert db.fetch_all("SELECT id FROM scheduled_predictions") == []
    assert "at least" in _result(q, "s2")["error"]
    assert "Channel 777" in _result(q, "s3")["error"]
    client.scheduler.add_job.assert_not_called()


def test_pred_cancel_scheduled_drops_the_row_and_the_job(tmp_path):
    client = _client()
    q = _queue(tmp_path, client)
    with _fresh_db() as db:
        sched_id = db.execute_insert(
            "INSERT INTO scheduled_predictions (channel_id, creator_id, title, opt1, opt2, duration_minutes, "
            "scheduled_ts, status, created_at) VALUES ('1', '2', 't', 'a', 'b', 5, 9999999999, 'pending', 0)")
        _drop(q, "c1", "pred_cancel_scheduled", {"sched_id": sched_id})
        asyncio.run(q.poll_once())
        status = db.fetch_one("SELECT status FROM scheduled_predictions WHERE id = ?", (sched_id,))[0]
        _drop(q, "c2", "pred_list_scheduled")
        asyncio.run(q.poll_once())
    assert _result(q, "c1")["ok"] is True and status == "cancelled"
    client.scheduler.remove_job.assert_called_once_with(f"scheduled_pred_{sched_id}")
    assert _result(q, "c2")["result"] == []


def test_pred_create_posts_a_live_prediction(tmp_path):
    channel = SimpleNamespace(id=555, send=AsyncMock())
    client = _client({555: channel})
    q = _queue(tmp_path, client)

    async def fake_post(cl, ch, title, options, end_ts):
        p = ps.Prediction(42, title, options, end_ts, channel_id=ch.id)
        cl.predictions[42] = p
        return p

    with patch.object(ps, "post_prediction", side_effect=fake_post) as post:
        _drop(q, "p1", "pred_create", {"channel_id": "555", "title": "Who wins", "options": ["A", "B"],
                                        "duration": "60"})
        asyncio.run(q.poll_once())
    res = _result(q, "p1")
    assert res["ok"] is True and res["result"]["message_id"] == 42
    assert post.call_args.args[1] is channel


def test_pred_lock_and_unlock_flip_the_live_prediction(tmp_path):
    client = _client()
    pred = ps.Prediction(42, "Who wins", ["A", "B"], end_ts=0, channel_id=555)
    client.predictions = {42: pred}
    q = _queue(tmp_path, client)
    with patch.object(ps, "_fetch_pred_message_in", AsyncMock(return_value=MagicMock())), \
            patch.object(ps, "_edit_prediction_message", AsyncMock()) as edit, \
            patch.object(ps, "_save"), \
            patch.object(ps, "award_indecisive_badges", AsyncMock()) as badges:
        _drop(q, "l1", "pred_lock", {"message_id": 42})
        asyncio.run(q.poll_once())
        assert pred.locked and _result(q, "l1")["ok"] is True
        assert edit.await_args.kwargs["interactive"] is False
        badges.assert_awaited_once()
        _drop(q, "l2", "pred_lock", {"message_id": 42})
        asyncio.run(q.poll_once())
        assert _result(q, "l2")["error"] == "Already locked."
        _drop(q, "l3", "pred_unlock", {"message_id": 42})
        _drop(q, "l4", "pred_lock", {"message_id": 7})
        asyncio.run(q.poll_once())
    assert not pred.locked and _result(q, "l3")["ok"] is True
    assert "No live prediction" in _result(q, "l4")["error"]


# ------------------------------------------------------------------ slash commands unchanged

def _interaction(client):
    return SimpleNamespace(
        client=client,
        user=SimpleNamespace(id=1234, mention="<@1234>"),
        channel=SimpleNamespace(id=555),
        response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )


def test_slash_pred_schedule_still_schedules_and_announces():
    cm = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=999)))
    client = _client({ps.CHANNELS.COMMUNITY_MANAGEMENT: cm})
    inter = _interaction(client)
    with _fresh_db() as db:
        asyncio.run(ps.handle_pred_schedule_command(inter, 555, "Match", ["A", "B"], "30", "45"))
        row = db.fetch_one("SELECT creator_id, duration_minutes, cm_message_id FROM scheduled_predictions")
    assert row == ("1234", 45, "999")
    assert "scheduled for" in inter.response.send_message.await_args.args[0]
    client.scheduler.add_job.assert_called_once()
    assert cm.send.await_args.kwargs["embed"].fields[3].value == "<@1234>"


def test_slash_pred_schedule_still_reports_bad_input():
    client = _client()
    inter = _interaction(client)
    with _fresh_db():
        asyncio.run(ps.handle_pred_schedule_command(inter, 555, "  ", ["A", "B"], "30", "45"))
    assert inter.response.send_message.await_args.args[0] == "The title can't be empty."
    client.scheduler.add_job.assert_not_called()


def test_slash_pred_create_still_posts():
    client = _client()
    inter = _interaction(client)
    with patch.object(ps, "post_prediction", AsyncMock()) as post:
        asyncio.run(ps.handle_pred_create_command(inter, "Match", ["A", "B", None], "30"))
    assert post.await_args.args[3] == ["A", "B"]
    assert "2 outcomes" in inter.followup.send.await_args.args[0]


# ------------------------------------------------------------------ the local helper

HELPER = os.path.join(ROOT, ".claude", "skills", "watcher", "scripts", "bot_action.py")


def test_helper_refuses_unknown_actions_and_bad_json():
    bad = subprocess.run([sys.executable, HELPER, "rm_rf"], capture_output=True, text=True)
    assert bad.returncode != 0 and "unknown action" in bad.stderr
    bad = subprocess.run([sys.executable, HELPER, "pred_list", "{nope"], capture_output=True, text=True)
    assert bad.returncode != 0 and "valid JSON" in bad.stderr


def test_helper_script_round_trips_through_the_queue(tmp_path):
    """The VM script the helper prints drops a request, the queue runs it, and the
    script prints the result."""
    vm_script = subprocess.run(
        [sys.executable, HELPER, "pred_list_scheduled"], capture_output=True, text=True, check=True).stdout
    q = _queue(tmp_path)   # creates tmp_path/bot_actions/{inbox,outbox,processing}
    os.makedirs(tmp_path / "data", exist_ok=True)
    os.rename(q.base, tmp_path / "data" / "bot_actions")
    q = ba.BotActionQueue(_client(), str(tmp_path / "data" / "bot_actions"))
    proc = subprocess.Popen([sys.executable, "-"], cwd=tmp_path, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    proc.stdin.write(vm_script)
    proc.stdin.close()
    deadline = time.time() + 10
    while not [n for n in os.listdir(q.inbox) if n.endswith(".json")] and time.time() < deadline:
        time.sleep(0.05)
    with _fresh_db():
        assert asyncio.run(q.poll_once()) == 1
    out, err = proc.communicate(timeout=10)
    assert proc.returncode == 0, err
    res = json.loads(out)
    assert res["ok"] is True and res["action"] == "pred_list_scheduled" and res["result"] == []
    assert os.listdir(q.outbox) == []   # the helper collects its result
