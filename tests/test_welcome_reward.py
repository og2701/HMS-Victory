"""Welcoming pays for the conversation, not for the word 'welcome'.

A welcome is judged on what the greeter did once the newcomer answered. Being ignored by a
newcomer is not held against anyone - there was nothing there to carry on. Saying hello,
getting a hello back, and disappearing is the thing that counts as dry.
"""

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
    monkeypatch.setattr(config, "WELCOME_REPUTATION_FILE", str(tmp_path / "rep.json"))
    paid = []
    monkeypatch.setattr(W, "_pay", lambda uid, amt, reason: paid.append((uid, amt)) or True)
    monkeypatch.setattr(W, "_refresh_tracked", W._refresh_tracked)
    W._tracked, W._tracked_loaded = set(), False
    return paid


def _run(client, msg):
    asyncio.run(W.handle_welcome_reward(client, msg))


def test_a_welcome_pays_straight_away_by_default(monkeypatch, tmp_path):
    paid = _fresh(monkeypatch, tmp_path)
    newcomer, greeter = FakeUser(2), FakeUser(1)
    W.register_new_member_join(newcomer)
    client = FakeClient([newcomer, greeter])

    _run(client, FakeMsg(greeter, "welcome!", mentions=[newcomer]))
    assert paid == [(1, config.WELCOME_REWARD)], "the ordinary case pays on the greeting"


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


def test_the_greeter_cap_counts_held_and_paid_alike(monkeypatch, tmp_path):
    paid = _fresh(monkeypatch, tmp_path)
    newcomer = FakeUser(200)
    W.register_new_member_join(newcomer)
    greeters = [FakeUser(i) for i in range(1, config.WELCOME_MAX_WELCOMERS + 3)]
    client = FakeClient([newcomer, *greeters])
    for g in greeters:
        _run(client, FakeMsg(g, "welcome", mentions=[newcomer]))
    assert len(paid) == config.WELCOME_MAX_WELCOMERS


def test_the_greeter_cap_still_holds(monkeypatch, tmp_path):
    paid = _fresh(monkeypatch, tmp_path)
    newcomer = FakeUser(100)
    W.register_new_member_join(newcomer)
    greeters = [FakeUser(i) for i in range(1, config.WELCOME_MAX_WELCOMERS + 3)]
    client = FakeClient([newcomer, *greeters])
    for g in greeters:
        _run(client, FakeMsg(g, "welcome", mentions=[newcomer]))
    store = W.load_json_file(config.WELCOME_TRACKING_FILE)
    assert len(store["100"]["paid"]) == config.WELCOME_MAX_WELCOMERS


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


# ---------------------------------------------------------------------------
# The payout is earned back, not removed: greet without ever engaging and your
# greeting stops paying up front; have a couple of real conversations and it returns.
# ---------------------------------------------------------------------------
def _expire(uid):
    """Age a newcomer's record past its life and prune, which is what banks the outcome."""
    store = W.load_json_file(config.WELCOME_TRACKING_FILE)
    store[str(uid)]["joined_at"] -= W._record_life_secs() + 60
    W.save_json_file(config.WELCOME_TRACKING_FILE, store)
    W._load_store()


def _dry_welcome(client, greeter, uid, monkeypatch=None):
    """Say hello, get a hello back, then say nothing. This is the dry case.

    The newcomer has to answer the greeter for it to count - being ignored is nobody's
    fault - and the greeter then has to let the conversation die.
    """
    newcomer = FakeUser(uid)
    W.register_new_member_join(newcomer)
    _run(client, FakeMsg(greeter, "welcome", mentions=[newcomer]))
    _run(client, FakeMsg(newcomer, "hello", mentions=[greeter]))
    _expire(uid)


def _good_welcome(client, greeter, uid):
    """Say hello, get a hello back, and answer them. This is what the payout is for."""
    newcomer = FakeUser(uid)
    W.register_new_member_join(newcomer)
    _run(client, FakeMsg(greeter, "welcome", mentions=[newcomer]))
    _run(client, FakeMsg(newcomer, "hello", mentions=[greeter]))
    _run(client, FakeMsg(greeter, "how are you finding it?", mentions=[newcomer]))
    _expire(uid)


def test_a_run_of_dead_welcomes_makes_the_greeting_earn_its_money(monkeypatch, tmp_path):
    paid = _fresh(monkeypatch, tmp_path)
    greeter = FakeUser(1)
    client = FakeClient([greeter])

    for i in range(config.WELCOME_DRY_STREAK_LIMIT):
        assert not W.welcome_needs_earning(1), "should still be paying up front"
        _dry_welcome(client, greeter, 100 + i, monkeypatch)

    assert W.welcome_needs_earning(1), "a full dry streak should tighten it"
    before = len(paid)
    late = FakeUser(500)
    W.register_new_member_join(late)
    _run(client, FakeMsg(greeter, "welcome", mentions=[late]))
    assert len(paid) == before, "now the greeting alone pays nothing"

    _run(client, FakeMsg(late, "oh hi", mentions=[greeter]))
    assert len(paid) == before, "the newcomer answering is not the greeter's doing"

    _run(client, FakeMsg(greeter, "hey, what brings you here", mentions=[late]))
    assert paid[-1] == (1, config.WELCOME_REWARD), "it pays once the greeter answers back"


def test_one_good_welcome_resets_the_streak(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    greeter = FakeUser(1)
    client = FakeClient([greeter])
    for i in range(config.WELCOME_DRY_STREAK_LIMIT - 1):
        _dry_welcome(client, greeter, 200 + i, monkeypatch)

    # A newcomer who actually replies breaks the run before it trips.
    _good_welcome(client, greeter, 300)

    _dry_welcome(client, greeter, 400, monkeypatch)
    assert not W.welcome_needs_earning(1)


def test_engaging_again_earns_instant_payment_back(monkeypatch, tmp_path):
    paid = _fresh(monkeypatch, tmp_path)
    greeter = FakeUser(1)
    client = FakeClient([greeter])
    for i in range(config.WELCOME_DRY_STREAK_LIMIT):
        _dry_welcome(client, greeter, 600 + i, monkeypatch)
    assert W.welcome_needs_earning(1)

    # Real conversations while tightened.
    for i in range(config.WELCOME_REDEMPTION_ENGAGEMENTS):
        _good_welcome(client, greeter, 700 + i)

    assert not W.welcome_needs_earning(1), "engagement should restore instant payment"
    before = len(paid)
    fresh = FakeUser(800)
    W.register_new_member_join(fresh)
    _run(client, FakeMsg(greeter, "welcome", mentions=[fresh]))
    assert len(paid) == before + 1, "back to paying on the greeting"


def test_one_persons_streak_does_not_affect_anyone_else(monkeypatch, tmp_path):
    paid = _fresh(monkeypatch, tmp_path)
    farmer, normal = FakeUser(1), FakeUser(9)
    client = FakeClient([farmer, normal])
    for i in range(config.WELCOME_DRY_STREAK_LIMIT):
        _dry_welcome(client, farmer, 900 + i, monkeypatch)
    assert W.welcome_needs_earning(1) and not W.welcome_needs_earning(9)

    n = FakeUser(950)
    W.register_new_member_join(n)
    before = len(paid)
    _run(client, FakeMsg(normal, "welcome", mentions=[n]))
    assert len(paid) == before + 1


def test_a_newcomer_who_never_speaks_is_nobodys_fault(monkeypatch, tmp_path):
    """Joining and going silent says nothing about the people who said hello, so it must
    not count towards anyone's dry streak."""
    _fresh(monkeypatch, tmp_path)
    greeter = FakeUser(1)
    client = FakeClient([greeter])

    for i in range(config.WELCOME_DRY_STREAK_LIMIT * 2):
        silent = FakeUser(1000 + i)
        W.register_new_member_join(silent)
        _run(client, FakeMsg(greeter, "welcome", mentions=[silent]))
        store = W.load_json_file(config.WELCOME_TRACKING_FILE)
        store[str(1000 + i)]["joined_at"] -= W._record_life_secs() + 60
        W.save_json_file(config.WELCOME_TRACKING_FILE, store)
        W._load_store()

    assert not W.welcome_needs_earning(1), "silent newcomers must not tighten anyone"


def test_a_newcomer_who_talks_to_everyone_but_you_is_not_your_fault(monkeypatch, tmp_path):
    """You cannot continue a conversation you were never included in.

    This used to count against the greeter, on the grounds that the newcomer was clearly
    reachable. It reads badly in practice: whether a new arrival picks you out of a busy
    channel is not something saying hello better would fix.
    """
    _fresh(monkeypatch, tmp_path)
    greeter, other = FakeUser(1), FakeUser(2)
    client = FakeClient([greeter, other])

    for i in range(config.WELCOME_DRY_STREAK_LIMIT * 2):
        n = FakeUser(2000 + i)
        W.register_new_member_join(n)
        _run(client, FakeMsg(greeter, "welcome", mentions=[n]))
        _run(client, FakeMsg(n, "hi everyone", mentions=[other]))   # spoke, just not back
        _expire(2000 + i)

    assert not W.welcome_needs_earning(1)


def test_answering_them_back_is_what_counts_as_a_real_welcome(monkeypatch, tmp_path):
    """The whole point of the change: greet, get answered, reply, and it is not dry."""
    _fresh(monkeypatch, tmp_path)
    greeter = FakeUser(1)
    client = FakeClient([greeter])
    for i in range(config.WELCOME_DRY_STREAK_LIMIT * 2):
        _good_welcome(client, greeter, 3000 + i)
    assert not W.welcome_needs_earning(1), "staying and talking must never tighten anyone"


def test_carrying_on_without_pressing_reply_still_counts(monkeypatch, tmp_path):
    """People answer in the same channel without a reply or a ping, and that is a
    conversation. Requiring the mention would mark the well-behaved as dry."""
    _fresh(monkeypatch, tmp_path)
    greeter = FakeUser(1)
    room = FakeChannel(4242)
    client = FakeClient([greeter])

    for i in range(config.WELCOME_DRY_STREAK_LIMIT):
        n = FakeUser(4000 + i)
        W.register_new_member_join(n)
        _run(client, FakeMsg(greeter, "welcome", mentions=[n], channel=room))
        _run(client, FakeMsg(n, "hello", mentions=[greeter], channel=room))
        _run(client, FakeMsg(greeter, "how's it going", channel=room))   # no reply, no ping
        _expire(4000 + i)

    assert not W.welcome_needs_earning(1)


def test_talking_in_a_different_channel_is_not_carrying_it_on(monkeypatch, tmp_path):
    """The loose same-room rule must not let any old message anywhere count."""
    _fresh(monkeypatch, tmp_path)
    greeter = FakeUser(1)
    client = FakeClient([greeter])

    for i in range(config.WELCOME_DRY_STREAK_LIMIT):
        n = FakeUser(5000 + i)
        W.register_new_member_join(n)
        _run(client, FakeMsg(greeter, "welcome", mentions=[n], channel=FakeChannel(11)))
        _run(client, FakeMsg(n, "hello", mentions=[greeter], channel=FakeChannel(11)))
        _run(client, FakeMsg(greeter, "unrelated chatter", channel=FakeChannel(22)))
        _expire(5000 + i)

    assert W.welcome_needs_earning(1), "chatting elsewhere is not answering them"
