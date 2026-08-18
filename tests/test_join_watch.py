"""Join-watch behaviour: toggle persistence, eligibility, and the screening flow."""

import asyncio
import discord
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from commands.moderation import join_watch


def _fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(join_watch, "JOIN_WATCH_FILE", str(tmp_path / "join_watch.json"))
    monkeypatch.setattr(join_watch, "JOIN_WATCH_BUFFERS_FILE", str(tmp_path / "buffers.json"))
    monkeypatch.setattr(join_watch, "_state_cache", None)
    monkeypatch.setattr(join_watch, "_buffers_loaded", False)
    join_watch._buffers.clear()
    join_watch._locks.clear()


def _restart(monkeypatch):
    """Simulate a redeploy: memory goes, the file stays."""
    join_watch._buffers.clear()
    join_watch._locks.clear()
    monkeypatch.setattr(join_watch, "_buffers_loaded", False)


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

    async def send(self, *args, **kwargs):
        self.sent.append({"args": args, **kwargs})


class _FakeResponse:
    status = 403
    reason = "Forbidden"


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
        self.deleted = False

    async def delete(self):
        self.deleted = True


class FakeClient:
    def __init__(self):
        self.police = FakeChannel()
        self.usage_log = FakeChannel()

    def get_channel(self, channel_id):
        if channel_id == join_watch.CHANNELS.POLICE_STATION:
            return self.police
        return self.usage_log

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


def test_stale_default_incident_context_is_migrated(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    legacy = next(iter(join_watch._LEGACY_DEFAULT_CONTEXTS))
    join_watch.set_join_watch_state(True, legacy)

    # A fresh load (as after a deploy/restart) swaps the stale default for the
    # current general one; a custom staff-written context is left alone.
    monkeypatch.setattr(join_watch, "_state_cache", None)
    assert join_watch.get_join_watch_state()["context"] == join_watch.DEFAULT_CONTEXT

    join_watch.set_join_watch_state(True, "custom incident wording")
    monkeypatch.setattr(join_watch, "_state_cache", None)
    assert join_watch.get_join_watch_state()["context"] == "custom incident wording"


def test_eligibility_rules(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    assert join_watch._eligible(FakeMember()) is True

    bot = FakeMember()
    bot.bot = True
    assert join_watch._eligible(bot) is False

    assert join_watch._eligible(FakeMember(member_id=join_watch.USERS.OGGERS)) is False

    staff = FakeMember(roles=[FakeRole(next(iter(join_watch.STAFF_ROLE_IDS)))])
    assert join_watch._eligible(staff) is False


def test_only_members_who_join_while_armed_are_watched(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    join_watch.set_join_watch_state(True)
    client = FakeClient()
    evaluated = []

    async def fake_evaluate(_client, _member, messages):
        evaluated.append(len(messages))
        return {"verdict": "unsure", "confidence": 0.5, "reason": "thin"}, {"input": 1500, "output": 50, "model": "gpt-5.4-mini"}

    monkeypatch.setattr(join_watch, "_evaluate", fake_evaluate)

    # Joined before arming (never registered): messages are ignored, no backtracking.
    unregistered = FakeMember(member_id=1)
    asyncio.run(join_watch.maybe_watch_message(client, FakeMessage(unregistered, "hola")))
    assert evaluated == []

    # Joined while armed: registered and screened.
    registered = FakeMember(member_id=2)
    join_watch.register_join(registered)
    asyncio.run(join_watch.maybe_watch_message(client, FakeMessage(registered, "hola")))
    assert evaluated == [1]

    # Joins while disarmed are not registered.
    join_watch.set_join_watch_state(False)
    late = FakeMember(member_id=3)
    join_watch.register_join(late)
    assert late.id not in join_watch._buffers


def test_undeletable_trigger_still_times_out_and_says_so(monkeypatch, tmp_path):
    """A message we cannot remove must not cost us the timeout or the report card."""
    _fresh(monkeypatch, tmp_path)
    join_watch.set_join_watch_state(True)
    member = FakeMember()
    client = FakeClient()

    async def fake_evaluate(_client, _member, _messages):
        return ({"verdict": "troll", "confidence": 0.95, "reason": "Raid spam."},
                {"input": 10, "output": 5, "model": "gpt-5.4-mini"})

    monkeypatch.setattr(join_watch, "_evaluate", fake_evaluate)
    join_watch.register_join(member)
    trigger = FakeMessage(member, "raid link here")

    async def forbidden():
        raise discord.Forbidden(_FakeResponse(), "no perms")

    trigger.delete = forbidden
    asyncio.run(join_watch.maybe_watch_message(client, trigger))

    # The timeout still lands and the card is still posted.
    assert len(member.timeouts) == 1
    assert len(client.police.sent) == 1
    import json as _json
    payload = _json.dumps(client.police.sent[0]["view"].to_components())
    # The failure is surfaced, not swallowed, and the jump link survives so staff
    # can go and remove it by hand.
    assert "not deleted" in payload
    assert "deleted by join-watch" not in payload
    assert "raid link here" in payload


def test_confident_troll_verdict_times_out_and_reports(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    join_watch.set_join_watch_state(True)
    member = FakeMember()
    client = FakeClient()

    async def fake_evaluate(_client, _member, _messages):
        return ({"verdict": "troll", "confidence": 0.92, "reason": "Joined to spread hate."},
                {"input": 2000, "output": 100, "model": "gpt-5.4-mini"})

    monkeypatch.setattr(join_watch, "_evaluate", fake_evaluate)
    join_watch.register_join(member)
    trigger = FakeMessage(member, "england scum etc")
    asyncio.run(join_watch.maybe_watch_message(client, trigger))

    assert len(member.timeouts) == 1
    assert "Join-watch" in member.timeouts[0][1]
    # The message that tripped the verdict is removed, not just timed out for.
    assert trigger.deleted is True
    assert len(client.police.sent) == 1
    assert client.police.sent[0]["view"] is not None
    import json as _json
    # The card carries the message content and the untimeout button. The text is
    # snapshotted before deletion, so staff can still judge the call from the card.
    payload = _json.dumps(client.police.sent[0]["view"].to_components())
    assert "england scum etc" in payload
    assert f"joinwatch:untimeout:{member.id}" in payload
    # The deletion is stated on the card rather than left for staff to infer.
    assert "deleted by join-watch" in payload
    assert "was deleted" in payload
    # Every scan is audited in the bot usage log as a card with outcome, link,
    # quote and estimated AI cost.
    assert len(client.usage_log.sent) == 1
    log_line = _json.dumps(client.usage_log.sent[0]["view"].to_components())
    scanned = f"1/{join_watch.MAX_SCANNED_MESSAGES}"
    assert scanned in log_line and "troll" in log_line and "timed out" in log_line
    assert trigger.jump_url in log_line
    assert "> england scum etc" in log_line
    assert "deleted" not in log_line
    # 2,000 in x $0.75/M + 100 out x $4.50/M = $0.00195, rounded in the footer.
    assert "est. cost $0.0020" in log_line and "2,000 in / 100 out tokens" in log_line
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
        return {"verdict": "unsure", "confidence": 0.4, "reason": "Not enough yet."}, {"input": 1500, "output": 50, "model": "gpt-5.4-mini"}

    monkeypatch.setattr(join_watch, "_evaluate", fake_evaluate)
    join_watch.register_join(member)
    for i in range(join_watch.MAX_SCANNED_MESSAGES + 2):
        asyncio.run(join_watch.maybe_watch_message(client, FakeMessage(member, f"msg {i}")))

    assert calls == list(range(1, join_watch.MAX_SCANNED_MESSAGES + 1))
    assert member.timeouts == []
    assert client.police.sent == []
    # One audit card per scan; the final one records that the scan finished.
    import json as _json
    assert len(client.usage_log.sent) == join_watch.MAX_SCANNED_MESSAGES
    assert "scan complete - no action" in _json.dumps(client.usage_log.sent[-1]["view"].to_components())
    assert "still watching" in _json.dumps(client.usage_log.sent[0]["view"].to_components())


def test_fine_verdicts_keep_scanning_to_the_cap(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    join_watch.set_join_watch_state(True)
    member = FakeMember()
    client = FakeClient()
    calls = []

    async def fake_evaluate(_client, _member, messages):
        calls.append(len(messages))
        return {"verdict": "fine", "confidence": 0.9, "reason": "Normal newcomer."}, {"input": 1500, "output": 50, "model": "gpt-5.4-mini"}

    monkeypatch.setattr(join_watch, "_evaluate", fake_evaluate)
    join_watch.register_join(member)
    for i in range(join_watch.MAX_SCANNED_MESSAGES + 2):
        asyncio.run(join_watch.maybe_watch_message(client, FakeMessage(member, f"msg {i}")))

    # A confident "fine" no longer stops the watch; all messages up to the cap are scanned.
    assert calls == list(range(1, join_watch.MAX_SCANNED_MESSAGES + 1))
    assert member.timeouts == []


def test_evaluation_error_never_times_anyone_out(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    join_watch.set_join_watch_state(True)
    member = FakeMember()
    client = FakeClient()

    async def fake_evaluate(_client, _member, _messages):
        return None, None

    monkeypatch.setattr(join_watch, "_evaluate", fake_evaluate)
    join_watch.register_join(member)
    for i in range(join_watch.MAX_SCANNED_MESSAGES):
        asyncio.run(join_watch.maybe_watch_message(client, FakeMessage(member, f"msg {i}")))
    assert member.timeouts == []
    assert client.police.sent == []


def test_cost_footer_prices_known_models():
    assert join_watch._cost_footer(None) is None
    assert join_watch._cost_footer({"input": 0, "output": 0}) is None
    footer = join_watch._cost_footer({"input": 1_000_000, "output": 1_000_000, "model": "gpt-5.4-mini"})
    assert footer == "est. cost $5.2500 · gpt-5.4-mini · 1,000,000 in / 1,000,000 out tokens"
    # Unknown models still report token counts, just without a price.
    footer = join_watch._cost_footer({"input": 100, "output": 10, "model": "mystery-model"})
    assert "est. cost" not in footer and "100 in / 10 out tokens" in footer


def test_evaluate_falls_back_to_gemini_when_openai_fails(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    join_watch.set_join_watch_state(True)
    member = FakeMember()

    async def no_images(_client, _member):
        return []

    async def openai_down(_static, _images, _dynamic, cache_key=None):
        return None, "request failed: boom", None

    async def gemini_ok(_static, _images, _dynamic):
        return ('{"verdict": "fine", "confidence": 0.8, "reason": "ok"}', None,
                {"input": 1200, "output": 40, "model": "gemini-3.5-flash"})

    monkeypatch.setattr(join_watch, "_profile_images", no_images)
    monkeypatch.setattr(join_watch, "_call_openai_json", openai_down)
    monkeypatch.setattr(join_watch, "_call_gemini_json", gemini_ok)

    verdict, usage = asyncio.run(join_watch._evaluate(FakeClient(), member, [
        {"channel": "#general", "content": "hi"},
    ]))
    assert verdict == {"verdict": "fine", "confidence": 0.8, "reason": "ok"}
    assert usage["model"] == "gemini-3.5-flash"


def test_verdict_parsing_tolerates_wrapping():
    assert join_watch._parse_verdict(None) is None
    assert join_watch._parse_verdict("not json at all") is None
    parsed = join_watch._parse_verdict('```json\n{"verdict": "troll", "confidence": 0.8}\n```')
    assert parsed == {"verdict": "troll", "confidence": 0.8}
    parsed = join_watch._parse_verdict('noise {"verdict": "fine", "confidence": 1} noise')
    assert parsed["verdict"] == "fine"


def test_system_join_message_is_not_screened(monkeypatch, tmp_path):
    import discord

    _fresh(monkeypatch, tmp_path)
    join_watch.set_join_watch_state(True)
    member = FakeMember()
    client = FakeClient()
    evaluated = []

    async def fake_evaluate(_client, _member, messages):
        evaluated.append(len(messages))
        return {"verdict": "unsure", "confidence": 0.5, "reason": "thin"}, {"input": 1500, "output": 50, "model": "gpt-5.4-mini"}

    monkeypatch.setattr(join_watch, "_evaluate", fake_evaluate)
    join_watch.register_join(member)

    # The "X joined the server" system message is authored by the member but
    # must be neither screened nor counted.
    system_message = FakeMessage(member, "")
    system_message.type = discord.MessageType.new_member
    asyncio.run(join_watch.maybe_watch_message(client, system_message))
    assert evaluated == []
    assert join_watch._buffers[member.id]["messages"] == []

    real = FakeMessage(member, "hello")
    real.type = discord.MessageType.default
    asyncio.run(join_watch.maybe_watch_message(client, real))
    assert evaluated == [1]


def test_toggle_announcement_names_the_mod(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    join_watch.set_join_watch_state(True, "semi final trolls")
    client = FakeClient()
    actor = FakeMember(member_id=42)

    asyncio.run(join_watch.announce_toggle(client, actor, True))
    asyncio.run(join_watch.announce_toggle(client, actor, False))

    assert len(client.police.sent) == 2
    import json as _json
    armed_text = _json.dumps(client.police.sent[0]["view"].to_components())
    disarmed_text = _json.dumps(client.police.sent[1]["view"].to_components())
    assert actor.mention in armed_text and "armed" in armed_text
    assert "semi final trolls" in armed_text
    assert actor.mention in disarmed_text and "disarmed" in disarmed_text


def test_disarming_clears_scan_progress(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    join_watch.set_join_watch_state(True)
    join_watch._buffers[1] = {"messages": [{"content": "x"}], "done": False}
    join_watch.set_join_watch_state(False)
    assert join_watch._buffers == {}


# ---------------------------------------------------------------------------
# The watch list must survive a restart, or a deploy mid-incident stops screening
# exactly the members it was armed for.
# ---------------------------------------------------------------------------
def test_a_member_mid_scan_is_still_watched_after_a_restart(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    join_watch.set_join_watch_state(True)
    member = FakeMember()
    client = FakeClient()
    seen = []

    async def fake_evaluate(_client, _member, messages):
        seen.append(len(messages))
        return ({"verdict": "unsure", "confidence": 0.2, "reason": "thin"},
                {"input": 1, "output": 1, "model": "gpt-5.4-mini"})

    monkeypatch.setattr(join_watch, "_evaluate", fake_evaluate)
    join_watch.register_join(member)
    asyncio.run(join_watch.maybe_watch_message(client, FakeMessage(member, "hello")))
    assert seen == [1]

    _restart(monkeypatch)
    assert member.id not in join_watch._buffers, "memory should be empty before the reload"

    # The next message rehydrates the list and continues the scan where it left off.
    asyncio.run(join_watch.maybe_watch_message(client, FakeMessage(member, "second")))
    assert seen == [1, 2], "the scan must resume, not restart"


def test_a_finished_scan_is_not_restored(monkeypatch, tmp_path):
    """Someone already actioned or scanned out must not come back on a redeploy."""
    _fresh(monkeypatch, tmp_path)
    join_watch.set_join_watch_state(True)
    member = FakeMember()
    client = FakeClient()

    async def fake_evaluate(_client, _member, _messages):
        return ({"verdict": "troll", "confidence": 0.99, "reason": "raid"},
                {"input": 1, "output": 1, "model": "gpt-5.4-mini"})

    monkeypatch.setattr(join_watch, "_evaluate", fake_evaluate)
    join_watch.register_join(member)
    asyncio.run(join_watch.maybe_watch_message(client, FakeMessage(member, "raid spam")))
    assert join_watch._buffers[member.id]["done"] is True

    _restart(monkeypatch)
    join_watch._load_buffers()
    assert member.id not in join_watch._buffers


def test_a_stale_watch_expires_rather_than_persisting_forever(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    join_watch.set_join_watch_state(True)
    member = FakeMember()
    join_watch.register_join(member)

    # Age the registration past the cut-off and reload.
    stored = join_watch.load_json_file(join_watch.JOIN_WATCH_BUFFERS_FILE)
    stored[str(member.id)]["registered_at"] -= (join_watch.JOIN_WATCH_MAX_WATCH_HOURS + 1) * 3600
    join_watch.save_json_file(join_watch.JOIN_WATCH_BUFFERS_FILE, stored)

    _restart(monkeypatch)
    join_watch._load_buffers()
    assert member.id not in join_watch._buffers


def test_disarming_clears_the_stored_list_too(monkeypatch, tmp_path):
    """Arming never backtracks, so a stored list must not outlive a disarm."""
    _fresh(monkeypatch, tmp_path)
    join_watch.set_join_watch_state(True)
    join_watch.register_join(FakeMember())
    assert join_watch.load_json_file(join_watch.JOIN_WATCH_BUFFERS_FILE)

    join_watch.set_join_watch_state(False)
    assert not join_watch.load_json_file(join_watch.JOIN_WATCH_BUFFERS_FILE)
