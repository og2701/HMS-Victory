"""Chronicle: recording, edits/deletes, voice pairing and the wrapped aggregates."""

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytz

from lib.chronicle import recorder
from lib.chronicle.db import ChronicleDB
from lib.chronicle import queries

UK = pytz.timezone("Europe/London")


def _msg(mid, uid, content="hello", ts=None, channel_id=10, bot=False,
         mentions=(), role_mentions=(), everyone=False, reply=None,
         attachments=(), embeds=0, flags=0, poll=None):
    created = ts or datetime(2026, 6, 1, 12, 30, tzinfo=timezone.utc)
    reference = None
    if reply:
        reply_id, reply_uid = reply
        reference = SimpleNamespace(message_id=reply_id,
                                    resolved=SimpleNamespace(author=SimpleNamespace(id=reply_uid)))
    return SimpleNamespace(
        id=mid, guild=SimpleNamespace(id=1),
        channel=SimpleNamespace(id=channel_id, parent_id=None),
        author=SimpleNamespace(id=uid, bot=bot),
        content=content, created_at=created, edited_at=None,
        reference=reference,
        attachments=[SimpleNamespace(url=u, filename="f.png", size=1, content_type="image/png")
                     for u in attachments],
        stickers=[], embeds=[None] * embeds,
        raw_mentions=list(mentions), raw_role_mentions=list(role_mentions),
        raw_channel_mentions=[], mention_everyone=everyone,
        flags=SimpleNamespace(value=flags), poll=poll,
    )


def _vstate(channel_id=None, self_mute=False, self_deaf=False, mute=False,
            deaf=False, stream=False, video=False):
    return SimpleNamespace(
        channel=SimpleNamespace(id=channel_id) if channel_id else None,
        self_mute=self_mute, self_deaf=self_deaf, mute=mute, deaf=deaf,
        self_stream=stream, self_video=video)


class ChronicleTestCase(unittest.TestCase):
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

    def write(self, rows):
        """Same collapse-and-commit path the background writer uses."""
        recorder._write(rows)


class TestMessageRows(ChronicleTestCase):
    def test_message_row_carries_counts_and_local_buckets(self):
        msg = _msg(1, 100, "hello there world",
                   ts=datetime(2026, 6, 1, 23, 30, tzinfo=timezone.utc))
        self.write(recorder.message_rows(msg))
        row = ChronicleDB.fetch_one(
            "SELECT user_id, char_count, word_count, local_day, local_hour, is_bot, source "
            "FROM messages WHERE message_id = 1")
        # 23:30 UTC on 1 June is 00:30 on 2 June in London - the bucket must follow the
        # clock people were looking at, not UTC.
        self.assertEqual(row, (100, 17, 3, "2026-06-02", 0, 0, "live"))

    def test_bots_are_stored_and_flagged(self):
        self.write(recorder.message_rows(_msg(2, 999, "beep", bot=True)))
        self.assertEqual(
            ChronicleDB.fetch_one("SELECT is_bot FROM messages WHERE message_id = 2")[0], 1)

    def test_mentions_replies_and_everyone(self):
        msg = _msg(3, 100, "oi <@200>", mentions=[200], role_mentions=[7],
                   everyone=True, reply=(1, 300))
        self.write(recorder.message_rows(msg))
        kinds = dict(ChronicleDB.fetch_all(
            "SELECT kind, target_id FROM mentions WHERE message_id = 3"))
        self.assertEqual(kinds["user"], 200)
        self.assertEqual(kinds["role"], 7)
        self.assertIsNone(kinds["everyone"])
        self.assertEqual(kinds["reply"], 300)
        self.assertEqual(
            ChronicleDB.fetch_one("SELECT reply_to, reply_to_user FROM messages WHERE message_id = 3"),
            (1, 300))

    def test_custom_and_unicode_emoji_are_counted(self):
        self.write(recorder.message_rows(
            _msg(4, 100, "lol <:kek:123> <:kek:123> 😂 <a:spin:456>")))
        rows = dict(((e, i), n) for e, i, n in ChronicleDB.fetch_all(
            "SELECT emoji, emoji_id, n FROM emoji_uses WHERE message_id = 4"))
        self.assertEqual(rows[("kek", 123)], 2)
        self.assertEqual(rows[("spin", 456)], 1)
        self.assertEqual(rows[("😂", None)], 1)

    def test_dms_are_not_recorded(self):
        msg = _msg(5, 100)
        msg.guild = None
        self.assertEqual(recorder.message_rows(msg), [])

    def test_reinsert_is_idempotent(self):
        rows = recorder.message_rows(_msg(6, 100))
        self.write(rows)
        self.write(rows[:1])          # message row again, as a re-run backfill would
        self.assertEqual(
            ChronicleDB.fetch_one("SELECT COUNT(*) FROM messages WHERE message_id = 6")[0], 1)


class TestAuditAndPolls(ChronicleTestCase):
    def test_audit_entry_keeps_actor_target_and_changes(self):
        entry = SimpleNamespace(
            id=555, guild=SimpleNamespace(id=1),
            action=SimpleNamespace(name="member_role_update"),
            user=SimpleNamespace(id=100),
            target=SimpleNamespace(id=200, name="victim"),
            reason="spam",
            changes=[SimpleNamespace(attribute="roles",
                                     before=[SimpleNamespace(id=7, name="member")],
                                     after=[SimpleNamespace(id=8, name="muted")])],
            extra=None,
            created_at=datetime(2026, 6, 1, 12, tzinfo=timezone.utc))
        self.write([recorder.audit_row(entry)])
        row = ChronicleDB.fetch_one(
            "SELECT action, user_id, target_id, target_type, reason, changes FROM audit_log")
        self.assertEqual(row[:5], ("member_role_update", 100, 200, "SimpleNamespace", "spam"))
        self.assertIn('"name": "muted"'.replace(" ", ""), row[5].replace(" ", ""))

    def test_audit_entries_dedupe_on_rerun(self):
        entry = SimpleNamespace(
            id=556, guild=SimpleNamespace(id=1), action=SimpleNamespace(name="kick"),
            user=SimpleNamespace(id=100), target=None, reason=None, changes=[], extra=None,
            created_at=datetime(2026, 6, 1, 12, tzinfo=timezone.utc))
        self.write([recorder.audit_row(entry)])
        self.write([recorder.audit_row(entry)])
        self.assertEqual(ChronicleDB.fetch_one("SELECT COUNT(*) FROM audit_log")[0], 1)

    def test_poll_is_stored_with_its_answers(self):
        poll = SimpleNamespace(
            question="beans?", multiple=False, expires_at=None,
            answers=[SimpleNamespace(id=1, text="yes", emoji=None),
                     SimpleNamespace(id=2, text="no", emoji=None)])
        self.write(recorder.message_rows(_msg(40, 100, "", poll=poll)))
        self.assertEqual(
            ChronicleDB.fetch_one("SELECT question, multiple FROM polls WHERE message_id = 40"),
            ("beans?", 0))

    def test_poll_votes_record_both_directions(self):
        payload = SimpleNamespace(message_id=40, channel_id=10, user_id=100, answer_id=2)
        rows = []
        with unittest_patch(recorder, "enqueue", lambda sql, params: rows.append((sql, params))):
            recorder.record_poll_vote(payload, "add")
            recorder.record_poll_vote(payload, "remove")
        self.write(rows)
        self.assertEqual([r[0] for r in ChronicleDB.fetch_all(
            "SELECT action FROM poll_votes ORDER BY id")], ["add", "remove"])

    def test_message_flags_are_kept(self):
        # MessageFlags.voice - the only thing separating a voice note from any other
        # audio attachment.
        self.write(recorder.message_rows(_msg(41, 100, "", flags=8192)))
        self.assertEqual(
            ChronicleDB.fetch_one("SELECT flags FROM messages WHERE message_id = 41")[0], 8192)


class TestEditsAndDeletes(ChronicleTestCase):
    def test_edit_updates_content_and_logs_the_old_text(self):
        self.write(recorder.message_rows(_msg(10, 100, "before")))
        before = _msg(10, 100, "before")
        after = _msg(10, 100, "after now longer")
        after.edited_at = datetime(2026, 6, 1, 13, 0, tzinfo=timezone.utc)
        rows = []
        with unittest_patch(recorder, "enqueue_many", rows.extend):
            recorder.record_edit(before, after)
        self.write(rows)
        self.assertEqual(
            ChronicleDB.fetch_one("SELECT content, word_count FROM messages WHERE message_id = 10"),
            ("after now longer", 3))
        self.assertEqual(
            ChronicleDB.fetch_one("SELECT old_content, new_content FROM message_edits")[0], "before")

    def test_embed_resolution_edit_is_ignored(self):
        rows = []
        with unittest_patch(recorder, "enqueue_many", rows.extend):
            recorder.record_edit(_msg(11, 100, "same"), _msg(11, 100, "same"))
        self.assertEqual(rows, [])

    def test_delete_marks_in_place_and_keeps_the_content(self):
        self.write(recorder.message_rows(_msg(12, 100, "gone")))
        rows = []
        with unittest_patch(recorder, "enqueue", lambda sql, params: rows.append((sql, params))):
            recorder.record_delete([12])
        self.write(rows)
        content, deleted = ChronicleDB.fetch_one(
            "SELECT content, deleted_ts FROM messages WHERE message_id = 12")
        self.assertEqual(content, "gone")
        self.assertIsNotNone(deleted)


class TestMemberUpdates(ChronicleTestCase):
    def _member(self, nick=None, roles=(), boost=None, timeout=None):
        return SimpleNamespace(
            id=100, guild=SimpleNamespace(id=1), nick=nick, global_name="owen",
            roles=[SimpleNamespace(id=r, name=f"role{r}") for r in roles],
            premium_since=boost, timed_out_until=timeout)

    def _capture(self, before, after):
        rows = []
        with unittest_patch(recorder, "enqueue", lambda sql, params: rows.append((sql, params))):
            recorder.record_member_update(before, after)
        self.write(rows)
        return [r[0] for r in ChronicleDB.fetch_all("SELECT action FROM member_events ORDER BY id")]

    def test_nick_and_role_changes(self):
        actions = self._capture(self._member("old", roles=(1, 2)),
                                self._member("new", roles=(2, 3)))
        self.assertEqual(set(actions), {"nick", "role_add", "role_remove"})

    def test_boost_is_recorded(self):
        started = datetime(2026, 6, 1, tzinfo=timezone.utc)
        self.assertEqual(self._capture(self._member(), self._member(boost=started)), ["boost"])


class TestVoice(ChronicleTestCase):
    def test_join_move_leave_produce_one_row_each(self):
        member = SimpleNamespace(id=100, guild=SimpleNamespace(id=1))
        rows = []
        with unittest_patch(recorder, "enqueue_many", rows.extend):
            recorder.record_voice(member, _vstate(None), _vstate(50))
            recorder.record_voice(member, _vstate(50), _vstate(51))
            recorder.record_voice(member, _vstate(51), _vstate(None))
        self.write(rows)
        self.assertEqual([r[0] for r in ChronicleDB.fetch_all(
            "SELECT action FROM voice_events ORDER BY id")], ["join", "move", "leave"])

    def test_mute_and_stream_flips_are_separate_rows(self):
        member = SimpleNamespace(id=100, guild=SimpleNamespace(id=1))
        rows = []
        with unittest_patch(recorder, "enqueue_many", rows.extend):
            recorder.record_voice(member, _vstate(50), _vstate(50, self_mute=True, stream=True))
        self.write(rows)
        self.assertEqual({r[0] for r in ChronicleDB.fetch_all("SELECT action FROM voice_events")},
                         {"mute", "stream_start"})

    def test_voice_seconds_pairs_sessions(self):
        base = int(datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc).timestamp())
        for action, offset in (("join", 0), ("leave", 3600), ("join", 7200), ("leave", 9000)):
            ChronicleDB.execute(
                "INSERT INTO voice_events (user_id, guild_id, channel_id, action, ts) "
                "VALUES (?,?,?,?,?)", (100, 1, 50, action, base + offset))
        self.assertEqual(queries.voice_seconds(base - 10, base + 20000, 100), 3600 + 1800)

    def test_session_still_open_is_closed_at_the_window_end(self):
        base = int(datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc).timestamp())
        ChronicleDB.execute(
            "INSERT INTO voice_events (user_id, guild_id, channel_id, action, ts) "
            "VALUES (?,?,?,?,?)", (100, 1, 50, "join", base))
        self.assertEqual(queries.voice_seconds(base - 10, base + 600, 100), 600)


class TestWrapped(ChronicleTestCase):
    def setUp(self):
        super().setUp()
        start = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)
        rows = []
        for i in range(10):                      # user 100, ten days running
            rows += recorder.message_rows(
                _msg(1000 + i, 100, "hello 😂", ts=start + timedelta(days=i)))
        for i in range(3):                       # user 200, same channel
            rows += recorder.message_rows(
                _msg(2000 + i, 200, "hi", ts=start + timedelta(days=i), reply=(1000, 100)))
        rows += recorder.message_rows(_msg(3000, 999, "beep", ts=start, bot=True))
        rows += recorder.message_rows(
            _msg(4000, 100, "old", ts=datetime(2025, 3, 1, 12, 0, tzinfo=timezone.utc)))
        self.write(rows)
        ChronicleDB.execute(
            "INSERT INTO reactions (message_id, channel_id, user_id, author_id, emoji, "
            "action, ts) VALUES (?,?,?,?,?,?,?)",
            (1000, 10, 200, 100, "🔥", "add", int(start.timestamp())))

    def test_server_totals_exclude_bots_and_other_years(self):
        out = queries.server_wrapped(2026)
        self.assertEqual(out["messages"], 13)
        self.assertEqual(out["posters"], 2)
        self.assertEqual(out["reactions"], 1)
        self.assertEqual(out["top_posters"][0], (100, 10))
        self.assertEqual(out["top_channels"][0], (10, 13))

    def test_server_totals_can_include_bots(self):
        self.assertEqual(queries.server_wrapped(2026, include_bots=True)["messages"], 14)

    def test_user_wrapped_rank_streak_and_relationships(self):
        out = queries.user_wrapped(100, 2026)
        self.assertEqual(out["messages"], 10)
        self.assertEqual(out["rank"], 1)
        self.assertEqual(out["active_days"], 10)
        self.assertEqual(out["longest_streak"], 10)
        self.assertEqual(out["reactions_received"], 1)
        self.assertEqual(out["replied_by"][0], (200, 3))
        self.assertEqual(out["top_emoji"][0][0], "😂")

    def test_user_wrapped_reply_targets(self):
        out = queries.user_wrapped(200, 2026)
        self.assertEqual(out["replies_to"][0], (100, 3))
        self.assertEqual(out["rank"], 2)

    def test_coverage_reports_what_is_stored(self):
        out = queries.coverage()
        self.assertEqual(out["messages"], 15)
        self.assertEqual(out["reactions"], 1)
        self.assertGreater(out["size_bytes"], 0)


class TestWriterBatching(ChronicleTestCase):
    def test_batch_preserves_order_across_statement_changes(self):
        """An UPDATE queued after an INSERT must still land after it once the writer
        collapses runs of identical SQL into executemany calls."""
        rows = recorder.message_rows(_msg(20, 100, "first"))
        rows.append(("UPDATE messages SET content = ? WHERE message_id = ?", ("second", 20)))
        rows += recorder.message_rows(_msg(21, 100, "third"))
        self.write(rows)
        self.assertEqual(
            ChronicleDB.fetch_one("SELECT content FROM messages WHERE message_id = 20")[0], "second")
        self.assertEqual(
            ChronicleDB.fetch_one("SELECT COUNT(*) FROM messages")[0], 2)

    def test_queue_round_trip(self):
        recorder.enqueue_many(recorder.message_rows(_msg(30, 100, "queued")))
        self.assertTrue(recorder.flush(timeout=10))
        self.assertEqual(
            ChronicleDB.fetch_one("SELECT content FROM messages WHERE message_id = 30")[0], "queued")


class _Patch:
    """Tiny stand-in for mock.patch.object, kept local so the tests read as plain python."""

    def __init__(self, obj, name, value):
        self.obj, self.name, self.value = obj, name, value

    def __enter__(self):
        self.original = getattr(self.obj, self.name)
        setattr(self.obj, self.name, self.value)
        return self.value

    def __exit__(self, *exc):
        setattr(self.obj, self.name, self.original)
        return False


def unittest_patch(obj, name, value):
    return _Patch(obj, name, value)


if __name__ == "__main__":
    unittest.main()
