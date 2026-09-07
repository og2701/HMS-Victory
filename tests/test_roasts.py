"""Offline evidence, image, memory and delivery tests; no Discord/OpenAI calls."""
import asyncio
import base64
import importlib.util
import json
import sqlite3
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import database
from lib.features import member_context as C
from lib.features import roasts as R

NOW = datetime(2026, 9, 7, tzinfo=timezone.utc)
TARGET = types.SimpleNamespace(id=1, display_name="Alice", bot=False, mention="<@1>")
OTHER = types.SimpleNamespace(id=2, display_name="Bob", bot=False)
REQUESTER = types.SimpleNamespace(id=3, display_name="Charlie", bot=False)


def message(mid, text="I am unbeatable at chess", *, author=TARGET, reply=None, resolved=None,
            attachments=(), channel_id=10, created_at=None):
    return types.SimpleNamespace(
        id=mid, content=text, author=author, attachments=list(attachments), reactions=[],
        created_at=created_at or NOW - timedelta(minutes=mid),
        channel=types.SimpleNamespace(id=channel_id),
        reference=types.SimpleNamespace(message_id=reply, resolved=resolved) if reply else None,
    )


class Channel:
    id = 10

    def __init__(self, messages=()):
        self.messages = messages
        self.send = AsyncMock()
        self.history_calls = []
        self.yielded = 0

    async def history(self, **kwargs):
        self.history_calls.append(kwargs)
        for msg in self.messages[:kwargs["limit"]]:
            self.yielded += 1
            yield msg


def attachment(aid=1, *, data=None, content_type="image/png", size=100):
    if data is None:
        from PIL import Image
        out = BytesIO()
        Image.new("RGB", (32, 24), "red").save(out, format="PNG")
        data = out.getvalue()
    return types.SimpleNamespace(
        id=aid, filename="screenshot.png", content_type=content_type, size=size,
        read=AsyncMock(return_value=data),
    )


def evidence():
    return {
        "target": {"user_id": "1", "display_name": "Alice"},
        "target_messages": [{"message_id": "10", "author_id": "1", "text": "I never lose"}],
        "other_member_replies": [{
            "message_id": "11", "author_id": "2", "target_message_id": "10", "text": "You lost again",
        }],
    }


def drafts(*, stray=False, selected=0):
    candidates = [{
        "angle": f"Chess boast versus result {n}",
        "evidence_message_ids": ["10"],
        "text": f"You declared yourself unbeatable, then spent the whole match explaining why the board was wrong. That's some fucking confidence for someone whose strongest move was opening the excuses tab. Round {n}.",
        "stray_user_id": None,
    } for n in range(3)]
    if stray:
        candidates[selected].update(stray_user_id="2", evidence_message_ids=["10", "11"])
    return {"candidates": candidates, "selected_index": selected}


def memory_db(test):
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    test.addCleanup(connection.close)
    restore = patch.object(database.DatabaseManager, "_connection", connection)
    restore.start()
    test.addCleanup(restore.stop)
    database.init_db()
    return connection


class EvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_filters_filler_and_duplicates_but_keeps_short_claims_and_repetition_count(self):
        channel = Channel([
            message(1, "I never lose"), message(2, "lol 😂"), message(3, "I never lose"),
            message(4, "I won"), message(5, "https://example.com"), message(6, "/roast"),
            message(7, "ancient boast", created_at=NOW - timedelta(days=31)),
        ])
        result, images = await C.collect_evidence(channel, TARGET, now=NOW)
        self.assertEqual([row["message_id"] for row in result["target_messages"]], ["4", "1"])
        self.assertEqual(result["target_messages"][-1]["occurrences"], 2)
        self.assertEqual(images, [])
        self.assertEqual(channel.history_calls[0]["limit"], 4000)
        self.assertFalse(channel.history_calls[0]["oldest_first"])

    async def test_reply_authorship_and_resolved_scanned_incoming_context(self):
        parent = message(20, "I beat you yesterday", author=OTHER)
        channel = Channel([
            message(1, "Not again", author=OTHER, reply=2),
            message(2, "I never lose", reply=20, resolved=parent),
            message(3, "unrelated chat", author=OTHER),
        ])
        result, _ = await C.collect_evidence(channel, TARGET, now=NOW)
        self.assertEqual(len(result["target_messages"]), 1)
        self.assertEqual({row["message_id"] for row in result["other_member_replies"]}, {"1", "20"})
        self.assertTrue(all(row["author_id"] == "2" for row in result["other_member_replies"]))
        self.assertTrue(all(row["target_message_id"] == "2" for row in result["other_member_replies"]))
        channel = Channel([message(2, reply=20), parent])
        result, _ = await C.collect_evidence(channel, TARGET, now=NOW)
        self.assertEqual(result["other_member_replies"][0]["message_id"], "20")

    async def test_no_private_old_or_bot_reply_material(self):
        parents = [
            message(20, author=OTHER, channel_id=999),
            message(21, author=OTHER, created_at=NOW - timedelta(days=31)),
            message(22, author=types.SimpleNamespace(id=9, display_name="Bot", bot=True)),
        ]
        channel = Channel([message(n, f"Claim {n}", reply=p.id, resolved=p) for n, p in enumerate(parents, 1)])
        result, _ = await C.collect_evidence(channel, TARGET, now=NOW)
        self.assertEqual(result["other_member_replies"], [])

    async def test_image_only_posts_are_labelled_and_capped_to_four(self):
        attachments = [attachment(n) for n in range(6)]
        channel = Channel([message(n + 1, "", attachments=[a]) for n, a in enumerate(attachments)])
        result, images = await C.collect_evidence(channel, TARGET, now=NOW)
        self.assertEqual(len(images), 4)
        self.assertEqual(len(result["target_messages"]), 4)
        self.assertEqual([i["message_id"] for i in images], ["1", "2", "3", "4"])
        self.assertTrue(all(row["image_labels"] for row in result["target_messages"]))
        attachments[-1].read.assert_not_awaited()
        blocks = R.model_content(result, images, {"personal": [], "server": []}, [])
        self.assertEqual(sum(b["type"] == "image_url" for b in blocks), 4)
        self.assertIn("message 1", blocks[1]["text"])
        self.assertNotIn("data:image", blocks[0]["text"])

    async def test_images_do_not_expand_target_history(self):
        older = attachment()
        channel = Channel([message(n, f"claim number {n}") for n in range(1, 81)] + [message(81, "", attachments=[older])])
        result, images = await C.collect_evidence(channel, TARGET, now=NOW)
        self.assertEqual(len(result["target_messages"]), 80)
        self.assertEqual(channel.yielded, 80)
        self.assertEqual(images, [])
        older.read.assert_not_awaited()

    async def test_attachment_without_mime_type_is_checked_from_real_image_bytes(self):
        picture = attachment(content_type=None)
        channel = Channel([message(1, "", attachments=[picture])])
        result, images = await C.collect_evidence(channel, TARGET, now=NOW)
        self.assertEqual(len(images), 1)
        self.assertEqual(result["target_messages"][0]["image_labels"], ["image_1"])

    async def test_bad_images_fall_back_to_caption_or_drop_empty_post(self):
        broken = attachment(data=b"not an image")
        missing = attachment()
        missing.read.side_effect = RuntimeError("expired")
        huge = attachment(size=C.IMAGE_BYTE_LIMIT + 1)
        video = attachment(content_type="video/mp4")
        channel = Channel([
            message(1, "My undefeated record", attachments=[broken]),
            message(2, "", attachments=[missing]), message(3, "", attachments=[huge, video]),
        ])
        with self.assertLogs(C.logger, level="WARNING"):
            result, images = await C.collect_evidence(channel, TARGET, now=NOW)
        self.assertEqual([r["message_id"] for r in result["target_messages"]], ["1"])
        self.assertEqual(images, [])
        huge.read.assert_not_awaited()
        video.read.assert_not_awaited()

    def test_image_resize_and_animated_first_frame(self):
        from PIL import Image
        for size, fmt in [((2000, 1000), "PNG"), ((20, 20), "GIF")]:
            with self.subTest(fmt=fmt):
                original = Image.new("RGB", size, "blue")
                out = BytesIO()
                original.save(out, format=fmt, save_all=fmt == "GIF",
                              **({"append_images": [Image.new("RGB", size, "red")]} if fmt == "GIF" else {}))
                encoded = C._encode_image(out.getvalue())
                with Image.open(BytesIO(base64.b64decode(encoded.split(",", 1)[1]))) as result:
                    self.assertLessEqual(max(result.size), 1600)
                    self.assertEqual(result.format, "JPEG")
                    self.assertGreater(result.getpixel((0, 0))[2], 200)


class SelectionTests(unittest.TestCase):
    def test_selected_candidate_only_and_real_target_evidence(self):
        result = drafts(selected=2)
        self.assertEqual(R.select_roast(json.dumps(result), evidence(), []), result["candidates"][2])
        result["candidates"][2]["evidence_message_ids"] = ["11"]
        self.assertEqual(R.select_roast(json.dumps(result), evidence(), []), result["candidates"][0])
        for candidate in result["candidates"]:
            candidate["evidence_message_ids"] = ["10", "999"]
        with self.assertRaises(ValueError):
            R.select_roast(json.dumps(result), evidence(), [])

    def test_strays_always_eligible_when_present_and_require_both_sides(self):
        guild = types.SimpleNamespace(get_member=lambda uid: OTHER if uid == 2 else None)
        self.assertEqual(R.eligible_strays(evidence(), guild), ["2"])
        result = drafts(stray=True)
        self.assertEqual(R.select_roast(json.dumps(result), evidence(), ["2"])["stray_user_id"], "2")
        self.assertIsNone(R.select_roast(json.dumps(result), evidence(), [])["stray_user_id"])
        result["candidates"][0]["evidence_message_ids"] = ["10"]
        self.assertIsNone(R.select_roast(json.dumps(result), evidence(), ["2"])["stray_user_id"])

    def test_any_candidate_can_use_stray_and_no_candidate_has_to(self):
        result = drafts()
        for candidate in result["candidates"]:
            candidate.update(stray_user_id="2", evidence_message_ids=["10", "11"])
        self.assertEqual(R.select_roast(json.dumps(result), evidence(), ["2"])["stray_user_id"], "2")
        self.assertIsNone(R.select_roast(json.dumps(drafts()), evidence(), ["2"])["stray_user_id"])

    def test_departed_members_and_unrelated_exchanges_cannot_be_strays(self):
        self.assertEqual(R.eligible_strays(evidence(), types.SimpleNamespace(get_member=lambda _: None)), [])
        unrelated = evidence()
        unrelated["other_member_replies"][0]["target_message_id"] = "99"
        self.assertIsNone(R.select_roast(json.dumps(drafts(stray=True)), unrelated, ["2"])["stray_user_id"])

    def test_invalid_unselected_draft_does_not_block_valid_winner(self):
        result = drafts()
        result["candidates"][2]["evidence_message_ids"] = ["999"]
        self.assertEqual(R.select_roast(json.dumps(result), evidence(), []), result["candidates"][0])

    def test_empty_refusal_shape_and_invalid_output(self):
        self.assertIsNone(R.select_roast('{"candidates": [], "selected_index": null}', evidence(), []))
        for text in ["", "word " * 101, "paragraph\nparagraph", "Hello <@2>"]:
            result = drafts()
            for candidate in result["candidates"]:
                candidate["text"] = text
            with self.subTest(text=text), self.assertRaises(ValueError):
                R.select_roast(json.dumps(result), evidence(), [])
        for result in ["not json", '{"candidates": [], "selected_index": 0}']:
            with self.assertRaises(ValueError):
                R.select_roast(result, evidence(), [])

    def test_developed_roast_is_not_rejected_by_old_short_length_limit(self):
        result = drafts()
        text = (
            "You announced yourself as the chess authority, lost the match, then "
            "explained that everyone else was taking it too seriously. Fascinating "
            "how the competition became meaningless at precisely the moment you "
            "stopped winning it. By your next message, the board was faulty and "
            "the clock had an agenda. You've spent more effort appealing the result "
            "than you spent protecting your queen. Next time, submit the excuses "
            "before the match; at least then you'll have prepared an opening."
        )
        self.assertGreater(len(text.split()), 65)
        result["candidates"][0]["text"] = text
        self.assertEqual(R.select_roast(json.dumps(result), evidence(), [])["text"], text)


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.connection = memory_db(self)

    def test_legacy_migration_is_repeatable_and_preserves_old_rows(self):
        self.connection.execute("DROP TABLE recent_roasts")
        self.connection.execute("CREATE TABLE recent_roasts (id INTEGER PRIMARY KEY, target_name TEXT, roast_text TEXT, created_at REAL)")
        self.connection.execute("INSERT INTO recent_roasts VALUES (1, 'Alice', 'legacy text', 1)")
        self.connection.commit()
        database.init_db()
        database.init_db()
        self.assertEqual(self.connection.execute("SELECT target_name, roast_text, target_id FROM recent_roasts").fetchone(), ("Alice", "legacy text", None))
        self.assertEqual(R.load_memory(10, TARGET.id), {"personal": [], "server": []})

    def test_personal_memory_survives_rename_busy_members_and_guild_isolation(self):
        candidate = drafts()["candidates"][0]
        R.save_roast(10, TARGET, candidate)
        for n in range(20):
            R.save_roast(10, OTHER, {**candidate, "angle": f"other angle {n}"})
        R.save_roast(99, TARGET, {**candidate, "angle": "another guild"})
        TARGET_RENAMED = types.SimpleNamespace(id=1, display_name="New name")
        R.save_roast(10, TARGET_RENAMED, {**candidate, "angle": "new angle"})
        personal = R.load_memory(10, 1)["personal"]
        self.assertEqual([r["target_name"] for r in personal], ["New name", "Alice"])
        self.assertEqual(len(R.load_memory(10, 2)["personal"]), 8)
        self.assertEqual(len(R.load_memory(10, 2)["server"]), 6)
        self.assertNotIn("another guild", str(R.load_memory(10, 1)))


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        memory_db(self)
        self.api = AsyncMock(return_value=self.response())
        fake_client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=self.api)))
        fake_openai = types.ModuleType("openai")
        fake_openai.AsyncOpenAI = Mock(return_value=fake_client)
        spec = importlib.util.spec_from_file_location("_roast_under_test", Path(__file__).resolve().parents[1] / "commands/social/roast.py")
        self.command = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"openai": fake_openai}):
            spec.loader.exec_module(self.command)
        self.collect = AsyncMock(return_value=(evidence(), []))
        patcher = patch.object(R, "collect_evidence", self.collect)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.award = AsyncMock()
        events = types.ModuleType("lib.bot.event_handlers")
        events.award_badge_with_notify = self.award
        patcher = patch.dict(sys.modules, {"lib.bot.event_handlers": events})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.interaction = types.SimpleNamespace(
            user=REQUESTER, channel=Channel(), guild_id=10,
            guild=types.SimpleNamespace(get_member=lambda uid: OTHER if uid == 2 else None),
            client=object(), response=types.SimpleNamespace(
                defer=AsyncMock(), send_message=AsyncMock(), is_done=Mock(return_value=True)),
            followup=types.SimpleNamespace(send=AsyncMock()), delete_original_response=AsyncMock(),
        )

    @staticmethod
    def response(*, content=None, finish_reason="stop", refusal=None):
        return types.SimpleNamespace(choices=[types.SimpleNamespace(
            finish_reason=finish_reason,
            message=types.SimpleNamespace(content=content if content is not None else json.dumps(drafts()), refusal=refusal),
        )])

    def usage(self):
        row = database.DatabaseManager.fetch_one("SELECT SUM(count) FROM roast_usage WHERE user_id = ?", (str(REQUESTER.id),))
        return row[0] or 0

    async def test_success_posts_only_winner_and_preserves_mentions_badges_and_memory(self):
        await self.command.roast(self.interaction, user=TARGET)
        self.assertEqual(self.usage(), 1)
        sent = self.interaction.channel.send.call_args
        self.assertIn(drafts()["candidates"][0]["text"], sent.args[0])
        self.assertNotIn("evidence_message_ids", sent.args[0])
        self.assertEqual(sent.kwargs["allowed_mentions"].users, [TARGET])
        self.assertFalse(sent.kwargs["allowed_mentions"].everyone)
        self.assertEqual(self.award.await_count, 2)
        self.assertEqual(len(R.load_memory(10, TARGET.id)["personal"]), 1)
        self.api.assert_awaited_once()
        self.interaction.followup.send.assert_not_awaited()

    async def test_empty_history_refunds_and_does_not_call_model(self):
        self.collect.return_value = ({**evidence(), "target_messages": []}, [])
        await self.command.roast(self.interaction, user=TARGET)
        self.assertEqual(self.usage(), 0)
        self.api.assert_not_awaited()
        self.interaction.channel.send.assert_not_awaited()

    async def test_history_api_delivery_errors_and_cancellation_refund(self):
        for failing in [self.collect, self.api, self.interaction.channel.send]:
            failing.side_effect = RuntimeError("test failure")
            with self.assertLogs(self.command.logger, level="ERROR"):
                await self.command.roast(self.interaction, user=TARGET)
            failing.side_effect = None
            self.assertEqual(self.usage(), 0)
        self.collect.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.command.roast(self.interaction, user=TARGET)
        self.assertEqual(self.usage(), 0)
        self.assertEqual(R.load_memory(10, TARGET.id)["personal"], [])

    async def test_invalid_truncated_refused_and_unusable_generations_refund(self):
        for response in [self.response(content="not json"), self.response(finish_reason="length"),
                         self.response(refusal="declined"), self.response(content=""),
                         self.response(content='{"candidates": [], "selected_index": null}')]:
            self.api.return_value = response
            with patch.object(self.command.logger, "exception"):
                await self.command.roast(self.interaction, user=TARGET)
            self.assertEqual(self.usage(), 0)
        self.interaction.channel.send.assert_not_awaited()

    async def test_badge_or_memory_failure_after_post_does_not_refund_or_report_delivery_failure(self):
        self.award.side_effect = RuntimeError("badge failure")
        with patch.object(R, "save_roast", side_effect=RuntimeError("db failure")), self.assertLogs(self.command.logger, level="ERROR"):
            await self.command.roast(self.interaction, user=TARGET)
        self.assertEqual(self.usage(), 1)
        self.interaction.channel.send.assert_awaited_once()
        self.interaction.followup.send.assert_not_awaited()

    async def test_concurrent_calls_cannot_exceed_daily_limit(self):
        started, release = asyncio.Event(), asyncio.Event()
        calls = 0

        async def blocked_collect(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == self.command.ROAST_DAILY_LIMIT:
                started.set()
            await release.wait()
            return evidence(), []

        self.collect.side_effect = blocked_collect
        requests = [asyncio.create_task(self.command.roast(self.interaction, user=TARGET)) for _ in range(self.command.ROAST_DAILY_LIMIT)]
        try:
            await asyncio.wait_for(started.wait(), 1)
            await self.command.roast(self.interaction, user=TARGET)
            self.assertEqual(calls, self.command.ROAST_DAILY_LIMIT)
            self.assertEqual(self.usage(), self.command.ROAST_DAILY_LIMIT)
            self.assertIn("daily limit", self.interaction.response.send_message.call_args.args[0])
        finally:
            release.set()
            await asyncio.gather(*requests)
        self.assertEqual(self.interaction.channel.send.await_count, self.command.ROAST_DAILY_LIMIT)

    async def test_images_forwarded_and_stray_can_recur_without_cooldown(self):
        self.api.return_value = self.response(content=json.dumps(drafts(stray=True)))
        image = {"label": "image_1", "message_id": "10", "data_url": "data:image/jpeg;base64,abc"}
        self.collect.return_value = (evidence(), [image])
        for _ in range(2):
            await self.command.roast(self.interaction, user=TARGET)
            payload = self.api.call_args.kwargs["messages"][1]["content"]
            self.assertEqual(json.loads(payload[0]["text"])["eligible_stray_user_ids"], ["2"])
            self.assertEqual(payload[-1]["image_url"]["url"], image["data_url"])
        self.assertEqual(self.usage(), 2)
        self.assertTrue(all(r["stray_user_id"] == "2" for r in R.load_memory(10, 1)["personal"]))


if __name__ == "__main__":
    unittest.main()
