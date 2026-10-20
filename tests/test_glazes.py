"""Glaze forms, selection, independent memory and Discord delivery, with no external calls."""
import asyncio
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from test_roasts import Channel, NOW, OTHER, POSTABLE, REQUESTER, TARGET, attachment, evidence, memory_db, message
import database
from lib.features import glazes as G, member_context as C, roasts as R

FORMS = ["commentary", "documentary", "bulletin"]


def drafts(selected=0, forms=FORMS):
    return {
        "candidates": [{
            "form": forms[n % len(forms)],
            "angle": f"Chess confidence celebrated {n}",
            "evidence_message_ids": ["10"],
            "text": f"Alice announces her chess ambitions with the certainty of someone already choosing the trophy cabinet, and the whole room believes the tournament has been decided. Edition {n}.",
        } for n in range(3)],
        "selected_index": selected,
    }


class FormTests(unittest.TestCase):
    def test_unused_forms_come_first_and_recent_ones_wait_their_turn(self):
        self.assertEqual(G.offered_forms([]), ["hype", "commentary", "documentary"])
        self.assertEqual(G.offered_forms(["hype"] * 7), ["commentary", "documentary", "bulletin"])
        recent = ["documentary", "commentary", "hype", "bulletin", "review", "citation", "trailer"]
        self.assertEqual(G.offered_forms(recent), ["encyclopedia", "citation", "review"])
        self.assertEqual(G.offered_forms(recent, "documentary"), ["documentary"])

    def test_ode_and_trailer_only_run_when_requested(self):
        for form in ["ode", "trailer"]:
            with self.subTest(form=form):
                self.assertNotIn(form, G.offered_forms(list(G.FORMS)[::-1]))
                self.assertEqual(G.offered_forms([], form), [form])

    def test_every_style_label_maps_to_a_form(self):
        self.assertEqual(sorted(G.STYLE_CHOICES.values()), sorted(G.FORMS))
        self.assertEqual(G.STYLE_CHOICES["Nature documentary"], "documentary")

    def test_prompt_and_schema_carry_only_the_offered_forms(self):
        prompt = G.system_prompt(FORMS)
        for form in G.FORMS:
            self.assertEqual(f"- {form}: " in prompt, form in FORMS)
        self.assertIn("one in each of these forms", prompt)
        self.assertIn("chose this form", G.system_prompt(["ode"], chosen=True))
        schema = G.response_format(FORMS)["json_schema"]["schema"]
        self.assertEqual(schema["properties"]["candidates"]["items"]["properties"]["form"]["enum"], FORMS)

    def test_header_uses_the_form_emoji(self):
        self.assertTrue(G.header(TARGET, REQUESTER, "documentary").startswith("🌿 <@1> 🌿\n-# glazed at Charlie's request"))
        self.assertTrue(G.header(TARGET, REQUESTER, None).startswith("✨ <@1> ✨"))


class SelectionTests(unittest.TestCase):
    def test_selected_winner_and_fallback_require_target_evidence(self):
        result = drafts(2)
        self.assertEqual(G.select_glaze(json.dumps(result), evidence(), FORMS), result["candidates"][2])
        result["candidates"][2]["evidence_message_ids"] = ["11"]
        result["candidates"][0] = None
        self.assertEqual(G.select_glaze(json.dumps(result), evidence(), FORMS), result["candidates"][1])
        result["candidates"][1]["evidence_message_ids"] = ["10", "missing"]
        with self.assertRaises(ValueError):
            G.select_glaze(json.dumps(result), evidence(), FORMS)

    def test_invalid_text_form_or_evidence_cannot_be_delivered(self):
        for field, value in [
            ("text", ""), ("text", "word " * 101), ("text", "x" * 1201), ("text", "\n".join("line" for _ in range(9))),
            ("text", "Hello <@2>"), ("text", "Hello @everyone"), ("text", None), ("angle", ""), ("angle", "x" * 241),
            ("form", "ode"), ("form", None),
            ("evidence_message_ids", []), ("evidence_message_ids", ["11"]),
            ("evidence_message_ids", ["unknown"]), ("evidence_message_ids", [10]),
        ]:
            result = drafts()
            for candidate in result["candidates"]:
                candidate[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                G.select_glaze(json.dumps(result), evidence(), FORMS)

    def test_a_clean_runner_up_beats_a_winner_built_on_the_reframe(self):
        result = drafts(1)
        result["candidates"][1]["text"] = "Alice isn't playing chess, she's rewriting the rules of the board itself."
        result["candidates"][0]["text"] = "Alice didn't just enter, she conquered. It's chess, and she owns it."
        self.assertEqual(G.select_glaze(json.dumps(result), evidence(), FORMS), result["candidates"][2])
        for candidate in result["candidates"]:
            candidate["text"] = "That's not chess, that's a coronation."
        self.assertEqual(G.select_glaze(json.dumps(result), evidence(), FORMS), result["candidates"][1])

    def test_text_is_tidied_and_line_forms_keep_their_lines(self):
        result = drafts()
        result["candidates"][0]["text"] = "# CHESS GENIUS SPEAKS\n\n> Alice moves—and the board bows – twice.\n"
        self.assertEqual(G.select_glaze(json.dumps(result), evidence(), FORMS)["text"],
                         "CHESS GENIUS SPEAKS\nAlice moves - and the board bows - twice.")

    def test_no_material_and_invalid_selection(self):
        self.assertIsNone(G.select_glaze('{"candidates": [], "selected_index": null}', evidence(), FORMS))
        for selected in [True, -1, 3, None, "0"]:
            with self.subTest(selected=selected), self.assertRaises(ValueError):
                G.select_glaze(json.dumps(drafts(selected)), evidence(), FORMS)
        with self.assertRaises(ValueError):
            G.select_glaze("not JSON", evidence(), FORMS)


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.connection = memory_db(self)

    def test_repeatable_migration_and_separate_roast_memory(self):
        roast = {**drafts()["candidates"][0], "stray_user_id": None}
        R.save_roast(10, TARGET, roast)
        self.assertEqual(G.load_memory(10, TARGET.id), {"personal": [], "server": []})
        G.save_glaze(10, TARGET, drafts()["candidates"][1])
        database.init_db()
        database.init_db()
        self.assertEqual(G.load_memory(10, TARGET.id)["personal"][0]["text"], drafts()["candidates"][1]["text"])
        self.assertEqual(R.load_memory(10, TARGET.id)["personal"][0]["text"], roast["text"])

    def test_rename_busy_member_guild_isolation_and_newest_first(self):
        candidate = drafts()["candidates"][0]
        G.save_glaze(10, TARGET, candidate)
        for n in range(20):
            G.save_glaze(10, OTHER, {**candidate, "angle": f"other angle {n}"})
        G.save_glaze(99, TARGET, {**candidate, "angle": "another guild"})
        renamed = types.SimpleNamespace(id=TARGET.id, display_name="New name")
        G.save_glaze(10, renamed, {**candidate, "angle": "newest"})
        memory = G.load_memory(10, TARGET.id)
        self.assertEqual([row["target_name"] for row in memory["personal"]], ["New name", "Alice"])
        self.assertEqual(len(memory["server"]), 6)
        self.assertEqual(memory["server"][0]["angle"], "newest")
        self.assertEqual(memory["server"][1]["angle"], "other angle 19")
        self.assertEqual(len(G.load_memory(10, OTHER.id)["personal"]), 8)
        self.assertNotIn("another guild", str(memory))
        self.assertEqual(R.load_memory(10, TARGET.id), {"personal": [], "server": []})

    def test_forms_are_remembered_and_legacy_glazes_count_as_hype(self):
        self.connection.execute(
            "INSERT INTO recent_glazes (guild_id, target_id, target_name, angle, glaze_text, created_at) "
            "VALUES ('10', '1', 'Alice', 'old', 'old glaze', 0)")
        G.save_glaze(10, TARGET, {**drafts()["candidates"][0], "form": "ode"})
        G.save_glaze(99, TARGET, {**drafts()["candidates"][0], "form": "trailer"})
        self.assertEqual(G.recent_forms(10), ["ode", "hype"])
        self.assertEqual(G.load_memory(10, TARGET.id)["personal"][0]["form"], "ode")


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        memory_db(self)
        self.api = AsyncMock(return_value=self.response())
        fake_client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=self.api)))
        fake_openai = types.ModuleType("openai")
        fake_openai.AsyncOpenAI = Mock(return_value=fake_client)
        spec = importlib.util.spec_from_file_location("_glaze_under_test", Path(__file__).resolve().parents[1] / "commands/social/glaze.py")
        self.command = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"openai": fake_openai}):
            spec.loader.exec_module(self.command)
        self.command.USERS = types.SimpleNamespace(OGGERS=REQUESTER.id)
        self.collect = AsyncMock(return_value=(evidence(), []))
        patcher = patch.object(G, "collect_evidence", self.collect)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.interaction = types.SimpleNamespace(
            user=REQUESTER, channel=Channel(), guild_id=10, permissions=POSTABLE,
            response=types.SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock(), is_done=Mock(return_value=True)),
            followup=types.SimpleNamespace(send=AsyncMock()), delete_original_response=AsyncMock(),
        )

    @staticmethod
    def response(content=None, finish_reason="stop", refusal=None):
        return types.SimpleNamespace(choices=[types.SimpleNamespace(
            finish_reason=finish_reason,
            message=types.SimpleNamespace(content=json.dumps(drafts()) if content is None else content, refusal=refusal),
        )])

    def usage(self):
        row = database.DatabaseManager.fetch_one("SELECT SUM(count) FROM glaze_usage WHERE user_id = ?", (str(REQUESTER.id),))
        return row[0] or 0

    def assert_refused_before_history(self):
        self.interaction.response.send_message.assert_awaited_once()
        self.assertTrue(self.interaction.response.send_message.call_args.kwargs["ephemeral"])
        self.interaction.response.defer.assert_not_awaited()
        self.collect.assert_not_awaited()
        self.api.assert_not_awaited()
        self.interaction.channel.send.assert_not_awaited()

    async def test_unreadable_source_or_unpostable_destination_is_refused(self):
        await self.command.glaze(self.interaction, channel=Channel(readable=False), user=TARGET)
        self.assert_refused_before_history()
        self.interaction.response.send_message.reset_mock()
        self.interaction.permissions = types.SimpleNamespace(send_messages=False, send_messages_in_threads=False)
        await self.command.glaze(self.interaction, user=TARGET)
        self.assert_refused_before_history()

    async def test_daily_limit_applies_to_everyone_but_oggers_and_failures_refund(self):
        self.command.USERS = types.SimpleNamespace(OGGERS=999)
        # Each delivered glaze rotates the offered forms, so answer in whichever forms were offered.
        offered = lambda **kwargs: self.response(json.dumps(drafts(
            forms=kwargs["response_format"]["json_schema"]["schema"]["properties"]["candidates"]["items"]["properties"]["form"]["enum"])))
        self.api.side_effect = offered
        for _ in range(self.command.GLAZE_DAILY_LIMIT):
            await self.command.glaze(self.interaction, user=TARGET)
        self.assertEqual(self.interaction.channel.send.await_count, self.command.GLAZE_DAILY_LIMIT)
        self.assertEqual(self.usage(), self.command.GLAZE_DAILY_LIMIT)
        self.interaction.channel.send.reset_mock()
        self.collect.reset_mock()
        self.api.reset_mock()
        await self.command.glaze(self.interaction, user=TARGET)
        self.assertIn(f"all {self.command.GLAZE_DAILY_LIMIT} of today's glazes", self.interaction.response.send_message.call_args.args[0])
        self.collect.assert_not_awaited()
        self.api.assert_not_awaited()

        database.DatabaseManager.execute("DELETE FROM glaze_usage")
        self.api.side_effect = RuntimeError("api down")
        with self.assertLogs(self.command.logger, level="ERROR"):
            await self.command.glaze(self.interaction, user=TARGET)
        self.assertEqual(self.usage(), 0)

        self.command.USERS = types.SimpleNamespace(OGGERS=REQUESTER.id)
        self.api.side_effect = offered
        self.interaction.channel.send.reset_mock()
        await self.command.glaze(self.interaction, user=TARGET)
        self.interaction.channel.send.assert_awaited_once()
        self.assertEqual(self.usage(), 0)

    async def test_images_source_channel_memory_and_only_selected_winner_are_delivered(self):
        G.save_glaze(10, TARGET, drafts()["candidates"][0])
        picture = attachment()
        source = Channel([message(11, "I can confirm", author=OTHER, reply=10),
                          message(10, "I never lose", attachments=[picture])])
        async def collect(channel, target):
            return await C.collect_evidence(channel, target, now=NOW)
        self.collect.side_effect = collect
        # The remembered commentary glaze sends commentary to the back of the queue.
        offered = ["hype", "documentary", "bulletin"]
        self.api.return_value = self.response(json.dumps(drafts(2, forms=offered)))
        await self.command.glaze(self.interaction, channel=source, user=TARGET)
        self.collect.assert_awaited_once_with(source, TARGET)
        self.interaction.response.defer.assert_awaited_once_with(ephemeral=True)
        sent = self.interaction.channel.send.call_args
        self.assertTrue(sent.args[0].startswith("📰 <@1> 📰"))
        self.assertIn(drafts()["candidates"][2]["text"], sent.args[0])
        self.assertNotIn(drafts()["candidates"][1]["text"], sent.args[0])
        self.assertEqual(sent.kwargs["allowed_mentions"].users, [TARGET])
        self.assertFalse(sent.kwargs["allowed_mentions"].everyone)
        self.assertFalse(sent.kwargs["allowed_mentions"].roles)
        self.interaction.channel.send.assert_awaited_once()
        source.send.assert_not_awaited()
        self.interaction.followup.send.assert_not_awaited()
        self.interaction.delete_original_response.assert_awaited_once()
        payload = self.api.call_args.kwargs
        self.assertEqual(payload["model"], "gpt-5.4")
        self.assertEqual(payload["response_format"], G.response_format(offered))
        self.assertEqual(payload["messages"][0]["content"], G.system_prompt(offered))
        blocks = payload["messages"][1]["content"]
        metadata = json.loads(blocks[0]["text"])
        self.assertEqual(metadata["recent_glazes"]["personal"][0]["text"], drafts()["candidates"][0]["text"])
        self.assertIsNone(metadata["requested_occasion"])
        self.assertNotIn("recent_roasts", metadata)
        self.assertEqual(metadata["other_member_replies"][0]["author_id"], "2")
        self.assertEqual(sum(block["type"] == "image_url" for block in blocks), 1)
        self.assertIn("message 10", blocks[1]["text"])
        self.assertEqual(blocks[2]["image_url"]["detail"], "high")
        memory = G.load_memory(10, TARGET.id)
        self.assertEqual(len(memory["personal"]), 2)
        self.assertEqual(memory["personal"][0]["form"], "bulletin")
        self.assertEqual(R.load_memory(10, TARGET.id), {"personal": [], "server": []})

    async def test_style_and_occasion_steer_the_drafts(self):
        self.api.return_value = self.response(json.dumps(drafts(forms=["ode"])))
        await self.command.glaze(self.interaction, user=TARGET, about="  passing   their\ndriving test " + "x" * 200,
                                 style="Ode")
        payload = self.api.call_args.kwargs
        self.assertEqual(payload["response_format"], G.response_format(["ode"]))
        self.assertEqual(payload["messages"][0]["content"], G.system_prompt(["ode"], chosen=True))
        occasion = json.loads(payload["messages"][1]["content"][0]["text"])["requested_occasion"]
        self.assertTrue(occasion.startswith("passing their driving test xx"))
        self.assertEqual(len(occasion), G.ABOUT_LIMIT)
        self.assertTrue(self.interaction.channel.send.call_args.args[0].startswith("📜"))

    async def test_default_channel_and_target(self):
        await self.command.glaze(self.interaction)
        self.collect.assert_awaited_once_with(self.interaction.channel, REQUESTER)

    async def test_empty_history_is_private_and_skips_api(self):
        self.collect.return_value = ({**evidence(), "target_messages": []}, [])
        await self.command.glaze(self.interaction, user=TARGET)
        self.api.assert_not_awaited()
        self.interaction.channel.send.assert_not_awaited()
        self.assertTrue(self.interaction.followup.send.call_args.kwargs["ephemeral"])
        self.assertFalse(self.interaction.followup.send.call_args.kwargs["allowed_mentions"].everyone)

    async def test_failures_are_private_and_do_not_create_memory(self):
        for failing in [self.collect, self.api, self.interaction.channel.send]:
            failing.side_effect = RuntimeError("test failure")
            with self.assertLogs(self.command.logger, level="ERROR"):
                await self.command.glaze(self.interaction, user=TARGET)
            failing.side_effect = None
            self.assertTrue(self.interaction.followup.send.call_args.kwargs["ephemeral"])
            self.assertEqual(G.load_memory(10, TARGET.id)["personal"], [])
        self.collect.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.command.glaze(self.interaction, user=TARGET)

    async def test_invalid_truncated_refused_and_no_material_never_post(self):
        for response in [self.response("not json"), self.response(""), self.response(finish_reason="length"),
                         self.response(refusal="declined"), self.response('{"candidates": [], "selected_index": null}')]:
            self.api.return_value = response
            with patch.object(self.command.logger, "exception"):
                await self.command.glaze(self.interaction, user=TARGET)
            self.assertTrue(self.interaction.followup.send.call_args.kwargs["ephemeral"])
        self.interaction.channel.send.assert_not_awaited()
        self.assertEqual(G.load_memory(10, TARGET.id)["personal"], [])

    async def test_post_delivery_cleanup_and_memory_failure_never_report_failed_glaze(self):
        self.interaction.delete_original_response.side_effect = RuntimeError("expired interaction")
        with patch.object(G, "save_glaze", side_effect=RuntimeError("db failure")), self.assertLogs(self.command.logger, level="ERROR"):
            await self.command.glaze(self.interaction, user=TARGET)
        self.interaction.channel.send.assert_awaited_once()
        self.interaction.followup.send.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
