"""The unusual-DM sweep: paging, expiry, and not reporting the same account twice.

The whole thing reads a field discord.py does not model, so the tests feed it raw member
JSON in the shape the API actually returns - captured from a live sweep of the guild.
"""
import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import discord

import config
from lib.features import dm_spam_watch as W


class _Resp:
    status = 404
    reason = "Not Found"

NOW = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)


def member(uid, until=None, joined="2026-08-18T10:00:00+00:00", name=None):
    """A member object with the keys the API really sends, unusual_dm_activity_until and all."""
    return {
        "user": {"id": str(uid), "username": name or f"user{uid}"},
        "joined_at": joined, "roles": ["1", "2"], "nick": None, "pending": False,
        "unusual_dm_activity_until": until,
    }


class FakeHTTP:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    async def get_members(self, guild_id, limit, after):
        self.calls.append({"limit": limit, "after": after})
        return self.pages.pop(0) if self.pages else []


class FakeChannel:
    def __init__(self):
        self.sent = []

    async def send(self, **kwargs):
        self.sent.append(kwargs.get("embed"))


class FakeClient:
    def __init__(self, pages):
        self.http = FakeHTTP(pages)
        self.channel = FakeChannel()

    def get_channel(self, _id):
        return self.channel


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _temp_state():
    path = tempfile.mktemp(suffix=".json")
    W.STATE_FILE = path
    return path


def test_only_live_flags_count():
    """A member flagged last March still carries a non-null stamp. 151 of the 152 in the
    guild are like that, so treating non-null as flagged would report the lot."""
    past = (NOW - timedelta(days=30)).isoformat()
    future = (NOW + timedelta(hours=6)).isoformat()
    c = FakeClient([[member(1, past), member(2, future), member(3, None)]])
    found = _run(W.fetch_flagged_members(c, now=NOW))
    assert list(found) == ["2"], f"expected only the live flag: {found}"


def test_it_pages_through_the_whole_guild():
    """The API caps at 1000 per request and the guild is over 12k, so a single page would
    silently only ever look at the oldest members."""
    future = (NOW + timedelta(hours=6)).isoformat()
    page1 = [member(i) for i in range(1, W.PAGE)] + [member(W.PAGE, future)]
    page2 = [member(W.PAGE + 1, future)]
    c = FakeClient([page1, page2])
    found = _run(W.fetch_flagged_members(c, now=NOW))
    assert len(found) == 2, f"the second page was not read: {found}"
    assert c.http.calls[1]["after"] == W.PAGE, \
        f"paging cursor did not advance: {c.http.calls}"


def test_a_new_flag_is_reported_once_and_not_again():
    path = _temp_state()
    try:
        future = (NOW + timedelta(hours=6)).isoformat()
        c = FakeClient([[member(7, future, name="scamacct")]])
        new = _run(W.sweep(c, now=NOW))
        assert list(new) == ["7"] and len(c.channel.sent) == 1

        c2 = FakeClient([[member(7, future, name="scamacct")]])
        again = _run(W.sweep(c2, now=NOW))
        assert again == {}, "the same flag was reported twice"
        assert c2.channel.sent == []
    finally:
        os.path.exists(path) and os.remove(path)


def test_being_caught_a_second_time_is_reported_again():
    """State keyed on the expiry, not just the id: an account flagged again next month is
    news, and would otherwise be remembered as old."""
    path = _temp_state()
    try:
        first = (NOW + timedelta(hours=6)).isoformat()
        c = FakeClient([[member(7, first)]])
        _run(W.sweep(c, now=NOW))

        later = NOW + timedelta(days=40)
        second = (later + timedelta(hours=6)).isoformat()
        c2 = FakeClient([[member(7, second)]])
        new = _run(W.sweep(c2, now=later))
        assert list(new) == ["7"], "a fresh flag on a known account went unreported"
    finally:
        os.path.exists(path) and os.remove(path)


def test_an_expired_flag_is_forgotten_rather_than_kept():
    path = _temp_state()
    try:
        future = (NOW + timedelta(hours=6)).isoformat()
        _run(W.sweep(FakeClient([[member(7, future)]]), now=NOW))
        later = NOW + timedelta(days=2)
        _run(W.sweep(FakeClient([[member(7, future)]]), now=later))
        assert json.load(open(path)) == {}, "the expired flag stayed in state forever"
    finally:
        os.path.exists(path) and os.remove(path)


def test_a_first_run_with_a_backlog_seeds_quietly():
    """Turning this on should not fire off a wall of alerts about history."""
    path = _temp_state()
    try:
        future = (NOW + timedelta(hours=6)).isoformat()
        many = [member(i, future) for i in range(1, W.FIRST_RUN_ALERT_LIMIT + 5)]
        c = FakeClient([many])
        _run(W.sweep(c, now=NOW))
        assert c.channel.sent == [], "the backlog was announced"
        assert len(json.load(open(path))) == len(many), "the backlog was not remembered either"
    finally:
        os.path.exists(path) and os.remove(path)


def test_a_small_first_run_is_still_reported():
    path = _temp_state()
    try:
        future = (NOW + timedelta(hours=6)).isoformat()
        c = FakeClient([[member(1, future), member(2, future)]])
        _run(W.sweep(c, now=NOW))
        assert len(c.channel.sent) == 2, "a couple of live flags should still be reported"
    finally:
        os.path.exists(path) and os.remove(path)


def test_the_alert_carries_what_decides_the_call():
    future = (NOW + timedelta(hours=6)).isoformat()
    e = W._embed("352462925065224193",
                 {"name": "scamacct", "until": future,
                  "joined": "2026-08-18T10:00:00+00:00", "roles": 3}, now=NOW)
    body = e.description + "".join(f"{f.name} {f.value}" for f in e.fields)
    assert "scamacct" in body and "352462925065224193" in body
    assert "Flag lifts" in [f.name for f in e.fields]
    assert "Account made" in [f.name for f in e.fields], "account age is half the judgement"


# --- the staff buttons -------------------------------------------------------------------

class FakeMessage:
    def __init__(self, embed):
        self.embeds = [embed]
        self.edits = []

    async def edit(self, **kwargs):
        self.edits.append(kwargs)


class FakeResponse:
    def __init__(self, log):
        self.log = log

    async def send_message(self, content=None, **kwargs):
        self.log.append(("replied", content))

    async def defer(self, **kwargs):
        self.log.append(("deferred", None))


class FakeUser:
    def __init__(self, staff):
        self.id = 42
        self.mention = "<@42>"
        from lib.core.behaviour_watch import STAFF_ROLE_IDS
        self.roles = [type("R", (), {"id": r})()
                      for r in (list(STAFF_ROLE_IDS)[:1] if staff else [1])]

    def __str__(self):
        return "mod#0001"


class FakeMember:
    def __init__(self, uid, dms_open=True):
        self.id = uid
        self.dms_open = dms_open
        self.dms = []

    async def send(self, **kwargs):
        if not self.dms_open:
            raise RuntimeError("Cannot send messages to this user")
        self.dms.append(kwargs)


class FakeGuild:
    def __init__(self, member=None):
        self.bans = []
        self.member = member

    async def ban(self, obj, **kwargs):
        self.bans.append((obj.id, kwargs.get("reason")))

    def get_member(self, _uid):
        return self.member

    async def fetch_member(self, _uid):
        if self.member is None:
            raise discord.NotFound(_Resp(), "unknown member")
        return self.member


class FakeButtonInteraction:
    def __init__(self, staff=True, embed=None, member=None):
        self.log = []
        self.user = FakeUser(staff)
        self.guild = FakeGuild(member)
        self.response = FakeResponse(self.log)
        self.message = FakeMessage(embed or W._embed("7", {"until": None, "joined": None}))
        self.followup = type("F", (), {
            "send": lambda _s, content=None, **kw: _noop(self.log, content)})()


async def _noop(log, content):
    log.append(("followup", content))


def test_every_button_matches_only_its_own_template():
    """Four DynamicItems sharing a prefix - one sloppy pattern and Ban answers Ignore."""
    buttons = [W.DMFlagBanButton(7), W.DMFlagTimeoutButton(7),
               W.DMFlagAnalyseButton(7), W.DMFlagDismissButton(7)]
    for b in buttons:
        cid = b.item.custom_id
        pat = lambda o: o.__class__.__discord_ui_compiled_template__
        matched = [o.__class__.__name__ for o in buttons if pat(o).match(cid)]
        assert matched == [b.__class__.__name__], f"{cid} also matched {matched}"
        assert pat(b).match(cid)["uid"] == "7", cid


def test_the_alert_carries_all_four_actions():
    view = W.action_view(7)
    labels = [c.item.label for c in view.children]
    assert len(labels) == 4, labels
    assert any("Ban" in l for l in labels) and any("Time out" in l for l in labels)
    assert "Analyse" in labels and "Ignore" in labels


def test_a_non_staff_click_bans_nobody():
    i = FakeButtonInteraction(staff=False)
    _run(W.DMFlagBanButton(7).callback(i))
    assert i.guild.bans == [], "a non-staff click went through"
    assert i.log and i.log[0] == ("replied", "Staff only.")


def test_banning_goes_by_id_so_it_works_after_they_leave():
    """get_member returns None here, as it would for someone already gone. A ban keyed on
    the member object would quietly fail exactly when it matters."""
    i = FakeButtonInteraction(staff=True)
    _run(W.DMFlagBanButton(7).callback(i))
    assert i.guild.bans and i.guild.bans[0][0] == 7, i.guild.bans
    assert "unusual DM" in i.guild.bans[0][1]


def test_acting_greys_the_buttons_out_rather_than_removing_them():
    """A report that loses its buttons reads as though it was never actionable, and you can
    no longer see what the other options were."""
    i = FakeButtonInteraction(staff=True)
    _run(W.DMFlagBanButton(7).callback(i))
    edit = i.message.edits[-1]
    view = edit["view"]
    assert view is not None and len(view.children) == 4, "the buttons were taken away"
    assert all(c.item.disabled for c in view.children), "the buttons are still clickable"
    handled = [f for f in edit["embed"].fields if f.name == "Handled"]
    assert handled and "<@42>" in handled[0].value, "the alert does not say who acted"


def test_a_fresh_alert_has_live_buttons():
    assert not any(c.item.disabled for c in W.action_view(7).children)


def test_the_appeal_dm_goes_out_before_the_ban_not_after():
    """Once the ban lands we no longer share a guild and Discord drops the DM, so an appeal
    notice sent afterwards reaches nobody at all."""
    m = FakeMember(7)
    i = FakeButtonInteraction(staff=True, member=m)
    _run(W.DMFlagBanButton(7).callback(i))
    assert m.dms, "they were banned without ever being told why"
    assert i.guild.bans, "the DM went out but the ban did not"


def test_the_appeal_dm_explains_the_real_reason():
    """Reusing the cluster appeal wholesale would tell a hijacked account it joined in a
    batch, which is not true and not something they can answer."""
    body = W.BAN_DM_TEXT.lower()
    assert "direct messages" in body, W.BAN_DM_TEXT
    assert "hacked" in body and "two-factor" in body, "no route back for a hijacked account"
    assert "registered within a few minutes" not in body, "that is the cluster reason"


def test_closed_dms_do_not_stop_the_ban():
    m = FakeMember(7, dms_open=False)
    i = FakeButtonInteraction(staff=True, member=m)
    _run(W.DMFlagBanButton(7).callback(i))
    assert i.guild.bans, "a closed DM blocked the ban"
    handled = [f for f in i.message.edits[-1]["embed"].fields if f.name == "Handled"][0]
    assert "could not DM" in handled.value, "the report does not say the notice failed"


def _run_all():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL  {name}: {e}")
        except Exception as e:
            import traceback
            print(f"ERROR {name}: {e!r}")
            traceback.print_exc()
    print(f"\n{passed}/{len(tests)} passed")
    return passed == len(tests)


if __name__ == "__main__":
    sys.exit(0 if _run_all() else 1)
