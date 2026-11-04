"""User-installed app spam: spotting it, the two rules, and what gets done about it.

The replays follow the 16 Feb 2025 raid as far as it can be rebuilt (the messages were
deleted before the chronicle backfilled that day): one fresh account running "Spectre", an
app on its own account, which kept posting an invite link in #general for several minutes
after the account was kicked, because an app's follow-ups don't need the member.
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import discord
import pytest

import config
from lib.core import app_spam as A
from lib.core import mod_actions as M

SPECTRE = 1340000000000000001
GENERAL = 959493057076666380
T0 = datetime(2025, 2, 16, 16, 45, tzinfo=timezone.utc)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(A, "TRACKER", A.AppSpamTracker())
    monkeypatch.setattr(config, "APP_SPAM_ENABLED", True)
    monkeypatch.setattr(config, "APP_SPAM_REPORT_ONLY", False)
    yield


class Meta:
    def __init__(self, uid, user_install=True, guild_install=False):
        self.user = type("U", (), {"id": uid})()
        self._user, self._guild = user_install, guild_install

    def is_user_integration(self):
        return self._user

    def is_guild_integration(self):
        return self._guild


class Role:
    def __init__(self, rid):
        self.id = rid


class Member:
    def __init__(self, uid, staff=False):
        self.id = uid
        from lib.core.behaviour_watch import STAFF_ROLE_IDS
        self.roles = [Role(next(iter(STAFF_ROLE_IDS)))] if staff else []
        self.timeouts = []

    async def timeout(self, until, reason=None):
        self.timeouts.append((until, reason))


class Guild:
    id = config.GUILD_ID

    def __init__(self, members=()):
        self.members = {m.id: m for m in members}

    def get_member(self, uid):
        return self.members.get(uid)

    async def fetch_member(self, uid):
        raise discord.NotFound(type("R", (), {"status": 404, "reason": "x"})(), "gone")


class Channel:
    def __init__(self, cid=GENERAL):
        self.id = cid
        self.parent_id = None
        self.bulk = []

    async def delete_messages(self, messages):
        self.bulk.append(list(messages))
        for m in messages:
            m.deleted = True


class Msg:
    _next = 1

    def __init__(self, uid, at, app=SPECTRE, guild=None, channel=None, bot=True, meta=True,
                 content="discord.gg/someinvite FUCK FRANCE"):
        Msg._next += 1
        self.id = Msg._next
        self.author = type("Bot", (), {"id": app, "bot": bot, "name": "Spectre"})()
        self.application_id = app
        self.interaction_metadata = Meta(uid) if meta is True else meta
        self.guild = guild or Guild()
        self.channel = channel or Channel()
        self.created_at = T0 + timedelta(seconds=at)
        self.content = content
        self.embeds = []
        self.attachments = []
        self.deleted = False

    async def delete(self):
        self.deleted = True


def feed(msgs):
    out = []
    for m in msgs:
        hit = A.hit_from_message(m)
        out.append(A.TRACKER.observe(hit) if hit else ("ignored", None))
    return out


# --- spotting ----------------------------------------------------------------------------
def test_only_apps_on_the_users_own_account_count():
    assert A.hit_from_message(Msg(1, 0)) is not None
    assert A.hit_from_message(Msg(1, 0, meta=None)) is None                      # plain bot post
    assert A.hit_from_message(Msg(1, 0, meta=Meta(1, user_install=False))) is None  # server app
    assert A.hit_from_message(Msg(1, 0, meta=Meta(1, guild_install=True))) is None  # both
    assert A.hit_from_message(Msg(1, 0, bot=False)) is None


def test_allowlisted_apps_staff_and_oggers_are_skipped():
    for app in (1514711405960499291, 1211781489931452447, 1555609854108770355):
        assert app in config.APP_SPAM_ALLOWED_APP_IDS
        assert A.hit_from_message(Msg(1, 0, app=app)) is None
    staff = Member(5, staff=True)
    assert A.hit_from_message(Msg(5, 0, guild=Guild([staff]))) is None
    assert A.hit_from_message(Msg(config.USERS.OGGERS, 0)) is None


def test_allowlisted_channel_is_skipped(monkeypatch):
    monkeypatch.setattr(config, "APP_SPAM_ALLOWED_CHANNEL_IDS", {GENERAL})
    assert A.hit_from_message(Msg(1, 0)) is None


# --- the rules ---------------------------------------------------------------------------
def test_normal_use_never_trips():
    # The busiest real user-app use in 30 days of chronicle: 2 posts a minute.
    verdicts = feed([Msg(1, t) for t in range(0, 600, 30)])
    assert all(v == "seen" for v, _ in verdicts)


def test_feb_2025_raid_one_account_is_blocked_within_seconds():
    """One account, its app posting every second for seven minutes."""
    verdicts = feed([Msg(1, t) for t in range(0, 420)])
    first = [i for i, (v, _) in enumerate(verdicts) if v == "trigger"]
    assert first == [config.APP_SPAM_USER_COUNT - 1]          # once, on the 5th post (4s in)
    finding = verdicts[first[0]][1]
    assert finding.kind == "user" and finding.users == [1]
    assert len(finding.hits) == config.APP_SPAM_USER_COUNT
    # Every later follow-up is swept up, kick or no kick.
    assert all(v == "cleanup" for v, _ in verdicts[first[0] + 1:])


def test_cleanup_ends_after_the_interaction_token_would_have():
    feed([Msg(1, t) for t in range(5)])
    late = config.APP_SPAM_CLEANUP_SECONDS + 10
    assert feed([Msg(1, late)])[0][0] == "seen"


def test_many_accounts_each_posting_a_little_is_an_app_raid():
    """Four accounts, two posts each, a second apart: no one trips the per-person rule."""
    msgs = [Msg(uid, i * 4 + uid) for i in range(2) for uid in (1, 2, 3, 4)]
    msgs.sort(key=lambda m: m.created_at)
    verdicts = feed(msgs)
    triggers = [f for v, f in verdicts if v == "trigger"]
    assert len(triggers) == 1
    raid = triggers[0]
    assert raid.kind == "raid" and sorted(raid.users) == [1, 2, 3, 4]
    assert len(raid.hits) == 8
    # Their next posts are deleted, and a fifth account joining in is blocked at once.
    assert feed([Msg(2, 12)])[0][0] == "cleanup"
    v, late = feed([Msg(9, 13)])[0]
    assert v == "trigger" and late.kind == "late" and late.users == [9]


def test_two_people_on_different_apps_are_not_a_raid():
    msgs = [Msg(1 + (i % 2), i, app=SPECTRE + (i % 2)) for i in range(8)]
    assert all(v == "seen" for v, _ in feed(msgs))


def test_release_stops_the_cleanup():
    feed([Msg(1, t) for t in range(5)])
    A.release(1)
    assert feed([Msg(1, 6)])[0][0] == "seen"


# --- acting ------------------------------------------------------------------------------
class Police:
    def __init__(self):
        self.sent = []

    async def send(self, content=None, **kwargs):
        self.sent.append((content, kwargs))


class Client:
    def __init__(self, police):
        self.police = police

    def get_channel(self, cid):
        return self.police if cid == config.CHANNELS.POLICE_STATION else None


def _buttons(view):
    return [type(c) for c in view.children]


def test_act_deletes_times_out_and_reports_with_undo_first():
    member = Member(1)
    guild, channel = Guild([member]), Channel()
    msgs = [Msg(1, t, guild=guild, channel=channel) for t in range(5)]
    finding = [f for v, f in feed(msgs) if v == "trigger"][0]
    police = Police()
    _run(A.act(Client(police), finding, guild))
    assert all(m.deleted for m in msgs) and len(channel.bulk) == 1
    assert len(member.timeouts) == 1
    until, _ = member.timeouts[0]
    assert timedelta(minutes=9) < until - discord.utils.utcnow() <= timedelta(minutes=10)
    (_, kw), = police.sent
    assert kw["embed"].title == "App spam blocked"
    assert "<@1>" in kw["embed"].description and "Spectre" in kw["embed"].description
    assert _buttons(kw["view"]) == [M.ModUntimeoutButton, M.ModAnalyseButton,
                                    M.ModTimeoutButton, M.ModBanButton]
    assert all(c.custom_id.split(":")[2] == "appspam" for c in kw["view"].children)


def test_act_on_a_raid_cards_everyone():
    members = [Member(u) for u in (1, 2, 3, 4)]
    guild = Guild(members)
    msgs = sorted([Msg(u, i * 4 + u, guild=guild) for i in range(2) for u in (1, 2, 3, 4)],
                  key=lambda m: m.created_at)
    finding = [f for v, f in feed(msgs) if v == "trigger"][0]
    police = Police()
    _run(A.act(Client(police), finding, guild))
    assert all(m.deleted for m in msgs)
    assert all(len(m.timeouts) == 1 for m in members)
    assert len(police.sent) == 4
    assert all(kw["embed"].title == "App raid blocked" for _, kw in police.sent)


def test_report_only_touches_nothing(monkeypatch):
    monkeypatch.setattr(config, "APP_SPAM_REPORT_ONLY", True)
    member = Member(1)
    guild = Guild([member])
    msgs = [Msg(1, t, guild=guild) for t in range(5)]
    finding = [f for v, f in feed(msgs) if v == "trigger"][0]
    police = Police()
    _run(A.act(Client(police), finding, guild))
    assert not any(m.deleted for m in msgs) and not member.timeouts
    (_, kw), = police.sent
    assert kw["embed"].title == "App spam reported"
    assert M.ModUntimeoutButton not in _buttons(kw["view"])
    assert M.ModIgnoreButton in _buttons(kw["view"])


def test_hook_hands_back_the_message_only_when_it_is_being_deleted():
    async def go():
        results = [A.handle_app_message(None, Msg(1, t)) for t in range(7)]
        await asyncio.sleep(0)
        return results
    import unittest.mock as mock
    with mock.patch.object(A, "act", new=mock.AsyncMock()):
        results = _run(go())
    assert results == [False, False, False, False, True, True, True]


def test_disabled_does_nothing(monkeypatch):
    monkeypatch.setattr(config, "APP_SPAM_ENABLED", False)
    assert A.handle_app_message(None, Msg(1, 0)) is False


def test_lifting_the_timeout_on_the_card_also_releases_them():
    feed([Msg(1, t) for t in range(5)])
    assert 1 in A.TRACKER.blocked

    class I:
        user = Member(42, staff=True)
        user.mention = "<@42>"
        message = None
        guild = Guild([Member(1)])
        log = []

        class response:
            @staticmethod
            async def defer(**kw):
                pass

        class followup:
            @staticmethod
            async def send(*a, **kw):
                pass

    _run(M.ModUntimeoutButton("appspam", 1).callback(I))
    assert 1 not in A.TRACKER.blocked
