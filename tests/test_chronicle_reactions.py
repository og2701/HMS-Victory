"""Reaction backfill: what one page of history turns into, and that it never doubles up
on reactions the live hook already recorded."""

import asyncio
import importlib.util
import os
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

import discord

from lib.chronicle import recorder
from lib.chronicle.db import ChronicleDB

_spec = importlib.util.spec_from_file_location(
    "chronicle_reactions_backfill",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "scripts", "chronicle_reactions_backfill.py"))
rb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rb)

WHEN = datetime(2024, 3, 1, 12, 0, tzinfo=timezone.utc)
CHANNEL = SimpleNamespace(id=10, name="general", parent_id=None)


class FakeReaction:
    def __init__(self, emoji, normal=(), burst=()):
        self.emoji = emoji
        self._normal, self._burst = list(normal), list(burst)
        self.normal_count, self.burst_count = len(self._normal), len(self._burst)
        self.count = self.normal_count + self.burst_count
        self.calls = []

    def users(self, limit=None, type=None):
        self.calls.append(type)
        ids = self._burst if type == discord.ReactionType.burst else self._normal

        async def gen():
            for uid in ids:
                yield SimpleNamespace(id=uid)
        return gen()


def _msg(mid, author=100, reactions=(), mtype="default"):
    return SimpleNamespace(
        id=mid, guild=SimpleNamespace(id=1), channel=CHANNEL,
        author=SimpleNamespace(id=author, bot=False), content="hi", created_at=WHEN,
        edited_at=None, reference=None, attachments=[], stickers=[], embeds=[],
        raw_mentions=[], raw_role_mentions=[], raw_channel_mentions=[],
        mention_everyone=False, flags=SimpleNamespace(value=0), poll=None,
        type=SimpleNamespace(name=mtype), reactions=list(reactions))


class ReactionBackfillTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        ChronicleDB.set_path(self.path)
        ChronicleDB.get_connection()

    def tearDown(self):
        ChronicleDB.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self.path + suffix)
            except OSError:
                pass

    def page(self, messages):
        rows, n = asyncio.run(rb._process_page(CHANNEL, messages, asyncio.Semaphore(4)))
        rb._write(rows)
        return n

    def test_users_are_recorded_with_the_message_time_and_author(self):
        fire = FakeReaction("🔥", normal=[200, 300])
        m = _msg(1, author=100, reactions=[fire])
        recorder._write(recorder.message_rows(m), stamp=False)
        self.assertEqual(self.page([m]), 2)
        rows = ChronicleDB.fetch_all(
            "SELECT user_id, author_id, emoji, burst, source, ts FROM reactions ORDER BY user_id")
        ts = int(WHEN.timestamp())
        self.assertEqual(rows, [(200, 100, "🔥", 0, "backfill", ts), (300, 100, "🔥", 0, "backfill", ts)])

    def test_discord_totals_are_kept_even_without_users(self):
        custom = FakeReaction(SimpleNamespace(name="kek", id=55, animated=False), normal=[200])
        self.page([_msg(2, reactions=[custom])])
        self.assertEqual(ChronicleDB.fetch_one(
            "SELECT emoji_key, emoji, emoji_id, count FROM reaction_counts WHERE message_id = 2"),
            ("55", "kek", 55, 1))

    def test_super_reactions_are_fetched_separately_and_flagged(self):
        both = FakeReaction("⭐", normal=[200], burst=[300])
        self.page([_msg(3, reactions=[both])])
        self.assertEqual(both.calls, [discord.ReactionType.normal, discord.ReactionType.burst])
        self.assertEqual(dict(ChronicleDB.fetch_all("SELECT user_id, burst FROM reactions")),
                         {200: 0, 300: 1})

    def test_reactions_the_live_hook_already_has_are_skipped(self):
        m = _msg(4, reactions=[FakeReaction("🔥", normal=[200, 300])])
        recorder._write([(recorder.REACTION_SQL, (4, 10, 1, 200, None, "🔥", None, 0, "add", 999, 0))],
                        stamp=False)
        self.page([m])
        self.assertEqual(ChronicleDB.fetch_all(
            "SELECT user_id, source FROM reactions ORDER BY user_id"), [(200, "live"), (300, "backfill")])
        # and the live row gains the author it was missing
        self.assertEqual(ChronicleDB.fetch_one(
            "SELECT author_id FROM reactions WHERE source = 'live'")[0], 100)

    def test_rerunning_a_page_adds_nothing(self):
        m = _msg(5, reactions=[FakeReaction("🔥", normal=[200])])
        self.page([m])
        self.assertEqual(self.page([m]), 0)
        self.assertEqual(ChronicleDB.fetch_one("SELECT COUNT(*) FROM reactions")[0], 1)

    def test_message_type_filled_in_and_missing_messages_inserted(self):
        stored = _msg(6, mtype="reply")
        stored_rows = recorder.message_rows(stored)
        # simulate a row from before msg_type existed
        recorder._write(stored_rows, stamp=False)
        ChronicleDB.execute("UPDATE messages SET msg_type = NULL WHERE message_id = 6")
        missing = _msg(7, mtype="pins_add")
        self.page([stored, missing])
        self.assertEqual(dict(ChronicleDB.fetch_all("SELECT message_id, msg_type FROM messages")),
                         {6: "reply", 7: "pins_add"})

    def test_script_writes_never_move_the_bots_last_alive(self):
        self.page([_msg(8, reactions=[FakeReaction("🔥", normal=[200])])])
        self.assertIsNone(ChronicleDB.fetch_one(
            "SELECT value FROM meta WHERE key = ?", (recorder.LAST_ALIVE_KEY,)))


class LiveReactionTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        ChronicleDB.set_path(self.path)
        ChronicleDB.get_connection()

    def tearDown(self):
        ChronicleDB.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self.path + suffix)
            except OSError:
                pass

    def test_live_add_takes_author_and_super_flag_from_the_event(self):
        payload = SimpleNamespace(
            message_id=9, channel_id=10, guild_id=1, user_id=200, message_author_id=100,
            cached_message=None, burst=True,
            emoji=SimpleNamespace(name="🔥", id=None, animated=False))
        rows = []
        original = recorder.enqueue
        recorder.enqueue = lambda sql, params: rows.append((sql, params))
        try:
            recorder.record_raw_reaction(payload, "add")
        finally:
            recorder.enqueue = original
        recorder._write(rows)
        self.assertEqual(ChronicleDB.fetch_one(
            "SELECT author_id, burst, source FROM reactions"), (100, 1, "live"))


if __name__ == "__main__":
    unittest.main()
