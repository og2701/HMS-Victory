"""Chronicle catch-up: what gets fetched after a restart, and that nothing doubles up."""

import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from lib.chronicle import catchup, recorder
from lib.chronicle.db import ChronicleDB

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)


def _snowflake(dt):
    return int((dt.timestamp() * 1000 - 1420070400000)) << 22


def _msg(mid, channel, uid=100, content="hi"):
    created = datetime.fromtimestamp(((mid >> 22) + 1420070400000) / 1000, timezone.utc)
    return SimpleNamespace(
        id=mid, guild=SimpleNamespace(id=1), channel=channel,
        author=SimpleNamespace(id=uid, bot=False), content=content,
        created_at=created, edited_at=None, reference=None, attachments=[],
        stickers=[], embeds=[], raw_mentions=[], raw_role_mentions=[],
        raw_channel_mentions=[], mention_everyone=False,
        flags=SimpleNamespace(value=0), poll=None)


class FakeChannel:
    """Serves a fixed message list through history(), honouring after/before the way
    Discord does, and records every call so the tests can see what was asked for."""

    def __init__(self, cid, messages=(), last_message_id=None, created_at=None, name="c"):
        self.id, self.name = cid, name
        self.parent_id = None
        self.messages = sorted(messages, key=lambda m: m.id)
        for m in self.messages:
            m.channel = self
        self.last_message_id = last_message_id or (self.messages[-1].id if self.messages else None)
        self.created_at = created_at or NOW - timedelta(days=400)
        self.calls = []

    def history(self, limit=None, after=None, before=None, oldest_first=True):
        self.calls.append({"after": getattr(after, "id", None), "before": before})
        lo = getattr(after, "id", None)
        hi = _snowflake(before) if isinstance(before, datetime) else None
        picked = [m for m in self.messages
                  if (lo is None or m.id > lo) and (hi is None or m.id < hi)]

        async def gen():
            for m in picked:
                yield m
        return gen()


class CatchupTestCase(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        ChronicleDB.set_path(self.path)
        ChronicleDB.get_connection()
        # Never the real lock path: on the bot's own box a live backfill holds it, and
        # catch_up would (correctly) wait for it forever.
        self._real_lock = catchup.BACKFILL_LOCK
        catchup.BACKFILL_LOCK = self.path + ".no-backfill-lock"

    def tearDown(self):
        catchup.BACKFILL_LOCK = self._real_lock
        ChronicleDB.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self.path + suffix)
            except OSError:
                pass

    def seed(self, channel, messages):
        recorder._write([row for m in messages for row in recorder.message_rows(m)])


class TestPlan(CatchupTestCase):
    def test_only_channels_with_something_newer_are_fetched(self):
        quiet = FakeChannel(1, last_message_id=500)
        busy = FakeChannel(2, last_message_id=900)
        todo = catchup.plan([quiet, busy], {1: 500, 2: 700}, now=NOW)
        self.assertEqual([(c.id, after) for c, after in todo], [(2, 700)])

    def test_channel_with_no_messages_ever_costs_nothing(self):
        self.assertEqual(catchup.plan([FakeChannel(3, last_message_id=None)], {}, now=NOW), [])

    def test_new_empty_channel_is_walked_in_full(self):
        fresh = FakeChannel(4, last_message_id=50, created_at=NOW - timedelta(hours=2))
        self.assertEqual([(c.id, a) for c, a in catchup.plan([fresh], {}, now=NOW)], [(4, None)])

    def test_old_channel_we_hold_nothing_for_is_left_to_the_backfill(self):
        old = FakeChannel(5, last_message_id=50, created_at=NOW - timedelta(days=300))
        self.assertEqual(catchup.plan([old], {}, now=NOW), [])


class TestCatchUp(CatchupTestCase):
    def _client(self, channels, audit=()):
        async def audit_logs(limit=None, after=None, oldest_first=True):
            for entry in audit:
                if after is None or entry.id > after.id:
                    yield entry

        guild = SimpleNamespace(id=1, channels=channels, threads=[], audit_logs=audit_logs)
        return SimpleNamespace(get_guild=lambda gid: guild)

    def _run(self, client, boundary, gap_start=None):
        import discord
        # FakeChannel is not a discord channel type, so widen the candidate filter.
        original = catchup._candidates
        catchup._candidates = lambda guild: list(guild.channels)
        try:
            asyncio.run(catchup.catch_up(client, 1, gap_start, boundary, "restart"))
        finally:
            catchup._candidates = original

    def test_fills_the_gap_and_stops_at_the_boundary(self):
        t0 = NOW - timedelta(hours=3)
        before_gap = _msg(_snowflake(t0), None, content="already stored")
        in_gap = [_msg(_snowflake(t0 + timedelta(minutes=m)), None, content=f"gap {m}")
                  for m in (10, 20, 30)]
        boundary = t0 + timedelta(minutes=45)
        after_boundary = _msg(_snowflake(boundary + timedelta(minutes=1)), None,
                              content="heard live")
        channel = FakeChannel(10, [before_gap, *in_gap, after_boundary])
        self.seed(channel, [before_gap])

        self._run(self._client([channel]), boundary, gap_start=int(t0.timestamp()))

        self.assertEqual(channel.calls[0]["after"], before_gap.id)
        rows = ChronicleDB.fetch_all("SELECT content, source FROM messages ORDER BY message_id")
        self.assertEqual(rows, [("already stored", "live"), ("gap 10", "catchup"),
                                ("gap 20", "catchup"), ("gap 30", "catchup")])
        outage = ChronicleDB.fetch_one(
            "SELECT reason, channels_fetched, messages_recovered FROM outages")
        self.assertEqual(outage, ("restart", 1, 3))

    def test_messages_already_heard_live_are_not_duplicated(self):
        """A message stored live inside the fetched window must not grow a second set
        of mention/emoji rows."""
        t0 = NOW - timedelta(hours=3)
        first = _msg(_snowflake(t0), None)
        overlap = _msg(_snowflake(t0 + timedelta(minutes=5)), None, content="<:kek:1> both")
        channel = FakeChannel(11, [first, overlap])
        self.seed(channel, [first])
        # Stored live already, but newer than what plan() treats as the channel's newest.
        recorder._write(recorder.message_rows(overlap))
        ChronicleDB.execute("UPDATE messages SET ts = ts - 10000 WHERE message_id = ?",
                            (overlap.id,))

        self._run(self._client([channel]), t0 + timedelta(hours=1))

        self.assertEqual(ChronicleDB.fetch_one(
            "SELECT COUNT(*) FROM emoji_uses WHERE message_id = ?", (overlap.id,))[0], 1)

    def test_audit_entries_newer_than_the_newest_stored_are_added(self):
        def entry(eid):
            return SimpleNamespace(
                id=eid, guild=SimpleNamespace(id=1), action=SimpleNamespace(name="kick"),
                user=SimpleNamespace(id=1), target=None, reason=None,
                changes=SimpleNamespace(before=None, after=None), extra=None,
                created_at=NOW)
        recorder._write([recorder.audit_row(entry(100))])
        self._run(self._client([], audit=[entry(100), entry(101), entry(102)]), NOW)
        self.assertEqual(ChronicleDB.fetch_one("SELECT COUNT(*) FROM audit_log")[0], 3)
        self.assertEqual(ChronicleDB.fetch_one("SELECT audit_recovered FROM outages")[0], 2)

    def test_waits_for_a_running_backfill(self):
        """Two processes on one token is the 429 storm the backfill lock exists to stop."""
        slept = []

        async def fake_sleep(seconds):
            slept.append(seconds)
            os.remove(catchup.BACKFILL_LOCK)

        original_lock, original_sleep = catchup.BACKFILL_LOCK, catchup.asyncio.sleep
        fd, catchup.BACKFILL_LOCK = tempfile.mkstemp()
        os.write(fd, str(os.getpid()).encode())     # held by a live process: this one
        os.close(fd)
        catchup.asyncio.sleep = fake_sleep
        try:
            self._run(self._client([]), NOW)
        finally:
            catchup.BACKFILL_LOCK, catchup.asyncio.sleep = original_lock, original_sleep
        self.assertEqual(slept, [60])


class TestStaleLock(CatchupTestCase):
    def test_lock_left_by_a_dead_process_does_not_block(self):
        """A killed backfill leaves its lock behind; the catch-up must not wait on it."""
        import subprocess, sys
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        with open(catchup.BACKFILL_LOCK, "w") as f:
            f.write(str(dead.pid))
        slept = []

        async def fake_sleep(seconds):
            slept.append(seconds)

        original_sleep = catchup.asyncio.sleep
        catchup.asyncio.sleep = fake_sleep
        try:
            client = SimpleNamespace(get_guild=lambda gid: SimpleNamespace(
                id=1, channels=[], threads=[], audit_logs=lambda **k: _empty()))
            asyncio.run(catchup.catch_up(client, 1, None, NOW, "restart"))
        finally:
            catchup.asyncio.sleep = original_sleep
        self.assertEqual(slept, [])

    def test_live_holder_is_reported(self):
        from lib.chronicle.walk import lock_holder
        with open(catchup.BACKFILL_LOCK, "w") as f:
            f.write(str(os.getpid()))
        self.assertEqual(lock_holder(catchup.BACKFILL_LOCK), os.getpid())


async def _empty():
    return
    yield


class TestLastAlive(CatchupTestCase):
    def test_every_write_stamps_last_alive(self):
        channel = FakeChannel(12)
        self.seed(channel, [_msg(_snowflake(NOW), channel)])
        self.assertIsNotNone(catchup.read_last_alive())

    def test_missing_stamp_reads_as_none(self):
        self.assertIsNone(catchup.read_last_alive())


if __name__ == "__main__":
    unittest.main()
