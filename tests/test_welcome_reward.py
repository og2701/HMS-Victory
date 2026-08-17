"""Welcoming pays for a reply, not for the word 'welcome'."""

import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from lib.features import ukp_rewards as W


class FakeUser:
    def __init__(self, uid, bot=False):
        self.id = uid
        self.bot = bot
        self.sent = []

    async def send(self, text):
        self.sent.append(text)


class FakeChannel:
    def __init__(self, cid=1):
        self.id = cid


class FakeMsg:
    def __init__(self, author, content="", mentions=(), reply_to=None, channel=None):
        self.author = author
        self.content = content
        self.mentions = list(mentions)
        self.guild = object()
        self.channel = channel or FakeChannel()
        self.reference = None
        if reply_to is not None:
            self.reference = types.SimpleNamespace(message_id=99, resolved=reply_to)


class FakeClient:
    def __init__(self, users):
        self._users = {u.id: u for u in users}

    def get_user(self, uid):
        return self._users.get(int(uid))


def _fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "WELCOME_TRACKING_FILE", str(tmp_path / "welcome.json"))
    monkeypatch.setattr(config, "EARNED_SOURCES_FILE", str(tmp_path / "earned.json"))
    paid = []
    monkeypatch.setattr(W, "_pay", lambda uid, amt, reason: paid.append((uid, amt)) or True)
    monkeypatch.setattr(W, "_refresh_tracked", W._refresh_tracked)
    W._tracked, W._tracked_loaded = set(), False
    return paid


def _run(client, msg):
    asyncio.run(W.handle_welcome_reward(client, msg))


def test_a_bare_welcome_earns_nothing_until_they_answer(monkeypatch, tmp_path):
    paid = _fresh(monkeypatch, tmp_path)
    newcomer, greeter = FakeUser(2), FakeUser(1)
    W.register_new_member_join(newcomer)
    client = FakeClient([newcomer, greeter])

    _run(client, FakeMsg(greeter, "welcome!", mentions=[newcomer]))
    assert paid == [], "the greeting alone must not pay"

    # The newcomer replies to them: now it pays.
    _run(client, FakeMsg(newcomer, "thanks!", mentions=[greeter]))
    assert paid == [(1, config.WELCOME_REWARD)]


def test_a_welcome_nobody_answers_never_pays(monkeypatch, tmp_path):
    paid = _fresh(monkeypatch, tmp_path)
    newcomer, greeter, other = FakeUser(2), FakeUser(1), FakeUser(3)
    W.register_new_member_join(newcomer)
    client = FakeClient([newcomer, greeter, other])

    _run(client, FakeMsg(greeter, "welcome", mentions=[newcomer]))
    # The newcomer talks, but to somebody else.
    _run(client, FakeMsg(newcomer, "hi", mentions=[other]))
    assert paid == []


def test_going_back_to_them_later_pays_the_follow_up(monkeypatch, tmp_path):
    paid = _fresh(monkeypatch, tmp_path)
    newcomer, greeter = FakeUser(2), FakeUser(1)
    W.register_new_member_join(newcomer)
    client = FakeClient([newcomer, greeter])

    _run(client, FakeMsg(greeter, "welcome", mentions=[newcomer]))
    _run(client, FakeMsg(newcomer, "ta", mentions=[greeter]))
    assert paid == [(1, config.WELCOME_REWARD)]

    # Same conversation: too soon to count as going back to them.
    _run(client, FakeMsg(greeter, "how are you finding it", mentions=[newcomer]))
    assert len(paid) == 1

    # An hour on, it is a separate occasion.
    store = W.load_json_file(config.WELCOME_TRACKING_FILE)
    store["2"]["joined_at"] -= config.WELCOME_FOLLOWUP_MIN_HOURS * 3600 + 60
    W.save_json_file(config.WELCOME_TRACKING_FILE, store)
    _run(client, FakeMsg(greeter, "settling in ok?", mentions=[newcomer]))
    assert paid[-1] == (1, config.WELCOME_FOLLOWUP_REWARD)

    # ...and only once.
    _run(client, FakeMsg(greeter, "still here?", mentions=[newcomer]))
    assert len(paid) == 2


def test_the_reply_has_to_be_reasonably_prompt(monkeypatch, tmp_path):
    paid = _fresh(monkeypatch, tmp_path)
    newcomer, greeter = FakeUser(2), FakeUser(1)
    W.register_new_member_join(newcomer)
    client = FakeClient([newcomer, greeter])
    _run(client, FakeMsg(greeter, "welcome", mentions=[newcomer]))

    store = W.load_json_file(config.WELCOME_TRACKING_FILE)
    store["2"]["pending"]["1"] -= config.WELCOME_REPLY_WINDOW_MINUTES * 60 + 60
    W.save_json_file(config.WELCOME_TRACKING_FILE, store)

    _run(client, FakeMsg(newcomer, "sorry, was away", mentions=[greeter]))
    assert paid == []


def test_the_greeter_cap_still_holds(monkeypatch, tmp_path):
    paid = _fresh(monkeypatch, tmp_path)
    newcomer = FakeUser(100)
    W.register_new_member_join(newcomer)
    greeters = [FakeUser(i) for i in range(1, config.WELCOME_MAX_WELCOMERS + 3)]
    client = FakeClient([newcomer, *greeters])
    for g in greeters:
        _run(client, FakeMsg(g, "welcome", mentions=[newcomer]))
    store = W.load_json_file(config.WELCOME_TRACKING_FILE)
    assert len(store["100"]["pending"]) == config.WELCOME_MAX_WELCOMERS


def test_you_cannot_welcome_yourself(monkeypatch, tmp_path):
    paid = _fresh(monkeypatch, tmp_path)
    newcomer = FakeUser(2)
    W.register_new_member_join(newcomer)
    client = FakeClient([newcomer])
    _run(client, FakeMsg(newcomer, "welcome me", mentions=[newcomer]))
    assert paid == []


def test_old_records_are_read_as_already_paid(monkeypatch, tmp_path):
    """A record written by the old scheme must not pay its welcomers a second time."""
    paid = _fresh(monkeypatch, tmp_path)
    import time
    W.save_json_file(config.WELCOME_TRACKING_FILE,
                     {"2": {"joined_at": int(time.time()), "welcomers": [1]}})
    newcomer, greeter = FakeUser(2), FakeUser(1)
    client = FakeClient([newcomer, greeter])
    _run(client, FakeMsg(newcomer, "thanks", mentions=[greeter]))
    assert paid == []


def test_the_hot_path_gate_ignores_unrelated_chatter(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    newcomer, greeter, bystander = FakeUser(2), FakeUser(1), FakeUser(9)
    W.register_new_member_join(newcomer)
    assert W.welcome_activity_possible(FakeMsg(greeter, "hi", mentions=[newcomer]))
    assert W.welcome_activity_possible(FakeMsg(newcomer, "hello all"))
    assert not W.welcome_activity_possible(FakeMsg(bystander, "anyway, football"))
