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

import config
from lib.features import dm_spam_watch as W

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
