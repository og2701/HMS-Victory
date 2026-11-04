"""The task board in #bot-workshop: who may press what (the watcher skill's go rules ride in the
custom_id), a press moving the row to received and leaving one event for watch.py, the board
staying inside Components V2's limits however full it gets, done rows rolling off, and the
board.py helper building a VM script for each op."""

import asyncio
import json
import os
import subprocess
import sys
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pytest  # noqa: E402

from config import USERS  # noqa: E402
from lib.bot import cabinet_board as cb  # noqa: E402

ASKER = 111111111111111111
RANDO = 222222222222222222
BOARD_PY = os.path.join(ROOT, ".claude", "skills", "watcher", "scripts", "board.py")


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def board(tmp_path, monkeypatch):
    monkeypatch.setattr(cb, "STATE_FILE", str(tmp_path / "workshop.json"))
    monkeypatch.setattr(cb, "LOCK_FILE", str(tmp_path / "workshop.lock"))
    monkeypatch.setattr(cb, "EVENTS_DIR", str(tmp_path / "events"))
    state = cb.empty_state()
    state["message_id"] = "900"
    cb.apply_op(state, "add", {"id": "1001", "asker": ASKER, "what": "Close the maybe-later ideas",
                               "status": "waiting", "rule": "oggers"}, now=100)
    cb.apply_op(state, "add", {"id": "1002", "asker": ASKER, "what": "Rename a channel",
                               "status": "waiting", "rule": "asker"}, now=101)
    cb.apply_op(state, "add", {"id": "1003", "asker": ASKER, "what": "Fix overwrites",
                               "status": "waiting", "rule": "mods"}, now=102)
    cb.save_state(state)
    return tmp_path


def _events(tmp_path):
    d = tmp_path / "events"
    return [json.loads(p.read_text()) for p in sorted(d.glob("*.json"))] if d.exists() else []


# ---------------------------------------------------------------- who may press

@pytest.mark.parametrize("verb,rule,user,ok", [
    ("ok", "o", USERS.OGGERS, True),
    ("ok", "o", USERS.HADIDAS, False),
    ("ok", "o", ASKER, False),
    ("no", "o", ASKER, True),          # the asker can withdraw their own request
    ("say", "o", ASKER, True),         # and clarify it
    ("say", "o", RANDO, False),
    ("ok", "m", USERS.HADIDAS, True),
    ("ok", "m", USERS.OGGERS, True),
    ("ok", "m", ASKER, False),
    ("ok", "a", ASKER, True),
    ("ok", "a", USERS.OGGERS, True),
    ("ok", "a", RANDO, False),
    ("no", "a", RANDO, False),
])
def test_may_press(verb, rule, user, ok):
    assert cb.may_press(verb, rule, ASKER, user) is ok


def test_custom_ids_round_trip_through_the_templates():
    for cls, verb in ((cb.ApproveButton, "ok"), (cb.RejectButton, "no"), (cb.ReplyButton, "say")):
        cid = cb.custom_id(verb, 1567519466982613035, "m", ASKER)
        assert len(cid) <= 100
        m = cls.__discord_ui_compiled_template__.fullmatch(cid)
        assert m and m["tid"] == "1567519466982613035" and m["rule"] == "m" and m["asker"] == str(ASKER)
        others = [c for c in cb.BUTTONS if c is not cls]
        assert not any(c.__discord_ui_compiled_template__.fullmatch(cid) for c in others)
        btn = _run(cls.from_custom_id(None, None, m))
        assert btn.item.custom_id == cid


# ---------------------------------------------------------------- presses

def test_approve_moves_the_row_to_received_and_writes_one_event(board):
    ok, msg = cb.record_press("ok", 1001, USERS.OGGERS, 5555, now=200)
    assert ok and "#1" in msg
    task = cb.find(cb.load_state(), "1001")
    assert task["status"] == "received"
    assert "Go from" in task["note"] and "processing" in task["note"]
    [ev] = _events(board)
    assert ev["action"] == "approve" and ev["task"] == "1001" and ev["user_id"] == str(USERS.OGGERS)
    assert ev["interaction"] == "5555"
    # A second press (a double click, or someone else) can't approve it again.
    ok, msg = cb.record_press("ok", 1001, USERS.OGGERS, 5556, now=201)
    assert not ok and "received" in msg
    assert len(_events(board)) == 1


def test_reply_carries_the_text_and_reject_says_no(board):
    assert cb.record_press("say", 1002, ASKER, 1, text="go but cap it at 10k")[0]
    assert cb.record_press("no", 1003, USERS.HADIDAS, 2)[0]
    evs = {e["task"]: e for e in _events(board)}
    assert evs["1002"]["action"] == "reply" and evs["1002"]["text"] == "go but cap it at 10k"
    assert evs["1003"]["action"] == "reject"
    assert "No from" in cb.find(cb.load_state(), "1003")["note"]


def test_press_on_a_missing_or_finished_row(board):
    ok, msg = cb.record_press("ok", 4242, USERS.OGGERS, 1)
    assert not ok and "isn't on the board" in msg
    state = cb.load_state()
    cb.apply_op(state, "set", {"id": "1001", "status": "done"})
    cb.save_state(state)
    assert not cb.record_press("ok", 1001, USERS.OGGERS, 2)[0]
    assert _events(board) == []


class _Response:
    def __init__(self):
        self.sent, self.modal = [], None

    async def send_message(self, content=None, **kw):
        self.sent.append((content, kw.get("ephemeral")))

    async def send_modal(self, modal):
        self.modal = modal


class _Http:
    def __init__(self):
        self.calls = []

    async def request(self, route, **kw):
        self.calls.append((route.method, route.path, kw.get("json")))


def _interaction(user_id, iid=77):
    client = SimpleNamespace(http=_Http(), get_channel=lambda cid: None)
    return SimpleNamespace(user=SimpleNamespace(id=user_id, __str__=lambda s: "u"), id=iid,
                           response=_Response(), client=client)


def test_someone_else_gets_a_private_waiting_note_and_nothing_is_recorded(board):
    i = _interaction(RANDO)
    _run(cb.ApproveButton(1001, "o", ASKER).callback(i))
    [(msg, eph)] = i.response.sent
    assert eph and "waiting on" in msg and str(USERS.OGGERS) in msg
    assert _events(board) == [] and cb.find(cb.load_state(), "1001")["status"] == "waiting"
    assert i.client.http.calls == []


def test_the_right_press_answers_privately_and_redraws_the_board(board):
    i = _interaction(USERS.OGGERS)
    _run(cb.ApproveButton(1001, "o", ASKER).callback(i))
    [(msg, eph)] = i.response.sent
    assert eph and "go" in msg.lower()
    [(method, path, body)] = i.client.http.calls
    assert method == "PATCH" and path.endswith("/messages/{message_id}")
    text = json.dumps(body, ensure_ascii=False)
    assert "Received" in text and "Go from" in text
    assert len(_events(board)) == 1


def test_reply_opens_a_modal_and_its_submit_is_recorded(board):
    i = _interaction(ASKER)
    _run(cb.ReplyButton(1002, "a", ASKER).callback(i))
    modal = i.response.modal
    assert isinstance(modal, cb.ReplyModal)
    modal.text._value = "yes please"
    j = _interaction(ASKER, iid=78)
    _run(modal.on_submit(j))
    [ev] = _events(board)
    assert ev["action"] == "reply" and ev["text"] == "yes please"


# ---------------------------------------------------------------- the board itself

def _count(components):
    return sum(1 + _count(c.get("components", [])) for c in components)


def _text(components):
    return sum(len(c.get("content", "")) + _text(c.get("components", [])) for c in components)


def _buttons(components):
    out = []
    for c in components:
        if c.get("type") == 2:
            out.append(c)
        out += _buttons(c.get("components", []))
    return out


def test_a_crowded_board_stays_inside_cv2_limits():
    state = cb.empty_state()
    for n in range(40):
        status = cb.STATUSES[n % len(cb.STATUSES)]
        cb.apply_op(state, "add", {"id": str(10_000 + n), "asker": ASKER, "what": "x" * 300,
                                   "status": status, "note": "n" * 300, "link": str(20_000 + n)}, now=n)
    comps = cb.render(state, now=1000)
    assert _count(comps) <= cb.MAX_COMPONENTS
    assert _text(comps) <= 4000
    assert len(_buttons(comps)) == 3 * cb.MAX_BUTTON_ROWS


def test_buttons_only_on_waiting_rows_and_rows_link_to_their_request():
    state = cb.empty_state()
    cb.apply_op(state, "add", {"id": "1", "asker": ASKER, "what": "a", "status": "waiting", "link": "9"}, now=1)
    cb.apply_op(state, "add", {"id": "2", "asker": ASKER, "what": "b", "status": "working"}, now=2)
    comps = cb.render(state, now=3)
    ids = [b["custom_id"] for b in _buttons(comps)]
    assert ids == [cb.custom_id(v, 1, "o", ASKER) for v in ("ok", "no", "say")]
    text = json.dumps(comps, ensure_ascii=False)
    assert "/1141037835445616640/1)" in text and "/1141037835445616640/9)" in text and "plan ↗" in text
    assert comps[0]["accent_color"] == cb.ACCENTS["waiting"]


def test_empty_board_renders():
    comps = cb.render(cb.empty_state(), now=1)
    assert "Nothing on" in json.dumps(comps)


def test_done_rows_roll_off_into_history():
    state = cb.empty_state()
    for n in range(8):
        cb.apply_op(state, "add", {"id": str(n + 1), "asker": ASKER, "what": f"t{n}", "status": "done"}, now=n)
    done = [t for t in state["tasks"] if t["status"] == "done"]
    assert len(done) == cb.KEEP_DONE
    assert [t["id"] for t in state["history"]] == ["1", "2", "3"]


def test_ops_validate_their_input():
    state = cb.empty_state()
    with pytest.raises(ValueError):
        cb.apply_op(state, "set", {"id": "5", "status": "done"})
    with pytest.raises(ValueError):
        cb.apply_op(state, "add", {"id": "5", "asker": ASKER, "what": "x", "status": "sleeping"})
    with pytest.raises(ValueError):
        cb.apply_op(state, "add", {"id": "5", "asker": ASKER, "what": "x", "rule": "anyone"})
    task = cb.apply_op(state, "add", {"id": "5", "asker": ASKER, "what": "x", "rule": "mods"})
    assert task["rule"] == "m" and task["status"] == "working" and task["no"] == 1
    again = cb.apply_op(state, "add", {"id": "5", "asker": ASKER, "what": "y"})
    assert again["no"] == 1 and len(state["tasks"]) == 1
    cb.apply_op(state, "remove", {"id": "5"})
    assert state["tasks"] == []


# ---------------------------------------------------------------- board.py

@pytest.mark.parametrize("argv", [
    ["init"], ["show"], ["redraw"],
    ["add", "1567519466982613035", str(ASKER), "Task board", "waiting", "--rule", "oggers", "--link", "42"],
    ["set", "1567519466982613035", "done", "--note", "shipped", "--link", "43"],
    ["set", "1567519466982613035", "-", "--note", ""],
    ["remove", "1567519466982613035"],
    ["set", "1567519466982613035", "working", "--edit"],
])
def test_board_helper_builds_a_vm_script(argv):
    out = subprocess.run([sys.executable, BOARD_PY, *argv], capture_output=True, text=True, check=True).stdout
    compile(out, "board_vm.py", "exec")
    assert "from lib.bot import cabinet_board as cb" in out


@pytest.mark.parametrize("argv", [[], ["nope"], ["add", "1", "2"], ["set", "abc", "done"],
                                  ["add", "1", "2", "x", "--link", "notanid"]])
def test_board_helper_refuses_bad_input(argv):
    r = subprocess.run([sys.executable, BOARD_PY, *argv], capture_output=True, text=True)
    assert r.returncode != 0
