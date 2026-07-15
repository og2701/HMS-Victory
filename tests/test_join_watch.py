"""Join-watch behaviour: toggle persistence, eligibility, and the screening flow."""

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from commands.moderation import join_watch


def _fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(join_watch, "JOIN_WATCH_FILE", str(tmp_path / "join_watch.json"))
    monkeypatch.setattr(join_watch, "_state_cache", None)
    join_watch._buffers.clear()
    join_watch._locks.clear()


class FakeRole:
    def __init__(self, role_id):
        self.id = role_id


class FakeAvatar:
    url = "https://cdn.example/avatar.png"

    def replace(self, **_):
        return self


class FakeMember:
    def __init__(self, member_id=1234, joined_minutes_ago=10, account_days_old=2, roles=()):
        now = datetime.now(timezone.utc)
        self.id = member_id
        self.bot = False
        self.name = "el_trollo"
        self.display_name = "El Trollo"
        self.global_name = "El Trollo"
        self.mention = f"<@{member_id}>"
        self.joined_at = now - timedelta(minutes=joined_minutes_ago)
        self.created_at = now - timedelta(days=account_days_old)
        self.roles = list(roles)
        self.display_avatar = FakeAvatar()
        self.timeouts = []

    async def timeout(self, until, reason=None):
        self.timeouts.append((until, reason))


class FakeChannel:
    name = "general"

    def __init__(self):
        self.sent = []

    async def send(self, **kwargs):
        self.sent.append(kwargs)


class FakeMessage:
    def __init__(self, member, content, channel=None):
        self.author = member
        self.guild = object()
        self.content = content
        self.channel = channel or FakeChannel()
        self.attachments = []
        self.jump_url = "https://discord.com/channels/1/2/3"
        self.created_at = datetime.now(timezone.utc)
        self.id = 999


class FakeClient:
    def __init__(self):
        self.police = FakeChannel()

    def get_channel(self, channel_id):
        return self.police

    async def fetch_user(self, user_id):
        raise RuntimeError("no network in tests")


def test_toggle_state_persists_and_context_is_kept(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    assert join_watch.join_watch_enabled() is False

    state = join_watch.set_join_watch_state(True, "custom incident context")
    assert state["enabled"] is True
    assert state["context"] == "custom incident context"

    # A reload from disk (fresh cache) sees the same state.
    monkeypatch.setattr(join_watch, "_state_cache", None)
    reloaded = join_watch.get_join_watch_state()
    assert reloaded["enabled"] is True
    assert reloaded["context"] == "custom incident context"

    # Disabling without a context keeps the stored context for next time.
    state = join_watch.set_join_watch_state(False)
    assert state["enabled"] is False
    assert state["context"] == "custom incident context"


def test_eligibility_rules(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    assert join_watch._eligible(FakeMember()) is True

    bot = FakeMember()
    bot.bot = True
    assert join_watch._eligible(bot) is False

    assert join_watch._eligible(FakeMember(member_id=join_watch.USERS.OGGERS)) is False

    old_member = FakeMember(joined_minutes_ago=(join_watch.MAX_MEMBER_AGE_HOURS + 1) * 60)
    assert join_watch._eligible(old_member) is False

    staff = FakeMember(roles=[FakeRole(next(iter(join_watch.STAFF_ROLE_IDS)))])
    assert join_watch._eligible(staff) is False


def test_confident_troll_verdict_times_out_and_reports(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    join_watch.set_join_watch_state(True)
    member = FakeMember()
    client = FakeClient()

    async def fake_evaluate(_client, _member, _messages):
        return {"verdict": "troll", "confidence": 0.92, "reason": "Joined to spread hate."}

    monkeypatch.setattr(join_watch, "_evaluate", fake_evaluate)
    asyncio.run(join_watch.maybe_watch_message(client, FakeMessage(member, "england scum etc")))

    assert len(member.timeouts) == 1
    assert "Join-watch" in member.timeouts[0][1]
    assert len(client.police.sent) == 1
    assert client.police.sent[0]["view"] is not None
    # Actioned members are never screened again.
    asyncio.run(join_watch.maybe_watch_message(client, FakeMessage(member, "another message")))
    assert len(member.timeouts) == 1


def test_unsure_verdicts_stop_after_message_cap_without_action(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    join_watch.set_join_watch_state(True)
    member = FakeMember()
    client = FakeClient()
    calls = []

    async def fake_evaluate(_client, _member, messages):
        calls.append(len(messages))
        return {"verdict": "unsure", "confidence": 0.4, "reason": "Not enough yet."}

    monkeypatch.setattr(join_watch, "_evaluate", fake_evaluate)
    for i in range(join_watch.MAX_SCANNED_MESSAGES + 2):
        asyncio.run(join_watch.maybe_watch_message(client, FakeMessage(member, f"msg {i}")))

    assert calls == list(range(1, join_watch.MAX_SCANNED_MESSAGES + 1))
    assert member.timeouts == []
    assert client.police.sent == []


def test_confident_fine_verdict_clears_early(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    join_watch.set_join_watch_state(True)
    member = FakeMember()
    client = FakeClient()
    calls = []

    async def fake_evaluate(_client, _member, messages):
        calls.append(len(messages))
        return {"verdict": "fine", "confidence": 0.9, "reason": "Normal newcomer."}

    monkeypatch.setattr(join_watch, "_evaluate", fake_evaluate)
    asyncio.run(join_watch.maybe_watch_message(client, FakeMessage(member, "hello everyone")))
    asyncio.run(join_watch.maybe_watch_message(client, FakeMessage(member, "how do i get roles")))

    assert calls == [1]
    assert member.timeouts == []


def test_evaluation_error_never_times_anyone_out(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    join_watch.set_join_watch_state(True)
    member = FakeMember()
    client = FakeClient()

    async def fake_evaluate(_client, _member, _messages):
        return None

    monkeypatch.setattr(join_watch, "_evaluate", fake_evaluate)
    for i in range(join_watch.MAX_SCANNED_MESSAGES):
        asyncio.run(join_watch.maybe_watch_message(client, FakeMessage(member, f"msg {i}")))
    assert member.timeouts == []
    assert client.police.sent == []


def test_verdict_parsing_tolerates_wrapping():
    assert join_watch._parse_verdict(None) is None
    assert join_watch._parse_verdict("not json at all") is None
    parsed = join_watch._parse_verdict('```json\n{"verdict": "troll", "confidence": 0.8}\n```')
    assert parsed == {"verdict": "troll", "confidence": 0.8}
    parsed = join_watch._parse_verdict('noise {"verdict": "fine", "confidence": 1} noise')
    assert parsed["verdict"] == "fine"


def test_disarming_clears_scan_progress(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    join_watch.set_join_watch_state(True)
    join_watch._buffers[1] = {"messages": [{"content": "x"}], "done": False}
    join_watch.set_join_watch_state(False)
    assert join_watch._buffers == {}
