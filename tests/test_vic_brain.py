"""Vic's reply brain: the prompt it builds, how replies are tidied, thread memory, running bits, the run log,
and the wiring into the direct-mention handler."""
import json
import os
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lib.features import vic_brain
from lib.features.vic_brain import BrainReply, BrainRequest, Line


def _response(text, searches=0, tokens=(100, 20)):
    out = [{"type": "web_search_call"} for _ in range(searches)]
    out.append({"type": "message", "content": [{"type": "output_text", "text": text}]})
    return {"status": "completed", "output": out, "usage": {"input_tokens": tokens[0], "output_tokens": tokens[1]}}


class TestPrompt(unittest.TestCase):
    def test_message_to_answer_comes_last_with_the_typed_words(self):
        req = BrainRequest(
            asker_name="Johnny", text="Shit names",
            thread=[Line(author="Vic", text='Og\'s epics: "AI, Love & GPUs"', is_vic=True)],
            recent=[Line(author="Kaizo", text="remind us of the titles"), Line(author="Vic", text="titles here", is_vic=True)],
            people=[("Kaizo", 12), ("Johnny", 34)], now=datetime(2026, 11, 21, 15, 6, tzinfo=timezone.utc),
        )
        prompt = vic_brain.build_user_prompt(req)
        self.assertTrue(prompt.rstrip().endswith("Shit names"))
        self.assertIn("sent as a reply to your message above", prompt)
        self.assertIn("[you]: titles here", prompt)
        self.assertIn("- Kaizo: 12", prompt)
        self.assertIn("Saturday 21 November 2026", prompt)
        self.assertLess(prompt.index("RECENT CHAT"), prompt.index("THREAD"))

    def test_bare_tag_and_picture_note(self):
        req = BrainRequest(asker_name="Og", text="", thread=[
            Line(author="Vic", text="caption", is_vic=True, images=1, note="a picture you made for Kaizo. who is who: Johnny: the gamer"),
        ])
        prompt = vic_brain.build_user_prompt(req)
        self.assertIn("[no words, just the tag]", prompt)
        self.assertIn("note: a picture you made for Kaizo", prompt)
        self.assertIn("[picture]", prompt)

    def test_records_bits_and_images_are_named(self):
        req = BrainRequest(asker_name="Og", text="who's richest", records="Steven: 12,000 UKP", bits=["Fray Bentos pies (set by Kaizo)"],
                           image_urls=["https://cdn/x.png"], about=[("Dalila", ["hola", "ham"])])
        prompt = vic_brain.build_user_prompt(req)
        self.assertIn("the only server figures you may quote", prompt)
        self.assertIn("RUNNING BITS\n- Fray Bentos pies", prompt)
        self.assertIn("It has 1 image(s) attached", prompt)
        self.assertIn("WHAT DALILA HAS BEEN SAYING LATELY", prompt)


class TestTidy(unittest.TestCase):
    def test_strips_the_ah_opener_and_help_closer(self):
        text, bit, clear = vic_brain.tidy("Ah, Fray Bentos pies. Noted. Let me know if there's anything else.")
        self.assertEqual(text, "Fray Bentos pies. Noted.")
        self.assertIsNone(bit)
        self.assertFalse(clear)

    def test_keeps_words_that_only_start_like_an_opener(self):
        self.assertEqual(vic_brain.tidy("Wellington boots, obviously.")[0], "Wellington boots, obviously.")
        self.assertEqual(vic_brain.tidy("So what.")[0], "So what.")

    def test_bit_tags_come_out_of_the_text(self):
        text, bit, clear = vic_brain.tidy("Fine. Pies it is.\n<bit>obsessed with Fray Bentos pies</bit>")
        self.assertEqual((text, bit, clear), ("Fine. Pies it is.", "obsessed with Fray Bentos pies", False))
        text, bit, clear = vic_brain.tidy('Dropped.\n<bit clear="1"/>')
        self.assertEqual((text, bit, clear), ("Dropped.", None, True))

    def test_whole_reply_in_quotes_is_unwrapped_but_a_quoted_phrase_is_not(self):
        self.assertEqual(vic_brain.tidy('"No."')[0], "No.")
        self.assertEqual(vic_brain.tidy('"Icebreaker" and "Wildfire"')[0], '"Icebreaker" and "Wildfire"')

    def test_long_replies_are_cut_to_fit_discord(self):
        self.assertLessEqual(len(vic_brain.tidy("x" * 5000)[0]), vic_brain.REPLY_CHAR_LIMIT)


class TestGenerate(unittest.TestCase):
    def test_reply_usage_and_searches(self):
        post = MagicMock(return_value=_response("Two nil. Grim.", searches=1))
        with patch.dict(os.environ, {"OPENAI_TOKEN": "k"}):
            reply = vic_brain.generate_reply(BrainRequest(asker_name="Og", text="score?", force_search=True), post)
        self.assertEqual((reply.text, reply.searches, reply.input_tokens, reply.output_tokens), ("Two nil. Grim.", 1, 100, 20))
        payload = post.call_args[0][0]
        self.assertEqual(payload["tool_choice"], "required")
        self.assertEqual(payload["instructions"], vic_brain.PERSONA)
        self.assertNotIn("temperature", payload)  # reasoning models reject it

    def test_a_repeat_of_an_earlier_line_is_asked_for_again(self):
        earlier = "North Korea's internet is about as free as a caged hamster. Enjoy the enlightenment."
        post = MagicMock(side_effect=[_response(earlier), _response("Fewer hairstyles than dead channels.")])
        req = BrainRequest(asker_name="Og", text="more", thread=[Line(author="Vic", text=earlier, is_vic=True)])
        with patch.dict(os.environ, {"OPENAI_TOKEN": "k"}):
            reply = vic_brain.generate_reply(req, post)
        self.assertEqual(reply.text, "Fewer hairstyles than dead channels.")
        self.assertTrue(reply.retried_repeat)
        self.assertEqual(reply.input_tokens, 200)
        self.assertIn("repeated a line", post.call_args_list[1][0][0]["input"][0]["content"][0]["text"])

    def test_images_are_dropped_when_the_call_with_them_fails(self):
        post = MagicMock(side_effect=[RuntimeError("bad image url"), _response("It's a grey smudge.")])
        req = BrainRequest(asker_name="Og", text="what's this", image_urls=["https://cdn/x.png"])
        with patch.dict(os.environ, {"OPENAI_TOKEN": "k"}):
            reply = vic_brain.generate_reply(req, post)
        self.assertEqual(reply.text, "It's a grey smudge.")
        first, second = (c[0][0]["input"][0]["content"] for c in post.call_args_list)
        self.assertEqual([i["type"] for i in first], ["input_text", "input_image"])
        self.assertEqual([i["type"] for i in second], ["input_text"])

    def test_nothing_usable_raises(self):
        post = MagicMock(return_value={"status": "failed", "error": {"message": "boom"}})
        with patch.dict(os.environ, {"OPENAI_TOKEN": "k"}):
            with self.assertRaises(vic_brain.BrainError):
                vic_brain.generate_reply(BrainRequest(asker_name="Og", text="hi"), post)


class TestMemory(unittest.TestCase):
    def test_a_picture_is_recalled_as_a_note_and_text_is_not(self):
        vic_brain.remember_reply(111, "image", asker="Kaizo", request="redraw The Last Supper", showed="thirteen at a table",
                                 cast="Johnny (<@797>): the gamer\nKaizo (<@128>): the dad")
        vic_brain.remember_reply(112, "text", asker="Kaizo", request="hi", caption="No.")
        note = vic_brain.note_for(111)
        self.assertIn("a picture you made for Kaizo", note)
        self.assertIn("Johnny (<@797>): the gamer; Kaizo (<@128>): the dad", note)
        self.assertIsNone(vic_brain.note_for(112))
        self.assertIsNone(vic_brain.note_for(999))
        vic_brain._state = None  # a restart reads it back from disk
        self.assertIn("thirteen at a table", vic_brain.note_for(111))

    def test_memory_is_capped(self):
        with patch.object(vic_brain, "MEMORY_LIMIT", 3):
            for i in range(5):
                vic_brain.remember_reply(i + 1, "text", request=str(i))
        self.assertIsNone(vic_brain.recall_reply(1))
        self.assertIsNotNone(vic_brain.recall_reply(5))

    def test_bits_expire_stay_per_channel_and_clear(self):
        now = time.time()
        vic_brain.set_bit(1, "obsessed with Fray Bentos pies", "Kaizo", now=now)
        vic_brain.set_bit(1, "talks like Big Sam", "Johnny", now=now)
        vic_brain.set_bit(1, "third", "Og", now=now)
        self.assertEqual(len(vic_brain.active_bits(1, now=now)), vic_brain.BIT_MAX_PER_CHANNEL)
        self.assertEqual(vic_brain.active_bits(2, now=now), [])
        self.assertEqual(vic_brain.active_bits(1, now=now + vic_brain.BIT_TTL_SECONDS + 1), [])
        vic_brain.clear_bits(1)
        self.assertEqual(vic_brain.active_bits(1, now=now), [])

    def test_run_log_lines(self):
        vic_brain.log_run(route="text", asker="Og", request="hi", reply="No.", ms=1200, searches=None)
        with open(vic_brain.RUN_LOG_FILE) as f:
            entry = json.loads(f.readline())
        self.assertEqual((entry["route"], entry["reply"], entry["ms"]), ("text", "No.", 1200))
        self.assertNotIn("searches", entry)


# --- the handler -----------------------------------------------------------------------------------------------

def _message(content, author_id=404634271861571584, mid=5000):
    from config import USERS
    message = MagicMock()
    message.id = mid
    message.content = content
    message.author.id = USERS.OGGERS if author_id is None else author_id
    message.author.nick = "Og"
    message.author.bot = False
    message.mentions = []
    message.attachments = []
    message.embeds = []
    message.reference = None
    message.guild = None
    message.channel.id = 959493057076666380
    message.channel.name = "general"
    message.channel.typing.return_value.__aenter__ = AsyncMock()
    message.channel.typing.return_value.__aexit__ = AsyncMock()

    async def _history(*a, **k):
        for m in ():
            yield m
    message.channel.history = _history
    posted = MagicMock()
    posted.id = 6000
    message.reply = AsyncMock(return_value=posted)
    return message


@pytest.mark.asyncio
async def test_text_reply_goes_through_the_brain_and_is_remembered(monkeypatch):
    from lib.features import chat_responder as cr
    monkeypatch.setattr(vic_brain, "ENABLED", True)
    client = MagicMock()
    client.user.id = 1171842947440967770
    message = _message("<@1171842947440967770> rewrite those")
    seen = {}

    def fake_generate(req, post):
        seen["req"] = req
        return BrainReply(text="Ah, here you go.\n<bit>pies</bit>", model="gpt-5.5", input_tokens=10, output_tokens=5,
                          bit_set="pies")

    monkeypatch.setattr(vic_brain, "generate_reply", lambda req, post: _tidied(fake_generate(req, post)))
    with patch.object(cr, "judge_mention", AsyncMock(return_value=None)), \
         patch.object(cr, "plan_mention", return_value=None), \
         patch.object(cr, "classify_mention_intent", return_value={"intent": "reply"}), \
         patch.object(cr, "find_recent_image_attachment", AsyncMock(return_value=None)), \
         patch.object(cr, "gather_one_off_context", AsyncMock(side_effect=AssertionError("old path used"))), \
         patch.object(cr, "generate_one_off_reply", side_effect=AssertionError("old path used")), \
         patch.object(cr.live_chat_manager, "record_usage") as usage, \
         patch.object(cr.live_chat_manager, "update_dashboard", AsyncMock()):
        cr._handled_one_off_message_ids.clear()
        assert await cr.handle_one_off_owner_mention(client, message) is True
    assert seen["req"].text == "rewrite those"
    assert seen["req"].channel_name == "general"
    assert message.reply.call_args[0][0] == "Here you go."
    usage.assert_any_call("gpt-5.5", 10, 5, is_reply=True)
    assert vic_brain.recall_reply(6000)["kind"] == "text"
    assert vic_brain.active_bits(959493057076666380) == ["pies (set by Og)"]
    with open(vic_brain.RUN_LOG_FILE) as f:
        entry = json.loads(f.readline())
    assert entry["route"] == "text" and entry["request"] == "rewrite those" and entry["reply_id"] == 6000


def _tidied(reply):
    reply.text, _bit, _clear = vic_brain.tidy(reply.text)
    return reply


@pytest.mark.asyncio
async def test_brain_failure_falls_back_to_the_old_path(monkeypatch):
    from lib.features import chat_responder as cr
    monkeypatch.setattr(vic_brain, "ENABLED", True)
    monkeypatch.setattr(vic_brain, "generate_reply", MagicMock(side_effect=vic_brain.BrainError("down")))
    client = MagicMock()
    client.user.id = 1171842947440967770
    message = _message("<@1171842947440967770> hello", mid=5001)
    with patch.object(cr, "judge_mention", AsyncMock(return_value=None)), \
         patch.object(cr, "plan_mention", return_value=None), \
         patch.object(cr, "classify_mention_intent", return_value={"intent": "reply"}), \
         patch.object(cr, "find_recent_image_attachment", AsyncMock(return_value=None)), \
         patch.object(cr, "gather_one_off_context", AsyncMock(return_value=("ctx", {}))), \
         patch.object(cr, "generate_one_off_reply", return_value=("Old reply.", 1, 1)) as old, \
         patch.object(cr.live_chat_manager, "record_usage"), \
         patch.object(cr.live_chat_manager, "update_dashboard", AsyncMock()):
        cr._handled_one_off_message_ids.clear()
        assert await cr.handle_one_off_owner_mention(client, message) is True
    old.assert_called_once()
    assert message.reply.call_args[0][0] == "Old reply."
