import sys
import types
import unittest
from unittest.mock import MagicMock, patch, AsyncMock
import time
import json
import io
import asyncio
from datetime import datetime, timezone

# Install stubs if discord is not installed in local environment
if "discord" not in sys.modules:
    discord = types.ModuleType("discord")
    discord.ButtonStyle = types.SimpleNamespace(secondary=0, success=1, danger=2, primary=3, link=4)
    discord.ChannelType = types.SimpleNamespace(text=0)
    discord.TextStyle = types.SimpleNamespace(paragraph=2)
    discord.EventStatus = types.SimpleNamespace(scheduled=1, active=2)

    class MockEmbed:
        def __init__(self, **kwargs):
            self.fields = []
            self.title = kwargs.get("title")
            self.description = kwargs.get("description")
            self.color = kwargs.get("color")

        def add_field(self, name, value, inline=True):
            self.fields.append(types.SimpleNamespace(name=name, value=value, inline=inline))

        def set_footer(self, text=None):
            self.footer = text

    discord.Embed = MockEmbed
    discord.Client = type("Client", (), {})
    discord.Message = type("Message", (), {})
    discord.Attachment = type("Attachment", (), {})
    discord.Guild = type("Guild", (), {})
    discord.TextChannel = type("TextChannel", (), {})
    discord.Interaction = type("Interaction", (), {})
    discord.NotFound = type("NotFound", (Exception,), {})
    discord.HTTPException = type("HTTPException", (Exception,), {})

    class MockAllowedMentions:
        def __init__(self, **kwargs):
            self.everyone = kwargs.get("everyone", False)
            self.roles = kwargs.get("roles", False)
            self.users = kwargs.get("users", True)
            self.replied_user = kwargs.get("replied_user", True)

    class MockFile:
        def __init__(self, fp, filename=None):
            self.fp = fp
            self.filename = filename

    discord.File = MockFile
    discord.AllowedMentions = MockAllowedMentions

    ui = types.ModuleType("discord.ui")

    class _Item:
        def __init_subclass__(cls, **kwargs):
            super().__init_subclass__()

        def __init__(self, *a, **k):
            if not hasattr(type(self), "callback"):
                self.callback = None
            self.value = ""
            self.default = k.get("default", "")
            self.label = k.get("label", "")
            self.emoji = k.get("emoji", "")
            self.custom_id = k.get("custom_id", "")
            self.style = k.get("style", None)
            self.children = list(a)
            self.content = a[0] if a and isinstance(a[0], str) else ""

        def add_item(self, item):
            self.children.append(item)
            return self

        def walk_children(self):
            for c in self.children:
                yield c
                if hasattr(c, "walk_children"):
                    yield from c.walk_children()

    for name in (
        "View", "LayoutView", "Container", "TextDisplay", "Separator", "ActionRow",
        "Modal", "TextInput", "ChannelSelect", "Select", "Button"
    ):
        setattr(ui, name, type(name, (_Item,), {}))

    def _select(*a, **k):
        def dec(fn):
            return fn
        return dec

    def _button(*a, **k):
        def dec(fn):
            return fn
        return dec

    ui.select = _select
    ui.button = _button
    discord.ui = ui
    sys.modules["discord"] = discord
    sys.modules["discord.ui"] = ui

from lib.features.chat_responder import (
    calculate_cost,
    LiveChatManager,
    build_system_prompt,
    parse_duration_str,
    resolve_channel_input,
    ChatbotWakeModal,
    ChatbotDashboardView,
    ChatbotDirectPauseButton,
    ONE_OFF_SYSTEM_PROMPT,
    gather_one_off_context,
    generate_one_off_reply,
    handle_one_off_owner_mention,
    handle_chat_message,
    live_chat_manager,
    sanitize_ai_mentions,
    is_openai_refusal,
    strip_search_citations,
    looks_like_live_query,
    parse_openai_response_output,
    is_flat_decline,
    _handled_one_off_message_ids,
    looks_like_image_request,
    extract_image_prompt,
    can_user_generate_image,
    record_user_image_generation,
    generate_image_openai,
)
from config import USERS


def _responses_body(text, *, annotations=None, search_calls=0, refusal=None, usage=(120, 45), status="completed"):
    """Build a raw OpenAI Responses API JSON body like the one generate_one_off_reply parses."""
    output = [{"type": "web_search_call", "id": f"ws_{i}", "status": "completed"} for i in range(search_calls)]
    content = []
    if refusal is not None:
        content.append({"type": "refusal", "refusal": refusal})
    if text is not None:
        content.append({"type": "output_text", "text": text, "annotations": annotations or []})
    output.append({"type": "message", "id": "msg_1", "role": "assistant", "status": "completed", "content": content})
    return json.dumps({
        "id": "resp_1",
        "object": "response",
        "status": status,
        "error": None,
        "output": output,
        "usage": {"input_tokens": usage[0], "output_tokens": usage[1], "total_tokens": usage[0] + usage[1]},
    }).encode("utf-8")


def _mock_resp(body: bytes):
    m = MagicMock()
    m.read.return_value = body
    m.__enter__.return_value = m
    return m


def _sent_payload(mock_urlopen, index=-1):
    req = mock_urlopen.call_args_list[index][0][0]
    return json.loads(req.data.decode("utf-8"))



class TestLiveChatResponder(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        live_chat_manager.last_reply_time = 0
        live_chat_manager.stop(clear_target=True)
        live_chat_manager.owner_mentions_paused = False
        _handled_one_off_message_ids.clear()
        live_chat_manager.conversation_history.clear()

    def test_calculate_cost_gpt4o(self):
        # 1,000 prompt tokens = $0.0025, 1,000 completion tokens = $0.0100
        cost = calculate_cost("gpt-4o", 1000, 1000)
        self.assertAlmostEqual(cost, 0.0125, places=5)

    def test_calculate_cost_gpt4o_mini(self):
        # 10,000 prompt tokens = $0.0015, 10,000 completion tokens = $0.0060
        cost = calculate_cost("gpt-4o-mini", 10000, 10000)
        self.assertAlmostEqual(cost, 0.0075, places=5)

    @patch("lib.features.chat_responder.save_chatbot_usage")
    @patch("lib.features.chat_responder.load_chatbot_usage")
    def test_live_chat_manager_usage_and_embed(self, mock_load, mock_save):
        mock_load.return_value = {
            "total_cost_usd": 0.0500,
            "total_prompt_tokens": 20000,
            "total_completion_tokens": 2000,
            "total_tokens": 22000,
            "total_replies_count": 5,
        }

        mgr = LiveChatManager()
        self.assertFalse(mgr.active)
        self.assertEqual(mgr.session_cost_usd, 0.0)
        self.assertAlmostEqual(mgr.all_time_cost_usd, 0.0500, places=4)
        self.assertEqual(mgr.all_time_tokens, 22000)
        self.assertEqual(mgr.all_time_replies_count, 5)

        # Start session
        mgr.start(channel_id=959493057076666380, channel_name="General Chat", duration_seconds=600, topic="Test Topic")
        self.assertTrue(mgr.active)
        self.assertEqual(mgr.target_channel_id, 959493057076666380)

        # Record usage
        mgr.record_usage("gpt-4o", prompt_tokens=400, completion_tokens=50, is_reply=True)
        self.assertGreater(mgr.session_cost_usd, 0.0)
        self.assertEqual(mgr.session_replies_count, 1)
        self.assertEqual(mgr.total_tokens, 450)

        # All-time metrics should be incremented
        self.assertGreater(mgr.all_time_cost_usd, 0.0500)
        self.assertEqual(mgr.all_time_tokens, 22450)
        self.assertEqual(mgr.all_time_replies_count, 6)
        mock_save.assert_called()

        # Check status embed
        embed = mgr.get_status_embed()
        self.assertIn("Active & Responding", embed.fields[0].value)
        self.assertIn("Session Cost (Live)", embed.fields[3].name)
        self.assertIn("$", embed.fields[3].value)
        self.assertIn("Total Cost (All-Time)", embed.fields[4].name)
        self.assertIn("$0.05", embed.fields[4].value)
        self.assertIn("Test Topic", embed.fields[6].value)

        # Stop session
        prev_cost = mgr.session_cost_usd
        mgr.stop()
        self.assertFalse(mgr.active)

        # Check offline embed preserves last session cost and displays total cost
        offline_embed = mgr.get_status_embed()
        self.assertIn("Offline / Asleep", offline_embed.fields[0].value)
        self.assertIn("Last Session Cost", offline_embed.fields[3].name)
        self.assertIn(f"${prev_cost:.4f}", offline_embed.fields[3].value)
        self.assertIn("Total Cost (All-Time)", offline_embed.fields[4].name)

    def test_resolve_channel_input(self):
        cid, cname = resolve_channel_input("general")
        self.assertEqual(cname, "General Chat")

        cid, cname = resolve_channel_input("<#959493057076666380>")
        self.assertEqual(cid, 959493057076666380)

        cid, cname = resolve_channel_input("959508821951275069")
        self.assertEqual(cid, 959508821951275069)

        # Robust normalization tests
        cid, cname = resolve_channel_input("VIP Lounge")
        self.assertEqual(cname, "VIP Lounge")

        cid, cname = resolve_channel_input("💎 | vip-lounge")
        self.assertEqual(cname, "VIP Lounge")

        cid, cname = resolve_channel_input("vip-lounge")
        self.assertEqual(cname, "VIP Lounge")

        cid, cname = resolve_channel_input("House of Commons")
        self.assertEqual(cname, "House of Commons")

    def test_is_message_for_bot(self):
        from lib.features.chat_responder import is_message_for_bot

        client = MagicMock()
        client.user.id = 999999999

        # Direct name checks (vic, victor, victory, hms)
        for phrase in ("hi vic", "hi Vic", "hey victor", "tell them victory", "what do you think hms", "hms victory"):
            msg = MagicMock()
            msg.author.bot = False
            msg.mentions = []
            msg.reference = None
            msg.content = phrase
            self.assertTrue(is_message_for_bot(client, msg), f"Failed for phrase: {phrase}")

        # False positives should NOT trigger
        for false_phrase in ("victim of circumstance", "the conviction was overturned", "military service"):
            msg = MagicMock()
            msg.author.bot = False
            msg.mentions = []
            msg.reference = None
            msg.content = false_phrase
            self.assertFalse(is_message_for_bot(client, msg), f"Should not trigger for: {false_phrase}")

    def test_parse_user_id(self):
        from lib.features.chat_responder import parse_user_id
        self.assertEqual(parse_user_id("123456789012345678"), 123456789012345678)
        self.assertEqual(parse_user_id("<@123456789012345678>"), 123456789012345678)
        self.assertEqual(parse_user_id("<@!123456789012345678>"), 123456789012345678)
        self.assertEqual(parse_user_id(""), None)
        self.assertEqual(parse_user_id("none"), None)
        self.assertEqual(parse_user_id("invalid"), None)

    def test_defence_prompt(self):
        prompt_normal = build_system_prompt(is_defence=False)
        self.assertIn("deadpan British persona", prompt_normal)
        self.assertNotIn("TROLL-DEFENCE", prompt_normal)

        prompt_defence = build_system_prompt(is_defence=True)
        self.assertIn("TROLL-DEFENCE / ROAST MODE", prompt_defence)
        self.assertIn("defensively roast them", prompt_defence)

    def test_live_chat_manager_defence_target(self):
        mgr = LiveChatManager()
        self.assertIsNone(mgr.target_user_id)

        # Start with target user
        mgr.start(
            channel_id=959493057076666380,
            channel_name="General Chat",
            target_user_id=123456789,
        )
        self.assertEqual(mgr.target_user_id, 123456789)
        embed = mgr.get_status_embed()
        self.assertIn("<@123456789>", embed.fields[5].value)
        self.assertIn("Defence Mode ACTIVE", embed.fields[5].value)

        # Dynamically clear target user
        mgr.set_target_user(None)
        self.assertIsNone(mgr.target_user_id)
        embed_cleared = mgr.get_status_embed()
        self.assertIn("Standard Mode", embed_cleared.fields[5].value)

        mgr.stop()

    def test_chatbot_wake_modal_defaults(self):
        modal = ChatbotWakeModal(default_channel="vip-lounge", default_target_user="111222333")
        self.assertEqual(modal.channel_input.default, "vip-lounge")
        self.assertEqual(modal.target_user_input.default, "111222333")

    def test_chatbot_dashboard_view_components_v2(self):
        view = ChatbotDashboardView()
        self.assertGreater(len(view.children), 0)
        container = view.children[0]
        # Should have text displays, separators, and action rows
        children = list(container.children)
        self.assertGreater(len(children), 5)

    def test_one_off_system_prompt(self):
        self.assertIn("HMS Victory", ONE_OFF_SYSTEM_PROMPT)
        self.assertIn("Oggers", ONE_OFF_SYSTEM_PROMPT)
        self.assertIn("cynical", ONE_OFF_SYSTEM_PROMPT.lower())
        self.assertIn("poem", ONE_OFF_SYSTEM_PROMPT.lower())
        self.assertIn("TEMPORAL ANCHOR", ONE_OFF_SYSTEM_PROMPT)
        self.assertIn("TODAY'S REAL-WORLD DATE", ONE_OFF_SYSTEM_PROMPT)
        self.assertIn("DECLINING IN CHARACTER", ONE_OFF_SYSTEM_PROMPT)
        self.assertIn("FLY THE FLAG, QUIETLY", ONE_OFF_SYSTEM_PROMPT)
        from lib.features.chat_responder import BASE_SYSTEM_PROMPT
        self.assertIn("FLY THE FLAG, QUIETLY", BASE_SYSTEM_PROMPT)

    @patch("urllib.request.urlopen")
    def test_generate_one_off_reply_payload(self, mock_urlopen):
        mock_urlopen.return_value = _mock_resp(_responses_body("A witty poem about Johnny.", usage=(120, 45)))

        content, p_tok, c_tok = generate_one_off_reply(
            prompt="write a poem about this user",
            context="RECENT CHAT IN #general:\nJohnny: I hate tea",
            openai_key="test-key",
        )
        self.assertEqual(content, "A witty poem about Johnny.")
        self.assertEqual(p_tok, 120)
        self.assertEqual(c_tok, 45)

        # Verify the request hits the Responses API with the right shape
        req = mock_urlopen.call_args[0][0]
        self.assertEqual(req.full_url, "https://api.openai.com/v1/responses")
        self.assertEqual(req.get_header("Authorization"), "Bearer test-key")
        payload = json.loads(req.data.decode("utf-8"))
        self.assertEqual(payload["model"], "gpt-4o")
        self.assertEqual(payload["max_output_tokens"], 300)
        self.assertNotIn("messages", payload)
        self.assertNotIn("max_tokens", payload)
        self.assertIn("HMS Victory", payload["instructions"])
        self.assertIn("TODAY'S REAL-WORLD DATE", payload["instructions"])
        self.assertEqual(payload["input"][0]["role"], "user")
        text_part = payload["input"][0]["content"][0]
        self.assertEqual(text_part["type"], "input_text")
        self.assertIn("Johnny: I hate tea", text_part["text"])
        self.assertIn("write a poem about this user", text_part["text"])
        self.assertEqual(payload["tools"][0]["type"], "web_search")
        self.assertEqual(payload["tools"][0]["user_location"]["country"], "GB")
        # A poem is not a live query, so the model decides whether to search
        self.assertEqual(payload["tool_choice"], "auto")

    @patch("urllib.request.urlopen")
    def test_generate_one_off_reply_forces_search_for_live_queries(self, mock_urlopen):
        mock_urlopen.return_value = _mock_resp(_responses_body("Stevenage v Luton, 8pm.", search_calls=1))

        content, _, _ = generate_one_off_reply(prompt="what league one games are on today", openai_key="test-key")

        self.assertEqual(content, "Stevenage v Luton, 8pm.")
        payload = _sent_payload(mock_urlopen)
        self.assertEqual(payload["tool_choice"], "required")
        self.assertEqual(payload["tools"][0]["type"], "web_search")

    @patch("urllib.request.urlopen")
    def test_generate_one_off_reply_search_disabled(self, mock_urlopen):
        mock_urlopen.return_value = _mock_resp(_responses_body("No."))

        generate_one_off_reply(prompt="what's the weather today", openai_key="test-key", enable_search=False)

        payload = _sent_payload(mock_urlopen)
        self.assertNotIn("tools", payload)
        self.assertNotIn("tool_choice", payload)

    def test_looks_like_live_query(self):
        for q in [
            "what league one games are on today",
            "any scores?",
            "what's the weather like",
            "latest news on the strike",
            "who won last night",
            "when's kick off",
        ]:
            self.assertTrue(looks_like_live_query(q), q)
        for q in ["write a poem about Johnny", "roast this man", "wish Dave luck with his interview", ""]:
            self.assertFalse(looks_like_live_query(q), q)

    async def test_gather_one_off_context_reply_and_mentions(self):
        client = MagicMock()
        client.user.id = 999999999

        # Target user mentioned
        target_user = MagicMock()
        target_user.id = 12345
        target_user.name = "john_doe"
        target_user.nick = "Johnny"

        # Referenced message
        ref_author = MagicMock()
        ref_author.id = 54321
        ref_author.name = "steven_smith"
        ref_author.nick = "Steven"

        ref_message = MagicMock()
        ref_message.author = ref_author
        ref_message.content = "Beans on toast is overrated"
        ref_message.attachments = []

        ref = MagicMock()
        ref.message_id = 888888
        ref.cached_message = ref_message

        message = MagicMock()
        message.reference = ref
        message.mentions = [target_user, client.user]
        message.content = "write a poem about this user"
        message.channel.name = "general"

        # Channel history
        hist_msg = MagicMock()
        hist_msg.id = 777777
        hist_msg.author = ref_author
        hist_msg.content = "Beans on toast is overrated"
        hist_msg.attachments = []

        async def async_history(*a, **k):
            yield hist_msg

        message.channel.history = async_history

        # Scheduled events
        mock_event = MagicMock()
        mock_event.name = "Pub Quiz"
        mock_event.status = 1  # scheduled
        mock_event.url = "https://discord.com/events/123/456"
        mock_event.creator.display_name = "Chin"
        guild_mock = MagicMock()
        guild_mock.id = 123
        async def async_fetch_events():
            return [mock_event]
        guild_mock.fetch_scheduled_events = async_fetch_events
        message.guild = guild_mock

        context = await gather_one_off_context(client, message)
        self.assertIn("DIRECT REPLY TARGET", context)
        self.assertIn("Steven (@steven_smith)", context)
        self.assertIn("Beans on toast is overrated", context)
        self.assertIn("MENTIONED USERS IN PROMPT: Johnny (@john_doe)", context)
        self.assertIn("RECENT CHAT IN #general", context)
        self.assertIn("ACTIVE / UPCOMING SERVER EVENTS", context)
        self.assertIn("https://discord.com/events/123/456", context)

    @patch("lib.features.chat_responder.handle_one_off_owner_mention")
    async def test_handle_chat_message_oggers_direct_tag_asleep(self, mock_handle_one_off):
        mock_handle_one_off.return_value = True
        client = MagicMock()
        client.user.id = 999999999

        message = MagicMock()
        message.author.bot = False
        message.author.id = USERS.OGGERS
        message.mentions = [client.user]
        message.content = f"<@{client.user.id}> write a poem about this user"

        live_chat_manager.active = False
        res = await handle_chat_message(client, message)
        self.assertTrue(res)
        mock_handle_one_off.assert_called_once_with(client, message)

    @patch("lib.features.chat_responder.handle_one_off_owner_mention")
    async def test_handle_chat_message_oggers_name_drop_asleep_ignored(self, mock_handle_one_off):
        """Owner messages that merely say the bot's name (no @mention) must not summon it."""
        mock_handle_one_off.return_value = True
        client = MagicMock()
        client.user.id = 999999999

        for content in ["hi vic", "if you dont want to do it yourself i can get vic to dm her x", "hms victory is broken"]:
            message = MagicMock()
            message.author.bot = False
            message.author.id = USERS.OGGERS
            message.mentions = []
            message.reference = None
            message.content = content

            live_chat_manager.active = False
            res = await handle_chat_message(client, message)
            self.assertFalse(res, content)
        mock_handle_one_off.assert_not_called()

    @patch("lib.features.chat_responder.handle_one_off_owner_mention")
    async def test_handle_chat_message_oggers_reply_to_bot_asleep_ignored(self, mock_handle_one_off):
        """A reply to one of the bot's messages without a ping is not an @mention either."""
        mock_handle_one_off.return_value = True
        client = MagicMock()
        client.user.id = 999999999

        bot_msg = MagicMock()
        bot_msg.author.id = client.user.id
        ref = MagicMock()
        ref.message_id = 4242
        ref.cached_message = bot_msg

        message = MagicMock()
        message.author.bot = False
        message.author.id = USERS.OGGERS
        message.mentions = []
        message.reference = ref
        message.content = "lol"

        live_chat_manager.active = False
        res = await handle_chat_message(client, message)
        self.assertFalse(res)
        mock_handle_one_off.assert_not_called()

    @patch("lib.features.chat_responder.handle_one_off_owner_mention")
    async def test_handle_chat_message_oggers_raw_tag_without_mentions_list(self, mock_handle_one_off):
        """A raw <@ID> in the text counts even if the mentions list is empty."""
        mock_handle_one_off.return_value = True
        client = MagicMock()
        client.user.id = 999999999

        message = MagicMock()
        message.author.bot = False
        message.author.id = USERS.OGGERS
        message.mentions = []
        message.reference = None
        message.content = f"<@!{client.user.id}> dm her"

        live_chat_manager.active = False
        res = await handle_chat_message(client, message)
        self.assertTrue(res)
        mock_handle_one_off.assert_called_once_with(client, message)

    @patch("lib.features.chat_responder.handle_one_off_owner_mention")
    async def test_handle_chat_message_roshy_direct_tag_asleep(self, mock_handle_one_off):
        mock_handle_one_off.return_value = True
        client = MagicMock()
        client.user.id = 999999999

        message = MagicMock()
        message.author.bot = False
        message.author.id = USERS.ROSHY
        message.mentions = [client.user]
        message.content = f"<@{client.user.id}> it's not a she it's an it"

        live_chat_manager.active = False
        live_chat_manager.owner_mentions_paused = False
        res = await handle_chat_message(client, message)
        self.assertTrue(res)
        mock_handle_one_off.assert_called_once_with(client, message)

    @patch("lib.features.chat_responder.handle_one_off_owner_mention")
    async def test_handle_chat_message_hadidas_direct_tag_asleep(self, mock_handle_one_off):
        mock_handle_one_off.return_value = True
        client = MagicMock()
        client.user.id = 999999999

        message = MagicMock()
        message.author.bot = False
        message.author.id = USERS.HADIDAS
        message.mentions = [client.user]
        message.content = f"<@{client.user.id}> status update"

        live_chat_manager.active = False
        live_chat_manager.owner_mentions_paused = False
        res = await handle_chat_message(client, message)
        self.assertTrue(res)
        mock_handle_one_off.assert_called_once_with(client, message)

    @patch("lib.features.chat_responder.handle_one_off_owner_mention")
    async def test_handle_chat_message_other_user_asleep(self, mock_handle_one_off):
        client = MagicMock()
        client.user.id = 999999999

        message = MagicMock()
        message.author.bot = False
        message.author.id = 11223344  # Not Oggers, Roshy, or Hadidas
        message.mentions = [client.user]
        message.content = f"<@{client.user.id}> write a poem"

        live_chat_manager.active = False
        res = await handle_chat_message(client, message)
        self.assertFalse(res)
        mock_handle_one_off.assert_not_called()

    @patch("lib.features.chat_responder.generate_one_off_reply")
    @patch("lib.features.chat_responder.gather_one_off_context")
    async def test_handle_one_off_owner_mention_execution(self, mock_gather, mock_generate):
        mock_gather.return_value = "Recent chat context"
        mock_generate.return_value = ("Shall I compare thee to a soggy chip?", 100, 50)

        client = MagicMock()
        client.user.id = 999999999

        typing_mock = MagicMock()
        typing_mock.__aenter__ = MagicMock(side_effect=lambda: asyncio.sleep(0))
        typing_mock.__aexit__ = MagicMock(side_effect=lambda *a: asyncio.sleep(0))

        message = MagicMock()
        message.id = 1234567891011
        message.channel.typing.return_value = typing_mock
        message.author.id = USERS.OGGERS
        message.author.nick = "Oggers"
        message.content = f"<@{client.user.id}> write a poem about this user"

        reply_mock = MagicMock()
        async def async_reply(*a, **k):
            return MagicMock()
        message.reply = async_reply

        res = await handle_one_off_owner_mention(client, message)
        self.assertTrue(res)
        mock_gather.assert_called_once_with(client, message, return_targets=True)
        mock_generate.assert_called_once()
        self.assertEqual(mock_generate.call_args[1]["prompt"], "write a poem about this user")

    @patch("lib.features.chat_responder.generate_one_off_reply")
    @patch("lib.features.chat_responder.gather_one_off_context")
    async def test_handle_one_off_owner_mention_target_user_tagging(self, mock_gather, mock_generate):
        mock_gather.return_value = ("Recent chat context", {"mahdi": 123456789})
        mock_generate.return_value = ("good luck, mahdi. hope you don't trip over your English.", 100, 20)

        client = MagicMock()
        client.user.id = 999999999

        typing_mock = MagicMock()
        typing_mock.__aenter__ = MagicMock(side_effect=lambda: asyncio.sleep(0))
        typing_mock.__aexit__ = MagicMock(side_effect=lambda *a: asyncio.sleep(0))

        sent_replies = []
        async def async_reply(content, **kwargs):
            sent_replies.append(content)
            return MagicMock()

        message = MagicMock()
        message.id = 9988776655
        message.channel.typing.return_value = typing_mock
        message.author.id = USERS.OGGERS
        message.author.nick = "Oggers"
        message.content = f"<@{client.user.id}> pls wish mahdi luck"
        message.reply = async_reply

        res = await handle_one_off_owner_mention(client, message)
        self.assertTrue(res)
        self.assertEqual(len(sent_replies), 1)
        # Should replace 'mahdi' with '<@123456789>'
        self.assertIn("<@123456789>", sent_replies[0])
        self.assertNotIn("mahdi.", sent_replies[0])


    @patch("lib.features.chat_responder.generate_ai_reply")
    async def test_handle_chat_message_other_user_active(self, mock_generate_ai):
        mock_generate_ai.return_value = ("pipe down, mate.", 20, 10)
        client = MagicMock()
        client.user.id = 999999999

        message = MagicMock()
        message.author.bot = False
        message.author.id = 55667788  # e.g. Mahdi
        message.author.name = "mahdi"
        message.author.nick = "Mahdi"
        message.channel.id = 959493057076666380
        message.content = "hey vic"
        message.attachments = []
        message.reference = None

        typing_mock = MagicMock()
        typing_mock.__aenter__ = MagicMock(side_effect=lambda: asyncio.sleep(0))
        typing_mock.__aexit__ = MagicMock(side_effect=lambda *a: asyncio.sleep(0))
        message.channel.typing.return_value = typing_mock

        reply_mock = MagicMock()
        async def async_reply(*a, **k):
            return MagicMock()
        message.reply = async_reply

        # Start manager in this channel
        live_chat_manager.start(channel_id=959493057076666380, channel_name="General Chat")
        self.assertTrue(live_chat_manager.active)

        res = await handle_chat_message(client, message)
        self.assertTrue(res)
        mock_generate_ai.assert_called_once()
        live_chat_manager.stop()

    async def test_extract_image_urls(self):
        from lib.features.chat_responder import extract_image_urls

        msg = MagicMock()
        att1 = MagicMock()
        att1.content_type = "image/png"
        att1.filename = "kaizo.png"
        att1.url = "https://cdn.discordapp.com/attachments/123/456/kaizo.png"

        att2 = MagicMock()
        att2.content_type = "text/plain"
        att2.filename = "log.txt"
        att2.url = "https://cdn.discordapp.com/attachments/123/456/log.txt"

        msg.attachments = [att1, att2]
        msg.reference = None

        urls = await extract_image_urls(msg)
        self.assertEqual(urls, ["https://cdn.discordapp.com/attachments/123/456/kaizo.png"])

    @patch("urllib.request.urlopen")
    def test_generate_one_off_reply_multimodal_payload(self, mock_urlopen):
        mock_urlopen.return_value = _mock_resp(_responses_body("A witty critique of the meme.", usage=(150, 25)))

        content, p_tok, c_tok = generate_one_off_reply(
            prompt="what is in this image",
            context="",
            image_urls=["https://cdn.discordapp.com/attachments/123/456/kaizo.png"],
            openai_key="test-key",
        )
        self.assertEqual(content, "A witty critique of the meme.")
        self.assertEqual((p_tok, c_tok), (150, 25))

        parts = _sent_payload(mock_urlopen)["input"][0]["content"]
        self.assertEqual(parts[0]["type"], "input_text")
        self.assertIn("ATTACHED IMAGE", parts[0]["text"])
        self.assertEqual(parts[1]["type"], "input_image")
        self.assertEqual(parts[1]["image_url"], "https://cdn.discordapp.com/attachments/123/456/kaizo.png")

    @patch("urllib.request.urlopen")
    def test_generate_one_off_reply_refusal_retries_without_images(self, mock_urlopen):
        mock_urlopen.side_effect = [
            _mock_resp(_responses_body(None, refusal="I can't help with that image.", usage=(100, 5))),
            _mock_resp(_responses_body("Fine. It's a meme about tea.", usage=(80, 12))),
        ]

        content, p_tok, c_tok = generate_one_off_reply(
            prompt="what is this",
            image_urls=["https://cdn.discordapp.com/attachments/1/2/x.png"],
            openai_key="test-key",
        )

        self.assertEqual(content, "Fine. It's a meme about tea.")
        self.assertEqual((p_tok, c_tok), (180, 17))
        self.assertEqual(mock_urlopen.call_count, 2)
        first_types = [p["type"] for p in _sent_payload(mock_urlopen, 0)["input"][0]["content"]]
        second_types = [p["type"] for p in _sent_payload(mock_urlopen, 1)["input"][0]["content"]]
        self.assertEqual(first_types, ["input_text", "input_image"])
        self.assertEqual(second_types, ["input_text"])

    @patch("time.sleep")
    @patch("urllib.request.urlopen")
    def test_generate_one_off_reply_failed_status_falls_back(self, mock_urlopen, _sleep):
        body = json.dumps({
            "status": "failed",
            "error": {"message": "boom"},
            "output": [],
            "usage": {"input_tokens": 10, "output_tokens": 0},
        }).encode("utf-8")
        mock_urlopen.return_value = _mock_resp(body)

        content, p_tok, c_tok = generate_one_off_reply(prompt="hi", openai_key="test-key")

        self.assertIn("overwhelmed my processors", content)
        self.assertEqual(mock_urlopen.call_count, 2)
        self.assertEqual((p_tok, c_tok), (20, 0))

    @patch("urllib.request.urlopen")
    def test_generate_one_off_reply_native_search_strips_citations(self, mock_urlopen):
        bbc = "https://www.bbc.co.uk/sport/football/fixtures?utm_source=openai"
        sky = "https://www.skysports.com/league-one-table?utm_source=openai"
        text = (
            f"Stevenage host Luton at 20:00 tonight ([bbc.co.uk]({bbc})). "
            f"Wycombe are top of the table ([skysports.com]({sky}))."
        )
        annotations = [
            {"type": "url_citation", "start_index": 38, "end_index": 100, "url": bbc, "title": "Fixtures"},
            {"type": "url_citation", "start_index": 130, "end_index": 200, "url": sky, "title": "Table"},
        ]
        mock_urlopen.return_value = _mock_resp(
            _responses_body(text, annotations=annotations, search_calls=1, usage=(400, 60))
        )

        content, p_tok, c_tok = generate_one_off_reply(
            prompt="what league one games are on tonight?",
            openai_key="test-key",
        )

        self.assertEqual(content, "Stevenage host Luton at 20:00 tonight. Wycombe are top of the table.")
        self.assertEqual((p_tok, c_tok), (400, 60))
        # Native search is a single round trip: no follow-up completion, no scraper call
        self.assertEqual(mock_urlopen.call_count, 1)

    def test_is_flat_decline(self):
        for flat in [
            "I can't identify people from images.",
            "I'm sorry, but I cannot identify who this is.",
            "I'm unable to determine who that is.",
            "Sorry, I can't help with identifying individuals in photos.",
            "I can't assist with that request.",
            "Unfortunately I'm not able to verify who this person is.",
        ]:
            self.assertTrue(is_flat_decline(flat), flat)
        for fine in [
            "I can't believe you've asked me that. It's a grey smudge behind LinkedIn's paywall, mate.",
            "Chin, you absolute donut.",
            "Stevenage host Luton at 20:00 tonight.",
            "",
            "   ",
        ]:
            self.assertFalse(is_flat_decline(fine), fine)

    @patch("urllib.request.urlopen")
    def test_generate_one_off_reply_rewrites_flat_decline(self, mock_urlopen):
        mock_urlopen.side_effect = [
            _mock_resp(_responses_body("I can't identify people from images.", usage=(300, 10))),
            _mock_resp(_responses_body("It's a grey smudge behind LinkedIn's paywall, mate. Even I can't unblur a premium upsell.", usage=(120, 25))),
        ]

        content, p_tok, c_tok = generate_one_off_reply(
            prompt="find out who this is",
            context="RECENT CHAT IN #general:\nroshyrowe: Someone viewed my profile. A great mystery",
            image_urls=["https://cdn.discordapp.com/attachments/1/2/blur.png"],
            openai_key="test-key",
        )

        self.assertEqual(content, "It's a grey smudge behind LinkedIn's paywall, mate. Even I can't unblur a premium upsell.")
        self.assertEqual((p_tok, c_tok), (420, 35))
        self.assertEqual(mock_urlopen.call_count, 2)

        rewrite = _sent_payload(mock_urlopen, 1)
        self.assertNotIn("tools", rewrite)
        self.assertIn("declined", rewrite["instructions"])
        parts = rewrite["input"][0]["content"]
        self.assertEqual([p["type"] for p in parts], ["input_text"])
        self.assertIn("I can't identify people from images.", parts[0]["text"])
        self.assertIn("find out who this is", parts[0]["text"])
        self.assertIn("roshyrowe", parts[0]["text"])

    @patch("time.sleep")
    @patch("urllib.request.urlopen")
    def test_generate_one_off_reply_keeps_decline_when_rewrite_fails(self, mock_urlopen, _sleep):
        mock_urlopen.side_effect = [
            _mock_resp(_responses_body("I can't identify people from images.", usage=(300, 10))),
            OSError("network down"),
        ]

        content, p_tok, c_tok = generate_one_off_reply(prompt="find out who this is", openai_key="test-key")

        self.assertEqual(content, "I can't identify people from images.")
        self.assertEqual((p_tok, c_tok), (300, 10))
        self.assertEqual(mock_urlopen.call_count, 2)

    @patch("urllib.request.urlopen")
    def test_generate_one_off_reply_in_character_reply_not_rewritten(self, mock_urlopen):
        mock_urlopen.return_value = _mock_resp(_responses_body("I can't believe you've asked me that. It's a grey smudge, mate."))

        content, _, _ = generate_one_off_reply(prompt="find out who this is", openai_key="test-key")

        self.assertEqual(content, "I can't believe you've asked me that. It's a grey smudge, mate.")
        self.assertEqual(mock_urlopen.call_count, 1)

    # ---- Intent classifier & target resolution ----

    @patch("urllib.request.urlopen")
    def test_classify_mention_intent_parses_json(self, mock_urlopen):
        from lib.features.chat_responder import classify_mention_intent

        body = json.dumps({"intent": "generate", "subject": "mentioned", "subject_name": None, "reason": "portrait of a tagged user"})
        mock_urlopen.return_value = _mock_resp(_responses_body(body, usage=(210, 30)))

        res = classify_mention_intent(
            "generate what you think @Steven looks like based on his message history, focus on what i reckon",
            mentioned_names=["Steven <3"],
            caller_name="Oggers",
            has_recent_bot_image=True,
            recent_bot_image_prompt="A tea-drinking Brit in a pub",
            openai_key="test-key",
        )

        self.assertEqual(res["intent"], "generate")
        self.assertEqual(res["subject"], "mentioned")
        self.assertIsNone(res["subject_name"])
        self.assertEqual((res["input_tokens"], res["output_tokens"]), (210, 30))

        payload = _sent_payload(mock_urlopen)
        self.assertEqual(payload["model"], "gpt-4o-mini")
        self.assertEqual(payload["text"]["format"]["type"], "json_schema")
        self.assertTrue(payload["text"]["format"]["strict"])
        self.assertNotIn("tools", payload)
        sent_text = payload["input"][0]["content"][0]["text"]
        self.assertIn("MENTIONED USERS: Steven <3", sent_text)
        self.assertIn("BOT POSTED AN IMAGE RECENTLY: yes", sent_text)
        self.assertIn("A tea-drinking Brit in a pub", sent_text)
        self.assertIn("focus on what i reckon", sent_text)

    @patch("time.sleep")
    @patch("urllib.request.urlopen")
    def test_classify_mention_intent_retries_once_on_5xx(self, mock_urlopen, _sleep):
        from lib.features.chat_responder import classify_mention_intent
        import urllib.error

        body = json.dumps({"intent": "generate", "subject": "mentioned", "subject_name": None, "reason": "ok"})
        mock_urlopen.side_effect = [
            urllib.error.HTTPError("https://api.openai.com/v1/responses", 500, "Internal Server Error", {}, io.BytesIO(b'{"error":{"message":"boom"}}')),
            _mock_resp(_responses_body(body)),
        ]

        res = classify_mention_intent("generate what you think @Steven looks like", openai_key="test-key")

        self.assertEqual(res["intent"], "generate")
        self.assertEqual(mock_urlopen.call_count, 2)

    @patch("time.sleep")
    @patch("urllib.request.urlopen")
    def test_classify_mention_intent_no_retry_on_4xx(self, mock_urlopen, _sleep):
        from lib.features.chat_responder import classify_mention_intent
        import urllib.error

        mock_urlopen.side_effect = urllib.error.HTTPError("https://api.openai.com/v1/responses", 400, "Bad Request", {}, io.BytesIO(b'{"error":{"message":"bad schema"}}'))

        self.assertIsNone(classify_mention_intent("draw me", openai_key="test-key"))
        self.assertEqual(mock_urlopen.call_count, 1)

    @patch("time.sleep")
    @patch("urllib.request.urlopen")
    def test_generate_image_openai_retries_on_5xx(self, mock_urlopen, _sleep):
        from lib.features.chat_responder import generate_image_openai
        import urllib.error, base64

        ok = _mock_resp(json.dumps({"data": [{"b64_json": base64.b64encode(b"png-bytes").decode()}], "usage": {"input_tokens": 12, "output_tokens": 300}}).encode())
        mock_urlopen.side_effect = [
            urllib.error.HTTPError("https://api.openai.com/v1/images/generations", 500, "Internal Server Error", {}, io.BytesIO(b"oops")),
            urllib.error.HTTPError("https://api.openai.com/v1/images/generations", 502, "Bad Gateway", {}, io.BytesIO(b"")),
            ok,
        ]

        img, p_tok, c_tok = generate_image_openai("a cat", openai_key="test-key")

        self.assertEqual(img, b"png-bytes")
        self.assertEqual((p_tok, c_tok), (12, 300))
        self.assertEqual(mock_urlopen.call_count, 3)

    @patch("time.sleep")
    @patch("urllib.request.urlopen")
    def test_generate_image_openai_no_retry_on_4xx_or_timeout(self, mock_urlopen, _sleep):
        from lib.features.chat_responder import generate_image_openai
        import urllib.error, socket

        mock_urlopen.side_effect = urllib.error.HTTPError("https://api.openai.com/v1/images/generations", 400, "Bad Request", {}, io.BytesIO(b'{"error":{"message":"safety"}}'))
        with self.assertRaises(urllib.error.HTTPError):
            generate_image_openai("a cat", openai_key="test-key")
        self.assertEqual(mock_urlopen.call_count, 1)

        mock_urlopen.reset_mock()
        mock_urlopen.side_effect = urllib.error.URLError(socket.timeout("timed out"))
        with self.assertRaises(urllib.error.URLError):
            generate_image_openai("a cat", openai_key="test-key")
        self.assertEqual(mock_urlopen.call_count, 1)

    @patch("time.sleep")
    @patch("urllib.request.urlopen")
    def test_generate_image_openai_gives_up_after_three_5xx(self, mock_urlopen, _sleep):
        from lib.features.chat_responder import generate_image_openai
        import urllib.error

        mock_urlopen.side_effect = urllib.error.HTTPError("https://api.openai.com/v1/images/generations", 503, "Unavailable", {}, io.BytesIO(b""))
        with self.assertRaises(urllib.error.HTTPError):
            generate_image_openai("a cat", openai_key="test-key")
        self.assertEqual(mock_urlopen.call_count, 3)

    def test_classify_image_failure(self):
        from lib.features.chat_responder import classify_image_failure
        import urllib.error, socket

        self.assertEqual(classify_image_failure(urllib.error.HTTPError("u", 500, "x", {}, io.BytesIO(b""))), "outage")
        self.assertEqual(classify_image_failure(urllib.error.HTTPError("u", 429, "x", {}, io.BytesIO(b""))), "outage")
        self.assertEqual(classify_image_failure(urllib.error.HTTPError("u", 400, "x", {}, io.BytesIO(b'{"error":{"code":"moderation_blocked"}}'))), "rejected")
        self.assertEqual(classify_image_failure(urllib.error.HTTPError("u", 400, "x", {}, io.BytesIO(b'{"error":{"message":"invalid size"}}'))), "unknown")
        self.assertEqual(classify_image_failure(urllib.error.URLError(socket.timeout("t"))), "outage")
        self.assertEqual(classify_image_failure(RuntimeError("no image data")), "unknown")

    @patch("urllib.request.urlopen")
    def test_generate_image_failure_excuse(self, mock_urlopen):
        from lib.features.chat_responder import generate_image_failure_excuse

        mock_urlopen.return_value = _mock_resp(_responses_body("The ship's painter has downed tools over Steven's face. Try again after his tea break.", usage=(90, 25)))

        text, p_tok, c_tok = generate_image_failure_excuse(
            "generate what you think @Steven looks like", "Oggers", "server owner", target_name="Steven <3", failure_reason="outage", openai_key="test-key",
        )

        self.assertIn("ship's painter", text)
        self.assertEqual((p_tok, c_tok), (90, 25))
        payload = _sent_payload(mock_urlopen)
        self.assertNotIn("tools", payload)
        sent = payload["input"][0]["content"][0]["text"]
        self.assertIn("FAILURE REASON: outage", sent)
        self.assertIn("SUBJECT OF THE IMAGE: Steven <3", sent)
        self.assertIn("NEVER mention APIs", payload["instructions"])

    @patch("lib.features.chat_responder.generate_image_failure_excuse")
    @patch("lib.features.chat_responder.generate_image_openai")
    @patch("lib.features.chat_responder.synthesize_contextual_image_prompt")
    @patch("lib.features.chat_responder.fetch_user_recent_chat_async", new_callable=AsyncMock, return_value=[])
    @patch("lib.features.chat_responder.find_recent_image_attachment", new_callable=AsyncMock, return_value=None)
    @patch("lib.features.chat_responder.classify_mention_intent")
    async def test_handle_one_off_image_failure_gets_witty_excuse(
        self, mock_classify, mock_find_img, mock_fetch_chat, mock_synth, mock_gen_img, mock_excuse
    ):
        import urllib.error
        mock_classify.return_value = {"intent": "generate", "subject": "caller", "subject_name": None, "reason": "", "input_tokens": 0, "output_tokens": 0}
        mock_synth.return_value = ("A portrait of Oggers", "<@1> Behold.", 10, 5)
        mock_gen_img.side_effect = urllib.error.HTTPError("u", 503, "Unavailable", {}, io.BytesIO(b""))
        mock_excuse.return_value = ("The Admiralty has requisitioned my paints. Ask again after they've finished squabbling.", 80, 20)

        client = MagicMock()
        client.user.id = 999999999
        message = self._leader_message(client, f"<@{client.user.id}> draw me as an admiral")

        with patch("lib.features.chat_responder.can_user_generate_image", return_value=(True, 999999)), \
             patch("lib.features.chat_responder.record_user_image_generation") as mock_record, \
             patch("lib.features.chat_responder.live_chat_manager.update_dashboard", new_callable=AsyncMock):
            res = await handle_one_off_owner_mention(client, message)

        self.assertTrue(res)
        mock_record.assert_not_called()
        self.assertEqual(mock_excuse.call_args[1]["failure_reason"], "outage")
        self.assertEqual(mock_excuse.call_args[1]["target_name"], "ogme01")
        reply = message.reply.call_args[0][0]
        self.assertTrue(reply.startswith(f"<@{USERS.OGGERS}> "))
        self.assertIn("requisitioned my paints", reply)
        self.assertNotIn("file", message.reply.call_args[1])

        # If the excuse call itself dies, the canned line still goes out
        mock_excuse.side_effect = RuntimeError("down")
        message2 = self._leader_message(client, f"<@{client.user.id}> draw me as a pirate")
        with patch("lib.features.chat_responder.can_user_generate_image", return_value=(True, 999999)), \
             patch("lib.features.chat_responder.record_user_image_generation"), \
             patch("lib.features.chat_responder.live_chat_manager.update_dashboard", new_callable=AsyncMock):
            await handle_one_off_owner_mention(client, message2)
        self.assertIn("canvas broke", message2.reply.call_args[0][0])

    def test_is_substantive_message(self):
        from lib.features.chat_responder import is_substantive_message
        for junk in ["", "No", "Tf", "<:hips:1146858165414154303>", "<@1171842947440967770>", "https://x.com/a", "<:a:1> <:b:2> 😂", "ok"]:
            self.assertFalse(is_substantive_message(junk), junk)
        for fine in ["Corrrr", "I love Bradley Walsh he's so handsome", "Vic loves me", "I am not human I'm an alien <:PepeHands:1>"]:
            self.assertTrue(is_substantive_message(fine), fine)

    @patch("database.DatabaseManager.fetch_all")
    def test_fetch_user_recent_chat_spread(self, mock_fetch):
        from lib.features.chat_responder import fetch_user_recent_chat

        recent = [
            ("100", "I mentioned Jaffa cakes once like 4 hours ago", None, 1000),
            ("100", "No", None, 999),
            ("100", "<:hips:1146858165414154303>", None, 998),
            ("100", "Lamborghini energy drink?", None, 997),
            ("100", "You can buy mashed potato on temu", None, 996),
            ("100", "Corrrr", None, 995),
        ]
        older = [
            ("100", "I love Bradley Walsh he's so handsome", None, 500),
            ("100", "I am not human I'm an alien", None, 300),
            ("100", "Nahhh I love my pet she keeps herself entertained", None, 400),
        ]
        mock_fetch.side_effect = [recent, older]

        res = fetch_user_recent_chat(None, 479207279850291221, limit=3, spread=True, older_sample=30)

        self.assertEqual(mock_fetch.call_count, 2)
        first_q, first_params = mock_fetch.call_args_list[0][0]
        self.assertEqual(first_params, ("479207279850291221", 6))
        second_q, second_params = mock_fetch.call_args_list[1][0]
        self.assertIn("RANDOM()", second_q)
        self.assertEqual(second_params, ("479207279850291221", 995, 30))

        contents = [r["content"] for r in res]
        # Oldest first; emoji-only and two-letter junk dropped; only the 3 newest substantive recent
        # messages kept (limit=3), so "Corrrr" falls off; the older random sample all kept.
        self.assertEqual(contents, [
            "I am not human I'm an alien",
            "Nahhh I love my pet she keeps herself entertained",
            "I love Bradley Walsh he's so handsome",
            "You can buy mashed potato on temu",
            "Lamborghini energy drink?",
            "I mentioned Jaffa cakes once like 4 hours ago",
        ])

    @patch("database.DatabaseManager.fetch_all")
    def test_fetch_user_recent_chat_default_is_unchanged(self, mock_fetch):
        from lib.features.chat_responder import fetch_user_recent_chat
        mock_fetch.return_value = [("100", "No", None, 10), ("100", "<:hips:1>", None, 9)]
        res = fetch_user_recent_chat(None, 1, limit=10)
        self.assertEqual(mock_fetch.call_count, 1)
        self.assertEqual([r["content"] for r in res], ["No", "<:hips:1>"])

    @patch("database.DatabaseManager.fetch_all")
    def test_fetch_user_bot_interactions(self, mock_fetch):
        from lib.features.chat_responder import fetch_user_bot_interactions, format_bot_interactions_for_context

        user_rows = [
            ("Make it a image you dummy vic", 900),
            ("the service is down again", 850),        # 'vic' inside 'service': not addressed to the bot
            ("<@1171842947440967770> who is the best server member", 800),
            ("<@1171842947440967770>", 780),               # bare ping: dropped
            ("Vic loves me", 700),
        ]
        bot_rows = [("<@479207279850291221> You are the ship's barnacle, Steven.", 750)]
        mock_fetch.side_effect = [user_rows, bot_rows]

        res = fetch_user_bot_interactions(479207279850291221, bot_id=1171842947440967770, limit=40)

        self.assertEqual([r["content"][:12] for r in res], ["Vic loves me", "<@4792072798", "<@1171842947", "Make it a im"])
        self.assertEqual([r["speaker"] for r in res], ["user", "bot", "user", "user"])
        section = format_bot_interactions_for_context("Steven", 479207279850291221, res)
        self.assertIn("HISTORY BETWEEN Steven (<@479207279850291221>) AND HMS VICTORY", section)
        self.assertIn("- HMS Victory: <@479207279850291221> You are the ship's barnacle", section)
        self.assertIn("- Steven: Vic loves me", section)

    def test_prompt_references_bot(self):
        from lib.features.chat_responder import prompt_references_bot
        for yes in [
            "generate a cartoon strip of  <@479207279850291221>interacting with you (HMS Victory), based on your history together",
            "draw steven with you",
            "you and him having a pint",
            "a photo of the two of you",
            "steven vs vic boxing match",
        ]:
            self.assertTrue(prompt_references_bot(yes), yes)
        for yes in [
            "can you generate an image to express your thoughts on oggers resetting your memory",
            "draw how you feel about being shut down every night",
            "paint a self portrait",
            "picture of yourself as a pirate",
        ]:
            self.assertTrue(prompt_references_bot(yes), yes)
        for no in ["can you draw steven", "what do you think of steven", "draw me as an admiral", "generate a portrait of <@555> based on his message history"]:
            self.assertFalse(prompt_references_bot(no), no)

    def test_recent_image_prompts_from_history(self):
        from lib.features.chat_responder import recent_image_prompts_from_history
        hist = [
            {"role": "assistant", "content": "[Generated Image: A tea-drinking Brit]"},
            {"role": "user", "content": "lol"},
            {"role": "assistant", "content": "Plain text reply"},
            {"role": "assistant", "content": "[Edited Image: Same Brit, balder]"},
            {"role": "assistant", "content": "[Generated Image: Steven with Jaffa cakes]"},
        ]
        self.assertEqual(recent_image_prompts_from_history(2, hist), ["Steven with Jaffa cakes", "Same Brit, balder"])
        self.assertEqual(recent_image_prompts_from_history(5, []), [])

    @patch("urllib.request.urlopen")
    def test_synthesize_contextual_image_prompt_splits_image_and_caption(self, mock_urlopen):
        from lib.features.chat_responder import synthesize_contextual_image_prompt

        def chat(body):
            return _mock_resp(json.dumps({"choices": [{"message": {"content": json.dumps(body)}}], "usage": {"prompt_tokens": 100, "completion_tokens": 30}}).encode())
        mock_urlopen.side_effect = [chat({"image_prompt": "A four-panel comic strip..."}), chat({"caption": "Behold."})]

        img_prompt, caption, p_tok, c_tok = synthesize_contextual_image_prompt(
            prompt="generate a cartoon strip of steven interacting with you",
            context="HISTORY BETWEEN Steven (<@1>) AND HMS VICTORY (1 messages):\n- Steven: Vic loves me",
            user_name="Oggers", caller_role="server owner", target_name="Steven",
            openai_key="test-key",
            previous_image_prompts=["Steven in a Jaffa Cakes Fanatic t-shirt holding a Lamborghini energy drink"],
        )

        self.assertEqual((img_prompt, caption), ("A four-panel comic strip...", "Behold."))
        self.assertEqual((p_tok, c_tok), (200, 60))

        # Call 1: image prompt, no persona anywhere in it
        image_call = _sent_payload(mock_urlopen, 0)
        system = image_call["messages"][0]["content"]
        self.assertNotIn("HMS Victory,", system)
        self.assertNotIn("You are HMS Victory", system)
        self.assertIn("HONOUR THE REQUESTED FORMAT", system)
        self.assertIn("3 or 4 sequential panels", system)
        self.assertIn("RECURRING themes", system)
        self.assertIn("BANNED", system)
        self.assertIn("GROUP PICTURES", system)
        self.assertIn("never render made-up usernames", system)
        user = image_call["messages"][1]["content"]
        self.assertIn("HMS VICTORY IS IN THE PICTURE: yes", user)
        self.assertIn("PREVIOUS IMAGES ALREADY PRODUCED", user)
        self.assertIn("Jaffa Cakes Fanatic", user)
        self.assertIn("Vic loves me", user)

        self.assertIn("FLY THE FLAG, QUIETLY", system)
        self.assertIn("Britain is never the loser", system)

        # Call 2: caption, persona on, given the finished image prompt
        caption_call = _sent_payload(mock_urlopen, 1)
        self.assertIn("You are HMS Victory", caption_call["messages"][0]["content"])
        self.assertIn("sides with Britain", caption_call["messages"][0]["content"])
        self.assertIn("THE IMAGE SHOWS: A four-panel comic strip...", caption_call["messages"][1]["content"])

    @patch("urllib.request.urlopen")
    def test_synthesize_contextual_image_prompt_plain_request_keeps_bot_out(self, mock_urlopen):
        from lib.features.chat_responder import synthesize_contextual_image_prompt

        def chat(body):
            return _mock_resp(json.dumps({"choices": [{"message": {"content": json.dumps(body)}}], "usage": {}}).encode())
        mock_urlopen.side_effect = [chat({"image_prompt": "A man with a coffee"}), OSError("caption service down")]

        img_prompt, caption, _, _ = synthesize_contextual_image_prompt(
            prompt="based on <@111> message history, what do you think he looks like",
            context="", user_name="Oggers", caller_role="server owner", target_name="Kaiz", openai_key="test-key",
        )

        self.assertEqual(img_prompt, "A man with a coffee")
        self.assertIn("HMS VICTORY IS IN THE PICTURE: no", _sent_payload(mock_urlopen, 0)["messages"][1]["content"])
        # caption call died: canned caption, image prompt still returned
        self.assertEqual(caption, "Here is your image. Try not to strain your eyes.")

    @patch("lib.features.chat_responder.build_user_dossier", return_value=None)
    @patch("lib.features.chat_responder.fetch_user_bot_interactions_async", new_callable=AsyncMock)
    @patch("lib.features.chat_responder.fetch_user_chat_sample_async", new_callable=AsyncMock)
    async def test_ensure_target_history_adds_sample_and_exchanges(self, mock_sample, mock_exchanges, _dossier):
        from lib.features.chat_responder import ensure_target_history_in_context

        mock_sample.return_value = [{"content": "I love Bradley Walsh", "channel": "general", "ts": 1}]
        mock_exchanges.return_value = [{"speaker": "user", "content": "Vic loves me", "ts": 2}]
        client = MagicMock()
        message = MagicMock()

        ctx = await ensure_target_history_in_context(
            client, message, "RECENT MESSAGE HISTORY FOR Steven (<@555>) (1 messages):\n- [#general] Jaffa cakes",
            555, "Steven", prompt="a cartoon strip of steven with you, based on your history together", bot_id=42,
        )
        self.assertIn("MESSAGE HISTORY SAMPLED ACROSS THE LAST 30 DAYS FOR Steven (<@555>)", ctx)
        self.assertIn("I love Bradley Walsh", ctx)
        self.assertIn("HISTORY BETWEEN Steven (<@555>) AND HMS VICTORY", ctx)
        self.assertIn("Jaffa cakes", ctx)  # original context kept
        self.assertEqual(mock_exchanges.call_args[0][:2], (555, 42))

        mock_exchanges.reset_mock()
        ctx2 = await ensure_target_history_in_context(client, message, "", 555, "Steven", prompt="draw steven as a pirate", bot_id=42)
        self.assertIn("SAMPLED ACROSS", ctx2)
        self.assertNotIn("HISTORY BETWEEN", ctx2)
        mock_exchanges.assert_not_called()

        # No target: untouched
        self.assertEqual(await ensure_target_history_in_context(client, message, "ctx", None, None), "ctx")

    def test_looks_like_group_request(self):
        from lib.features.chat_responder import looks_like_group_request, resolve_image_target
        for yes in [
            "Can you generate an image that represents the various members of ukplace",
            "draw everyone here as pirates",
            "a group photo of the server",
            "paint the lads down the pub",
        ]:
            self.assertTrue(looks_like_group_request(yes), yes)
        for no in ["draw me", "what does steven look like", "generate a portrait of <@1>"]:
            self.assertFalse(looks_like_group_request(no), no)
        self.assertEqual(resolve_image_target("draw the members", 1, "Oggers", [], {}, subject="group"), (None, None))

    @patch("database.DatabaseManager.fetch_all")
    def test_fetch_most_active_users(self, mock_fetch):
        from lib.features.chat_responder import fetch_most_active_users
        mock_fetch.return_value = [("285860055570579457", 3774), ("1171842947440967770", 900), ("404634271861571584", 3052), ("bad", 1)]
        res = fetch_most_active_users(days=30, limit=2, exclude_ids=[1171842947440967770])
        self.assertEqual(res, [(285860055570579457, 3774), (404634271861571584, 3052)])
        q, params = mock_fetch.call_args[0]
        self.assertIn("GROUP BY user_id", q)
        self.assertEqual(params[1], 3)

    @patch("lib.features.chat_responder.build_user_dossier", return_value=None)
    @patch("lib.features.chat_responder.fetch_user_recent_chat")
    @patch("lib.features.chat_responder.fetch_most_active_users")
    async def test_build_group_roster_context(self, mock_active, mock_chat, _dossier):
        from lib.features.chat_responder import build_group_roster_context

        def member(uid, name, bot=False):
            m = MagicMock(); m.id = uid; m.nick = name; m.global_name = None; m.display_name = name; m.name = name.lower(); m.bot = bot
            return m
        johnny, oggers, steven, somebot = member(1, "Johnny"), member(2, "oggers"), member(3, "Steven <3"), member(4, "Claude AI", bot=True)
        guild = MagicMock(); guild.name = "ukplace"
        guild.get_member.side_effect = lambda uid: {1: johnny, 2: oggers, 3: steven, 4: somebot}.get(uid)
        mock_active.return_value = [(4, 5000), (1, 3774), (2, 3052), (99, 2000), (3, 2083)]
        mock_chat.side_effect = lambda client, uid, ch, limit, spread, older: [{"content": f"msg from {uid}", "ts": 1}]
        calls = []
        real_side = mock_chat.side_effect
        mock_chat.side_effect = lambda *a: (calls.append(a), real_side(*a))[1]

        roster = await build_group_roster_context(None, guild, must_include=[steven], max_members=3, bot_id=777)

        self.assertIn("SERVER MEMBER ROSTER FOR ukplace (these 3 people are the ONLY people who may appear", roster)
        # mentioned user first, then most active humans; the bot and the unresolvable id 99 skipped
        self.assertEqual(roster.index("MEMBER: Steven <3 (<@3>)") < roster.index("MEMBER: Johnny (<@1>)") < roster.index("MEMBER: oggers (<@2>)"), True)
        self.assertNotIn("Claude AI", roster)
        self.assertNotIn("<@99>", roster)
        self.assertIn("  - msg from 1", roster)
        self.assertEqual(mock_active.call_args[0][2], [777, 3])
        # spread sample per member: 10 recent + 30 older across the archive window
        self.assertEqual(calls[0][3:], (10, True, 30))
        self.assertIn("sampled across the last 30 days", roster)

    @patch("lib.features.chat_responder.generate_image_openai")
    @patch("lib.features.chat_responder.synthesize_contextual_image_prompt")
    @patch("lib.features.chat_responder.build_group_roster_context", new_callable=AsyncMock)
    @patch("lib.features.chat_responder.fetch_user_bot_interactions_async", new_callable=AsyncMock, return_value=[])
    @patch("lib.features.chat_responder.fetch_user_chat_sample_async", new_callable=AsyncMock, return_value=[])
    @patch("lib.features.chat_responder.fetch_user_recent_chat_async", new_callable=AsyncMock, return_value=[])
    @patch("lib.features.chat_responder.find_recent_image_attachment", new_callable=AsyncMock, return_value=None)
    @patch("lib.features.chat_responder.classify_mention_intent")
    async def test_handle_one_off_group_request_uses_roster(
        self, mock_classify, mock_find_img, mock_fetch_chat, _sample, _exchanges, mock_roster, mock_synth, mock_gen_img
    ):
        mock_classify.return_value = {"intent": "generate", "subject": "group", "subject_name": None, "reason": "server members", "input_tokens": 0, "output_tokens": 0}
        mock_roster.return_value = "SERVER MEMBER ROSTER FOR ukplace (these 2 people are the ONLY people who may appear):\n\nMEMBER: Johnny (<@1>)\n  - up the pompey"
        mock_synth.return_value = ("Johnny and oggers at a pub quiz", "<@1> Behold the regulars.", 10, 5)
        mock_gen_img.return_value = (b"img", 20, 200)

        client = MagicMock()
        client.user.id = 999999999
        message = self._leader_message(client, f"<@{client.user.id}> Can you generate an image that represents the various members of ukplace", author_id=USERS.HADIDAS)
        message.guild = MagicMock(); message.guild.name = "ukplace"

        with patch("lib.features.chat_responder.can_user_generate_image", return_value=(True, 3)), \
             patch("lib.features.chat_responder.record_user_image_generation"), \
             patch("lib.features.chat_responder.live_chat_manager.update_dashboard", new_callable=AsyncMock):
            res = await handle_one_off_owner_mention(client, message)

        self.assertTrue(res)
        mock_roster.assert_called_once()
        self.assertIn("SERVER MEMBER ROSTER", mock_synth.call_args[1]["context"])
        self.assertEqual(mock_synth.call_args[1]["target_name"], "the ukplace regulars")
        mock_gen_img.assert_called_once_with("Johnny and oggers at a pub quiz")

    @patch("lib.features.chat_responder.fetch_most_active_users")
    async def test_build_server_overview_context(self, mock_active):
        from lib.features import chat_responder as cr

        def member(uid, name, bot=False):
            m = MagicMock(); m.id = uid; m.nick = name; m.global_name = None; m.display_name = name; m.name = name.lower(); m.bot = bot
            return m
        guild = MagicMock(); guild.id = 4242; guild.name = "ukplace"; guild.member_count = 1480
        guild.get_member.side_effect = lambda uid: {1: member(1, "Johnny"), 2: member(2, "oggers"), 4: member(4, "Claude AI", bot=True)}.get(uid)
        mock_active.return_value = [(4, 9000), (1, 3774), (2, 3052), (99, 500)]
        cr._SERVER_OVERVIEW_CACHE.clear()

        text = await cr.build_server_overview_context(None, guild, bot_id=777, limit=10)

        self.assertIn("SERVER OVERVIEW: ukplace, 1480 members.", text)
        self.assertIn("Johnny (<@1>, 3774 msgs); oggers (<@2>, 3052 msgs)", text)
        self.assertNotIn("Claude AI", text)
        self.assertNotIn("<@99>", text)
        self.assertIn("never invent members", text)

        # cached: second call doesn't hit the archive again
        await cr.build_server_overview_context(None, guild, bot_id=777)
        self.assertEqual(mock_active.call_count, 1)
        cr._SERVER_OVERVIEW_CACHE.clear()
        self.assertEqual(await cr.build_server_overview_context(None, None), "")

    @patch("lib.features.chat_responder.build_server_overview_context", new_callable=AsyncMock, return_value="SERVER OVERVIEW: ukplace, 10 members.")
    async def test_gather_one_off_context_includes_server_overview(self, mock_overview):
        client = MagicMock(); client.user.id = 999
        message = MagicMock()
        message.reference = None
        message.mentions = []
        message.content = "who is shark daddy"
        message.author.id = USERS.OGGERS
        message.channel.name = "general"
        async def async_history(*a, **k):
            if False:
                yield None
        message.channel.history = async_history
        message.guild = MagicMock(); message.guild.id = 1
        async def no_events():
            return []
        message.guild.fetch_scheduled_events = no_events

        context = await gather_one_off_context(client, message)
        self.assertIn("SERVER OVERVIEW: ukplace, 10 members.", context)
        mock_overview.assert_called_once()

    def test_appearance_directives(self):
        from lib.features.chat_responder import appearance_directives
        a1 = appearance_directives(479207279850291221)
        a2 = appearance_directives(479207279850291221)
        b = appearance_directives(404634271861571584)
        self.assertEqual(a1, a2)                      # same person, same base look every time
        self.assertNotEqual(a1, b)                    # different people differ
        self.assertIn("Physical base", a1)
        self.assertIn("Art style if none was requested", a1)
        self.assertIn("infer gender", a1)
        from lib.features.chat_responder import APPEARANCE_POOLS
        self.assertFalse(any("photo" in st or "realistic" in st for st in APPEARANCE_POOLS["style"]))
        group = appearance_directives(1, include_physical=False)
        self.assertNotIn("Physical base", group)
        self.assertIn("Art style", group)

    @patch("urllib.request.urlopen")
    def test_synthesize_image_prompt_gets_variety_directives(self, mock_urlopen):
        from lib.features.chat_responder import synthesize_contextual_image_prompt, appearance_directives

        def chat(body):
            return _mock_resp(json.dumps({"choices": [{"message": {"content": json.dumps(body)}}], "usage": {}}).encode())
        mock_urlopen.side_effect = [chat({"image_prompt": "x"}), chat({"caption": "y"})]
        synthesize_contextual_image_prompt(
            prompt="what does he look like", context="", user_name="Oggers", caller_role="server owner",
            target_name="Lanca", target_id=555, openai_key="test-key",
        )
        user = _sent_payload(mock_urlopen, 0)["messages"][1]["content"]
        self.assertIn(appearance_directives(555), user)
        system = _sent_payload(mock_urlopen, 0)["messages"][0]["content"]
        self.assertIn("Never the stock cartoon lead", system)
        self.assertIn("at most two short labels", system)
        self.assertIn("NEVER photorealistic", system)
        self.assertIn("A CARICATURE FOR A ROAST, NOT A PORTRAIT", system)
        self.assertIn('"gag"', system)
        self.assertIn('"supporting_references"', system)
        self.assertIn("PACK THE SCENE with 4-6 SUPPORTING REFERENCES", system)
        self.assertIn("LOOKS COME FROM THE MESSAGES FIRST", system)
        self.assertIn('"character_sheet"', system)
        self.assertIn("tie-breaker ONLY", user)
        self.assertIn("DEDUCE: commit to a specific, plausible look", system)

        # A response carrying the sheet still yields the image prompt
        mock_urlopen.side_effect = [chat({"character_sheet": {"gender": "male (name)", "style": "gig poster"}, "image_prompt": "a screen-printed gig poster of..."}), chat({"caption": "y"})]
        img, _, _, _ = synthesize_contextual_image_prompt(
            prompt="what does he look like", context="", user_name="Oggers", caller_role="server owner",
            target_name="Kaiz", target_id=556, openai_key="test-key",
        )
        self.assertEqual(img, "a screen-printed gig poster of...")

        # group: style directives only, no physical base
        mock_urlopen.side_effect = [chat({"image_prompt": "x"}), chat({"caption": "y"})]
        synthesize_contextual_image_prompt(
            prompt="draw the members", context="", user_name="Oggers", caller_role="server owner",
            target_name="the ukplace regulars", is_group=True, openai_key="test-key",
        )
        user = _sent_payload(mock_urlopen, 4)["messages"][1]["content"]
        self.assertIn("VARIETY DIRECTIVES (tie-breaker ONLY, for character-sheet fields you can neither evidence nor deduce): Art style", user)
        self.assertIn("Every listed person appears, each with their own gag", user)
        system = _sent_payload(mock_urlopen, 4)["messages"][0]["content"]
        self.assertIn("EVERY ONE OF THEM MUST APPEAR", system)
        self.assertIn('"characters": [', system)
        self.assertNotIn("Physical base", user)

    @patch("urllib.request.urlopen")
    def test_synthesize_group_prompt_parses_characters_and_scales_budget(self, mock_urlopen):
        from lib.features.chat_responder import synthesize_contextual_image_prompt
        def chat(body):
            return _mock_resp(json.dumps({"choices": [{"message": {"content": json.dumps(body)}}], "usage": {}}).encode())
        mock_urlopen.side_effect = [chat({"characters": [{"name": "Lanca", "gag": "waffles"}, {"name": "Gunner", "gag": "Oxford"}], "scene": "kitchen fire", "image_prompt": "Lanca and Gunner..."}), chat({"caption": "y"})]
        roster = "SERVER MEMBER ROSTER FOR ukplace (these 2 people...):\n\nMEMBER: Lanca (<@1>)\nDOSSIER...\n\nMEMBER: Gunner (<@2>)\nDOSSIER..."
        img, _, _, _ = synthesize_contextual_image_prompt(prompt="image of <@1> and <@2>", context=roster, user_name="Hadidas", caller_role="deputy",
                                                          target_name="Lanca and Gunner", is_group=True, openai_key="test-key")
        self.assertEqual(img, "Lanca and Gunner...")
        self.assertEqual(_sent_payload(mock_urlopen, 0)["max_tokens"], 550 + 160)

    @patch("urllib.request.urlopen")
    def test_classify_mention_intent_sees_recent_exchanges_and_correction_rule(self, mock_urlopen):
        from lib.features.chat_responder import classify_mention_intent, recent_one_off_exchanges

        hist = [
            {"role": "user", "speaker": "oggers", "content": "based on <@1> message history, what do you think they look like. do it as a cartoon"},
            {"role": "assistant", "speaker": "HMS Victory", "content": "[Generated Image: A gouache cartoon of a young woman cuddling a tabby cat]"},
        ]
        transcript = recent_one_off_exchanges(4, hist)
        self.assertEqual(transcript.splitlines()[0][:8], "oggers: ")
        self.assertIn("HMS Victory: [Generated Image: A gouache cartoon", transcript)

        body = json.dumps({"intent": "edit", "subject": "named", "subject_name": "Pengrin", "reason": "correction"})
        mock_urlopen.return_value = _mock_resp(_responses_body(body))
        res = classify_mention_intent("the cat is black", caller_name="Oggers", has_recent_bot_image=True,
                                      recent_bot_image_prompt="A gouache cartoon...", recent_history=transcript, openai_key="test-key")
        self.assertEqual(res["intent"], "edit")
        payload = _sent_payload(mock_urlopen)
        self.assertIn("RECENT CHAT:\noggers: based on", payload["input"][0]["content"][0]["text"])
        self.assertIn('"the cat is black", "the green one"', payload["instructions"])

    def test_own_attachment_image_urls(self):
        from lib.features.chat_responder import own_attachment_image_urls
        def att(fn, ct, url):
            a = MagicMock(); a.filename = fn; a.content_type = ct; a.url = url; return a
        msg = MagicMock()
        msg.attachments = [att("cat.png", "image/png", "https://cdn/cat.png"), att("notes.txt", "text/plain", "https://cdn/notes.txt"), att("x.JPG", "", "https://cdn/x.JPG")]
        self.assertEqual(own_attachment_image_urls(msg), ["https://cdn/cat.png", "https://cdn/x.JPG"])
        msg.attachments = []
        self.assertEqual(own_attachment_image_urls(msg), [])

    @patch("urllib.request.urlopen")
    def test_synthesize_image_prompt_with_reference_images(self, mock_urlopen):
        from lib.features.chat_responder import synthesize_contextual_image_prompt

        def chat(body):
            return _mock_resp(json.dumps({"choices": [{"message": {"content": json.dumps(body)}}], "usage": {}}).encode())
        mock_urlopen.side_effect = [chat({"image_prompt": "a black cat with a white bib..."}), chat({"caption": "y"})]

        synthesize_contextual_image_prompt(
            prompt="regenerate your previous cartoon of <@1> using this cat as reference", context="", user_name="Oggers",
            caller_role="server owner", target_name="Pengrin", target_id=1, openai_key="test-key",
            reference_image_urls=["https://cdn.discordapp.com/attachments/1/2/cat.png"],
        )
        image_call = _sent_payload(mock_urlopen, 0)
        content = image_call["messages"][1]["content"]
        self.assertIsInstance(content, list)
        self.assertEqual(content[0]["type"], "text")
        self.assertIn("REFERENCE IMAGES ATTACHED BY THE REQUESTER: 1", content[0]["text"])
        self.assertEqual(content[1], {"type": "image_url", "image_url": {"url": "https://cdn.discordapp.com/attachments/1/2/cat.png"}})
        self.assertIn("REFERENCE IMAGES", image_call["messages"][0]["content"])
        # caption call stays text-only
        self.assertIsInstance(_sent_payload(mock_urlopen, 1)["messages"][1]["content"], str)

    @patch("urllib.request.urlopen")
    def test_synthesize_image_edit_prompt_with_reference_images(self, mock_urlopen):
        from lib.features.chat_responder import synthesize_image_edit_prompt
        mock_urlopen.return_value = _mock_resp(json.dumps({
            "choices": [{"message": {"content": json.dumps({"edit_type": "edit", "image_prompt": "make the cat black with a white bib", "caption": "Done."})}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }).encode())
        synthesize_image_edit_prompt("the cat is black", prev_prompt="A cartoon with a tabby cat", openai_key="test-key",
                                     reference_image_urls=["https://cdn/cat.png"])
        content = _sent_payload(mock_urlopen)["messages"][1]["content"]
        self.assertIsInstance(content, list)
        self.assertEqual(content[1]["image_url"]["url"], "https://cdn/cat.png")
        self.assertIn("REFERENCE IMAGES ATTACHED", content[0]["text"])

    @patch("lib.features.chat_responder.generate_image_openai")
    @patch("lib.features.chat_responder.synthesize_contextual_image_prompt")
    @patch("lib.features.chat_responder.fetch_user_bot_interactions_async", new_callable=AsyncMock, return_value=[])
    @patch("lib.features.chat_responder.fetch_user_chat_sample_async", new_callable=AsyncMock, return_value=[])
    @patch("lib.features.chat_responder.fetch_user_recent_chat_async", new_callable=AsyncMock, return_value=[])
    @patch("lib.features.chat_responder.find_recent_image_attachment", new_callable=AsyncMock, return_value=None)
    @patch("lib.features.chat_responder.classify_mention_intent")
    async def test_handle_one_off_passes_reference_attachment_to_synth(
        self, mock_classify, mock_find_img, mock_fetch_chat, _sample, _exchanges, mock_synth, mock_gen_img
    ):
        mock_classify.return_value = {"intent": "generate", "subject": "mentioned", "subject_name": None, "reason": "", "input_tokens": 0, "output_tokens": 0}
        mock_synth.return_value = ("a black cat", "<@1> There.", 10, 5)
        mock_gen_img.return_value = (b"img", 20, 200)

        client = MagicMock(); client.user.id = 999999999
        pengrin = MagicMock(); pengrin.id = 5; pengrin.nick = "Pengrin"; pengrin.global_name = None; pengrin.display_name = "Pengrin"; pengrin.name = "pengrin"
        message = self._leader_message(client, f"<@{client.user.id}> regenerate your previous cartoon of <@5> using this cat as reference", mentions=[pengrin])
        att = MagicMock(); att.filename = "cat.png"; att.content_type = "image/png"; att.url = "https://cdn/cat.png"
        message.attachments = [att]

        with patch("lib.features.chat_responder.can_user_generate_image", return_value=(True, 999999)), \
             patch("lib.features.chat_responder.record_user_image_generation"), \
             patch("lib.features.chat_responder.live_chat_manager.update_dashboard", new_callable=AsyncMock):
            res = await handle_one_off_owner_mention(client, message)

        self.assertTrue(res)
        self.assertEqual(mock_synth.call_args[1]["reference_image_urls"], ["https://cdn/cat.png"])
        self.assertTrue(mock_classify.call_args[1]["has_attached_image"])

    @patch("urllib.request.urlopen")
    def test_synthesize_image_prompt_no_subject_uses_chat_situation(self, mock_urlopen):
        from lib.features.chat_responder import synthesize_contextual_image_prompt

        def chat(body):
            return _mock_resp(json.dumps({"choices": [{"message": {"content": json.dumps(body)}}], "usage": {}}).encode())
        mock_urlopen.side_effect = [chat({"image_prompt": "x"}), chat({"caption": "y"})]
        synthesize_contextual_image_prompt(
            prompt="can you generate an image to express your thoughts on oggers resetting your memory",
            context="RECENT CHAT IN #general:\noggers: dont worry im setting up the lobotomy table",
            user_name="Hadidas", caller_role="deputy prime minister", target_name=None, openai_key="test-key",
        )
        user = _sent_payload(mock_urlopen, 0)["messages"][1]["content"]
        self.assertIn("SUBJECT: no specific person", user)
        self.assertIn("RECENT CHAT in the context is the source", user)
        self.assertIn("HMS VICTORY IS IN THE PICTURE: yes", user)
        self.assertNotIn("Physical base", user)          # no person, no seeded looks
        self.assertIn("lobotomy table", user)

        mock_urlopen.side_effect = [chat({"image_prompt": "x"}), chat({"caption": "y"})]
        synthesize_contextual_image_prompt(prompt="what does he look like", context="", user_name="Oggers", caller_role="server owner",
                                           target_name="Lanca", target_id=5, openai_key="test-key")
        user = _sent_payload(mock_urlopen, 2)["messages"][1]["content"]
        self.assertIn("SUBJECT: Lanca. Their history is the source.", user)
        self.assertIn("their mum, dad, nan", user)
        self.assertIn("Physical base", user)

    @patch("lib.features.chat_responder.generate_image_openai")
    @patch("lib.features.chat_responder.synthesize_contextual_image_prompt")
    @patch("lib.features.chat_responder.gather_one_off_context", new_callable=AsyncMock)
    @patch("lib.features.chat_responder.find_recent_image_attachment", new_callable=AsyncMock, return_value=None)
    @patch("lib.features.chat_responder.classify_mention_intent")
    async def test_handle_one_off_no_subject_still_synthesises_with_context(
        self, mock_classify, mock_find_img, mock_gather, mock_synth, mock_gen_img
    ):
        """'your thoughts on oggers resetting your memory' must go through the synthesiser with the chat, not raw to the image model."""
        mock_classify.return_value = {"intent": "generate", "subject": "none", "subject_name": None, "reason": "", "input_tokens": 0, "output_tokens": 0}
        mock_gather.return_value = ("RECENT CHAT IN #general:\noggers: dont worry im setting up the lobotomy table", {})
        mock_synth.return_value = ("A weathered warship strapped to an operating table...", "<@1> Behold my impending lobotomy.", 10, 5)
        mock_gen_img.return_value = (b"img", 20, 200)

        client = MagicMock(); client.user.id = 999999999
        message = self._leader_message(client, f"<@{client.user.id}> can you generate an image to express your thoughts on oggers resetting your memory", author_id=USERS.HADIDAS)

        with patch("lib.features.chat_responder.can_user_generate_image", return_value=(True, 3)), \
             patch("lib.features.chat_responder.record_user_image_generation"), \
             patch("lib.features.chat_responder.live_chat_manager.update_dashboard", new_callable=AsyncMock):
            res = await handle_one_off_owner_mention(client, message)

        self.assertTrue(res)
        mock_synth.assert_called_once()
        self.assertIn("lobotomy table", mock_synth.call_args[1]["context"])
        self.assertIsNone(mock_synth.call_args[1]["target_name"])
        mock_gen_img.assert_called_once_with("A weathered warship strapped to an operating table...")
        self.assertIn("impending lobotomy", message.reply.call_args[0][0])
        self.assertNotIn("strain your eyes", message.reply.call_args[0][0])

    @patch("database.DatabaseManager.fetch_all")
    def test_fetch_user_messages_bulk_spreads_evenly(self, mock_fetch):
        from lib.features.chat_responder import fetch_user_messages_bulk
        rows = [(f"message number {i} about things", 1000 + i) for i in range(100)] + [("<:hips:1>", 2000), ("ok", 2001)]
        mock_fetch.return_value = rows
        res = fetch_user_messages_bulk(1, days=30, limit=10)
        self.assertEqual(len(res), 10)
        self.assertEqual(res[0]["content"], "message number 0 about things")
        self.assertEqual(res[-1]["content"], "message number 90 about things")
        self.assertTrue(all("hips" not in r["content"] for r in res))

    @patch("database.DatabaseManager.fetch_all")
    def test_fetch_mentions_of_user(self, mock_fetch):
        from lib.features.chat_responder import fetch_mentions_of_user
        mock_fetch.return_value = [("2", "tharan is a pub geezer", 900), ("3", "<@1>", 850), ("4", "<@1> stop", 800)]
        res = fetch_mentions_of_user(1, ["Tharan", "th"], days=30, limit=10)
        self.assertEqual([r["content"] for r in res], ["tharan is a pub geezer", "<@1> stop"])
        q, params = mock_fetch.call_args[0]
        self.assertIn("user_id != ?", q)
        self.assertEqual(params[0], "1")
        self.assertIn("%<@1>%", params)
        self.assertIn("%tharan%", params)
        self.assertNotIn("%th%", params)  # too short to be a useful alias

    @patch("lib.features.chat_responder._save_dossier_cache")
    @patch("lib.features.chat_responder.fetch_mentions_of_user")
    @patch("lib.features.chat_responder.fetch_user_messages_bulk")
    @patch("urllib.request.urlopen")
    def test_build_user_dossier_and_cache(self, mock_urlopen, mock_bulk, mock_about, _save):
        from lib.features import chat_responder as cr
        cr._USER_DOSSIER_CACHE.clear()
        cr._dossier_cache_loaded = True
        mock_bulk.return_value = [{"content": f"msg {i} about pumpkin spice", "ts": i} for i in range(40)]
        mock_about.return_value = [{"author_id": "2", "content": "tharan is a pub geezer", "ts": 5}]
        mock_urlopen.return_value = _mock_resp(json.dumps({
            "choices": [{"message": {"content": json.dumps({
                "summary": "A pumpkin spice apologist.",
                "recurring_themes": ["pumpkin spice lattes: 'I understand the hype'"],
                "running_jokes": ["pub geezer"],
                "catchphrases": ["corrrr"],
                "what_others_say": ["<@2>: tharan is a pub geezer"],
                "notable_incidents": ["stair rail for two steps"],
                "look_clues": "none",
            })}}],
            "usage": {"prompt_tokens": 5000, "completion_tokens": 300},
        }).encode())

        with patch("lib.features.chat_responder.live_chat_manager.record_usage"):
            text = cr.build_user_dossier(1, "Tharan", openai_key="test-key")

        self.assertIn("DOSSIER ON Tharan (<@1>), distilled from 40 of their messages and 1 mentions", text)
        self.assertIn("Summary: A pumpkin spice apologist.", text)
        self.assertIn("Running jokes:\n  - pub geezer", text)
        self.assertIn("Notable incidents:\n  - stair rail for two steps", text)
        self.assertNotIn("Look clues", text)
        sent = _sent_payload(mock_urlopen)
        self.assertEqual(sent["model"], "gpt-4o-mini")
        self.assertIn("comedy dossier", sent["messages"][0]["content"])
        self.assertIn("msg 39 about pumpkin spice", sent["messages"][1]["content"])
        self.assertIn("<@2>: tharan is a pub geezer", sent["messages"][1]["content"])

        # cached: no second call
        text2 = cr.build_user_dossier(1, "Tharan", openai_key="test-key")
        self.assertEqual(text2, text)
        self.assertEqual(mock_urlopen.call_count, 1)

        # too little history: no dossier, no call
        mock_bulk.return_value = [{"content": "hi there mate", "ts": 1}]
        self.assertIsNone(cr.build_user_dossier(2, "Newbie", openai_key="test-key"))
        self.assertEqual(mock_urlopen.call_count, 1)
        cr._USER_DOSSIER_CACHE.clear()

    @patch("lib.features.chat_responder.fetch_user_bot_interactions_async", new_callable=AsyncMock, return_value=[])
    @patch("lib.features.chat_responder.fetch_user_chat_sample_async", new_callable=AsyncMock)
    @patch("lib.features.chat_responder.fetch_user_recent_chat_async", new_callable=AsyncMock)
    @patch("lib.features.chat_responder.build_user_dossier")
    async def test_ensure_target_history_prefers_dossier(self, mock_dossier, mock_recent, mock_sample, _ex):
        from lib.features.chat_responder import ensure_target_history_in_context
        mock_dossier.return_value = "DOSSIER ON Tharan (<@1>): pumpkin spice"
        mock_recent.return_value = [{"content": "just now: corrrr", "channel": "general", "ts": 1}]
        ctx = await ensure_target_history_in_context(MagicMock(), MagicMock(), "", 1, "Tharan", prompt="draw tharan")
        self.assertIn("DOSSIER ON Tharan", ctx)
        self.assertIn("THEIR MOST RECENT MESSAGES (for freshness) FOR Tharan", ctx)
        self.assertIn("just now: corrrr", ctx)
        mock_sample.assert_not_called()

        mock_dossier.return_value = None
        mock_sample.return_value = [{"content": "old msg", "channel": "general", "ts": 1}]
        ctx = await ensure_target_history_in_context(MagicMock(), MagicMock(), "", 1, "Tharan", prompt="draw tharan")
        self.assertIn("SAMPLED ACROSS THE LAST 30 DAYS", ctx)
        self.assertNotIn("DOSSIER", ctx)

    def test_delivery_mentions(self):
        from lib.features.chat_responder import delivery_mentions
        chin = MagicMock(); chin.id = 795
        danez = MagicMock(); danez.id = 412
        prompt = "turn <@795> into an animorph of a horse and send it to <@412>"
        self.assertEqual([u.id for u in delivery_mentions(prompt, [danez, chin], target_id=795)], [412])
        self.assertEqual(delivery_mentions("draw <@795> next to <@412>", [danez, chin], target_id=795), [])
        self.assertEqual(delivery_mentions(prompt, [danez, chin], target_id=412), [])

    @patch("lib.features.chat_responder.generate_image_openai")
    @patch("lib.features.chat_responder.synthesize_contextual_image_prompt")
    @patch("lib.features.chat_responder.fetch_user_bot_interactions_async", new_callable=AsyncMock, return_value=[])
    @patch("lib.features.chat_responder.fetch_user_chat_sample_async", new_callable=AsyncMock, return_value=[])
    @patch("lib.features.chat_responder.build_user_dossier", return_value=None)
    @patch("lib.features.chat_responder.fetch_user_recent_chat_async", new_callable=AsyncMock, return_value=[])
    @patch("lib.features.chat_responder.find_recent_image_attachment", new_callable=AsyncMock, return_value=None)
    @patch("lib.features.chat_responder.classify_mention_intent")
    async def test_handle_one_off_two_mentions_picks_subject_and_tags_recipient(
        self, mock_classify, mock_find_img, mock_fetch_chat, _dossier, _sample, _exchanges, mock_synth, mock_gen_img
    ):
        mock_classify.return_value = {"intent": "generate", "subject": "mentioned", "subject_name": "<@795>", "reason": "", "input_tokens": 0, "output_tokens": 0}
        mock_synth.return_value = ("Chin mid-morph into a horse", "Behold Chin, half horse.", 10, 5)
        mock_gen_img.return_value = (b"img", 20, 200)

        client = MagicMock(); client.user.id = 999999999
        chin = MagicMock(); chin.id = 795; chin.nick = "Chin"; chin.global_name = None; chin.display_name = "Chin"; chin.name = "chin"
        danez = MagicMock(); danez.id = 412; danez.nick = None; danez.global_name = "Danez"; danez.display_name = "Danez"; danez.name = "danez"
        message = self._leader_message(client, f"<@{client.user.id}> turn <@795> into an animorph of a horse and send it to <@412>", mentions=[danez, chin])

        with patch("lib.features.chat_responder.can_user_generate_image", return_value=(True, 999999)), \
             patch("lib.features.chat_responder.record_user_image_generation"), \
             patch("lib.features.chat_responder.live_chat_manager.update_dashboard", new_callable=AsyncMock):
            res = await handle_one_off_owner_mention(client, message)

        self.assertTrue(res)
        self.assertEqual(mock_synth.call_args[1]["target_name"], "Chin")
        self.assertEqual(mock_synth.call_args[1]["target_id"], 795)
        self.assertTrue(message.reply.call_args[0][0].startswith("<@412> Behold Chin"))

    def test_looks_like_random_pick(self):
        from lib.features.chat_responder import looks_like_random_pick, resolve_image_target
        for yes in ["can you choose one of the users in this channel at random and make them a fursona", "pick someone and draw them", "draw a random member as a pirate"]:
            self.assertTrue(looks_like_random_pick(yes), yes)
        for no in ["draw me", "draw <@1> as a random pirate"]:
            self.assertFalse(looks_like_random_pick(no), no)
        self.assertEqual(resolve_image_target("pick someone", 1, "Oggers", [], {}, subject="random"), (None, None))

    @patch("lib.features.chat_responder.fetch_most_active_users", return_value=[])
    @patch("lib.features.chat_responder.fetch_channel_active_users")
    async def test_pick_random_member_skips_bots_and_unknowns(self, mock_channel_users, _active):
        from lib.features.chat_responder import pick_random_member
        def member(uid, name, bot=False):
            m = MagicMock(); m.id = uid; m.nick = name; m.global_name = None; m.display_name = name; m.name = name.lower(); m.bot = bot
            return m
        guild = MagicMock()
        guild.get_member.side_effect = lambda uid: {1: member(1, "Johnny"), 4: member(4, "Claude AI", bot=True)}.get(uid)
        mock_channel_users.return_value = [4, 99, 1]
        for _ in range(5):
            self.assertEqual(await pick_random_member(None, guild, 123, bot_id=777), ("Johnny", 1))
        self.assertEqual(mock_channel_users.call_args[0][0], 123)
        self.assertIn(777, mock_channel_users.call_args[0][3])
        mock_channel_users.return_value = []
        self.assertEqual(await pick_random_member(None, guild, 123, bot_id=777), (None, None))

    @patch("lib.features.chat_responder.generate_image_openai")
    @patch("lib.features.chat_responder.synthesize_contextual_image_prompt")
    @patch("lib.features.chat_responder.pick_random_member", new_callable=AsyncMock, return_value=("Johnny", 797))
    @patch("lib.features.chat_responder.fetch_user_bot_interactions_async", new_callable=AsyncMock, return_value=[])
    @patch("lib.features.chat_responder.fetch_user_chat_sample_async", new_callable=AsyncMock, return_value=[])
    @patch("lib.features.chat_responder.build_user_dossier", return_value="DOSSIER ON Johnny (<@797>): pompey")
    @patch("lib.features.chat_responder.fetch_user_recent_chat_async", new_callable=AsyncMock, return_value=[])
    @patch("lib.features.chat_responder.find_recent_image_attachment", new_callable=AsyncMock, return_value=None)
    @patch("lib.features.chat_responder.classify_mention_intent")
    async def test_handle_one_off_random_subject(
        self, mock_classify, mock_find_img, mock_fetch_chat, _dossier, _sample, _exchanges, mock_pick, mock_synth, mock_gen_img
    ):
        mock_classify.return_value = {"intent": "generate", "subject": "random", "subject_name": None, "reason": "", "input_tokens": 0, "output_tokens": 0}
        mock_synth.return_value = ("Johnny as a Pompey-blue fox fursona", "Behold the fursona nobody asked for.", 10, 5)
        mock_gen_img.return_value = (b"img", 20, 200)

        client = MagicMock(); client.user.id = 999999999
        message = self._leader_message(client, f"<@{client.user.id}> can you choose one of the users in this channel at random and make them a fursona", author_id=USERS.HADIDAS)
        message.channel.id = 123

        with patch("lib.features.chat_responder.can_user_generate_image", return_value=(True, 3)), \
             patch("lib.features.chat_responder.record_user_image_generation"), \
             patch("lib.features.chat_responder.live_chat_manager.update_dashboard", new_callable=AsyncMock):
            res = await handle_one_off_owner_mention(client, message)

        self.assertTrue(res)
        self.assertEqual(mock_pick.call_args[0][2], 123)
        self.assertEqual(mock_synth.call_args[1]["target_name"], "Johnny")
        self.assertEqual(mock_synth.call_args[1]["target_id"], 797)
        self.assertIn("DOSSIER ON Johnny", mock_synth.call_args[1]["context"])
        self.assertTrue(message.reply.call_args[0][0].startswith("<@797> Behold the fursona"))

    def test_is_delegation_prompt(self):
        from lib.features.chat_responder import is_delegation_prompt
        for yes in ["pls do this", "do this", "this", "^", "please do that one", "can you do this pls", "what he said", "ok do it", "Do this!"]:
            self.assertTrue(is_delegation_prompt(yes), yes)
        for no in ["do this but make him bald", "draw me", "this is rubbish", "pls do a fursona of <@1>", ""]:
            self.assertFalse(is_delegation_prompt(no), no)

    @patch("urllib.request.urlopen")
    def test_classify_mention_intent_sees_replied_to_message(self, mock_urlopen):
        from lib.features.chat_responder import classify_mention_intent
        body = json.dumps({"intent": "generate", "subject": "mentioned", "subject_name": "Lou Skunt", "reason": "delegated"})
        mock_urlopen.return_value = _mock_resp(_responses_body(body))
        res = classify_mention_intent("pls do this", has_reply_ref=True, replied_to_text="based on <@1022>'s message history can you make him a fursona",
                                      replied_to_author="Hadidas", mentioned_names=["Lou Skunt"], openai_key="test-key")
        self.assertEqual(res["subject"], "mentioned")
        payload = _sent_payload(mock_urlopen)
        self.assertIn("THE MESSAGE BEING REPLIED TO (by Hadidas): \"based on <@1022>", payload["input"][0]["content"][0]["text"])
        self.assertIn('"pls do this"', payload["instructions"])
        self.assertIn('"add a pink mullet to this fine gentleman"', payload["instructions"])

    @patch("lib.features.chat_responder.generate_image_openai")
    @patch("lib.features.chat_responder.synthesize_contextual_image_prompt")
    @patch("lib.features.chat_responder.fetch_user_bot_interactions_async", new_callable=AsyncMock, return_value=[])
    @patch("lib.features.chat_responder.fetch_user_chat_sample_async", new_callable=AsyncMock, return_value=[])
    @patch("lib.features.chat_responder.build_user_dossier", return_value=None)
    @patch("lib.features.chat_responder.fetch_user_recent_chat_async", new_callable=AsyncMock, return_value=[])
    @patch("lib.features.chat_responder.find_recent_image_attachment", new_callable=AsyncMock, return_value=None)
    @patch("lib.features.chat_responder.classify_mention_intent")
    async def test_handle_one_off_pls_do_this_delegates_to_replied_message(
        self, mock_classify, mock_find_img, mock_fetch_chat, _dossier, _sample, _exchanges, mock_synth, mock_gen_img
    ):
        mock_classify.return_value = {"intent": "generate", "subject": "mentioned", "subject_name": "<@1022>", "reason": "", "input_tokens": 0, "output_tokens": 0}
        mock_synth.return_value = ("Lou Skunt as a skunk fursona", "Behold Lou.", 10, 5)
        mock_gen_img.return_value = (b"img", 20, 200)

        client = MagicMock(); client.user.id = 999999999
        lou = MagicMock(); lou.id = 1022; lou.nick = "Lou Skunt"; lou.global_name = None; lou.display_name = "Lou Skunt"; lou.name = "lou"
        hadidas = MagicMock(); hadidas.id = USERS.HADIDAS; hadidas.nick = "Hadidas"; hadidas.global_name = None; hadidas.display_name = "Hadidas"; hadidas.name = "hadidas"
        ref_msg = MagicMock()
        ref_msg.content = f"<@{client.user.id}> based on <@1022>'s message history can you make him a fursona"
        ref_msg.author = hadidas
        ref_msg.mentions = [client.user, lou]
        ref = MagicMock(); ref.message_id = 4242; ref.resolved = ref_msg

        message = self._leader_message(client, f"<@{client.user.id}> pls do this", reference=ref)

        with patch("lib.features.chat_responder.can_user_generate_image", return_value=(True, 999999)), \
             patch("lib.features.chat_responder.record_user_image_generation"), \
             patch("lib.features.chat_responder.live_chat_manager.update_dashboard", new_callable=AsyncMock):
            res = await handle_one_off_owner_mention(client, message)

        self.assertTrue(res)
        # classifier saw the replied-to request and its mention
        self.assertEqual(mock_classify.call_args[1]["replied_to_text"], "based on <@1022>'s message history can you make him a fursona")
        self.assertEqual(mock_classify.call_args[1]["mentioned_names"], ["Lou Skunt"])
        # and the portrait is of Lou, with the delegated request as the prompt
        self.assertEqual(mock_synth.call_args[1]["target_id"], 1022)
        self.assertIn("make him a fursona", mock_synth.call_args[1]["prompt"])
        mock_gen_img.assert_called_once_with("Lou Skunt as a skunk fursona")

    @patch("lib.features.chat_responder.generate_image_openai")
    @patch("lib.features.chat_responder.synthesize_contextual_image_prompt")
    @patch("lib.features.chat_responder.fetch_most_active_users", return_value=[(1, 9000), (2, 8000)])
    @patch("lib.features.chat_responder.fetch_user_recent_chat", return_value=[])
    @patch("lib.features.chat_responder.build_user_dossier", side_effect=lambda uid, name, *a, **k: f"DOSSIER ON {name} (<@{uid}>): stuff")
    @patch("lib.features.chat_responder.fetch_user_bot_interactions_async", new_callable=AsyncMock, return_value=[])
    @patch("lib.features.chat_responder.fetch_user_chat_sample_async", new_callable=AsyncMock, return_value=[])
    @patch("lib.features.chat_responder.fetch_user_recent_chat_async", new_callable=AsyncMock, return_value=[])
    @patch("lib.features.chat_responder.find_recent_image_attachment", new_callable=AsyncMock, return_value=None)
    @patch("lib.features.chat_responder.classify_mention_intent")
    async def test_handle_one_off_two_tagged_people_is_a_picture_of_exactly_them(
        self, mock_classify, mock_find_img, mock_fetch_chat, _sample, _exchanges, mock_dossier, _chat, mock_active, mock_synth, mock_gen_img
    ):
        mock_classify.return_value = {"intent": "generate", "subject": "mentioned", "subject_name": None, "reason": "", "input_tokens": 0, "output_tokens": 0}
        mock_synth.return_value = ("MJ and Markoos at the bar", "Behold the pair of them.", 10, 5)
        mock_gen_img.return_value = (b"img", 20, 200)

        client = MagicMock(); client.user.id = 999999999
        mj = MagicMock(); mj.id = 301; mj.nick = "MJ Rocker"; mj.global_name = None; mj.display_name = "MJ Rocker"; mj.name = "mj"; mj.bot = False
        markoos = MagicMock(); markoos.id = 302; markoos.nick = "Markoos(Potatoed)"; markoos.global_name = None; markoos.display_name = "Markoos(Potatoed)"; markoos.name = "markoos"; markoos.bot = False
        message = self._leader_message(client, f"<@{client.user.id}> based on their message history can you generate an image of <@301> and <@302>", author_id=USERS.HADIDAS, mentions=[markoos, mj])
        message.guild = MagicMock(); message.guild.name = "ukplace"

        with patch("lib.features.chat_responder.can_user_generate_image", return_value=(True, 3)), \
             patch("lib.features.chat_responder.record_user_image_generation"), \
             patch("lib.features.chat_responder.live_chat_manager.update_dashboard", new_callable=AsyncMock):
            res = await handle_one_off_owner_mention(client, message)

        self.assertTrue(res)
        kw = mock_synth.call_args[1]
        self.assertTrue(kw["is_group"])
        self.assertEqual(kw["target_name"], "Markoos(Potatoed) and MJ Rocker")
        self.assertIn("these 2 people are the ONLY people who may appear", kw["context"])
        self.assertIn("DOSSIER ON MJ Rocker (<@301>)", kw["context"])
        self.assertIn("DOSSIER ON Markoos(Potatoed) (<@302>)", kw["context"])
        self.assertNotIn("MEMBER: Johnny", kw["context"])   # no padding with the server's regulars

        # Many tagged people: all of them, in message order, none dropped
        people = []
        for i in range(8):
            u = MagicMock(); u.id = 400 + i; u.nick = f"P{i}"; u.global_name = None; u.display_name = f"P{i}"; u.name = f"p{i}"; u.bot = False
            people.append(u)
        tags = " ".join(f"<@{u.id}>" for u in people)
        message2 = self._leader_message(client, f"<@{client.user.id}> draw {tags} on a stag do", author_id=USERS.HADIDAS, mentions=list(reversed(people)))
        message2.guild = MagicMock(); message2.guild.name = "ukplace"
        with patch("lib.features.chat_responder.can_user_generate_image", return_value=(True, 3)), \
             patch("lib.features.chat_responder.record_user_image_generation"), \
             patch("lib.features.chat_responder.live_chat_manager.update_dashboard", new_callable=AsyncMock):
            await handle_one_off_owner_mention(client, message2)
        kw = mock_synth.call_args[1]
        self.assertIn("these 8 people are the ONLY people who may appear", kw["context"])
        for u in people:
            self.assertIn(f"DOSSIER ON {u.nick} (<@{u.id}>)", kw["context"])
        self.assertEqual(kw["target_name"], "P7, P6, P5, P4, P3, P2, P1 and P0")

    @patch("urllib.request.urlopen")
    def test_chat_completion_null_content_is_a_refusal(self, mock_urlopen):
        from lib.features.chat_responder import _chat_completion_json, OpenAIRefusal
        mock_urlopen.return_value = _mock_resp(json.dumps({"choices": [{"message": {"content": None, "refusal": "I can't identify people in images."}, "finish_reason": "stop"}], "usage": {}}).encode())
        with self.assertRaises(OpenAIRefusal) as cm:
            _chat_completion_json("sys", "user", "test-key")
        self.assertIn("can't identify people", str(cm.exception))

    @patch("urllib.request.urlopen")
    def test_image_prompt_writer_retries_without_references_on_refusal(self, mock_urlopen):
        from lib.features.chat_responder import synthesize_contextual_image_prompt
        def chat(body):
            return _mock_resp(json.dumps({"choices": [{"message": {"content": json.dumps(body)}}], "usage": {}}).encode())
        def refusal():
            return _mock_resp(json.dumps({"choices": [{"message": {"content": None, "refusal": "no faces"}}], "usage": {}}).encode())

        # First refusal: retried WITH the image, reframed as art direction
        mock_urlopen.side_effect = [refusal(), chat({"image_prompt": "a gen 4 pokemon trainer sprite of a long-haired man in a cravat"}), chat({"caption": "y"})]
        img, _, _, _ = synthesize_contextual_image_prompt(
            prompt="using <@1> profile picture can you generate them as a pokemon sprite", context="", user_name="Hadidas", caller_role="deputy",
            target_name="oggers", target_id=1, openai_key="test-key", reference_image_urls=["https://cdn/oggers.png"],
        )
        self.assertEqual(img, "a gen 4 pokemon trainer sprite of a long-haired man in a cravat")
        self.assertEqual(mock_urlopen.call_count, 3)
        first = _sent_payload(mock_urlopen, 0)["messages"][1]["content"]
        self.assertIsInstance(first, list)
        second = _sent_payload(mock_urlopen, 1)["messages"][1]["content"]
        self.assertIsInstance(second, list)                        # still has the image
        self.assertEqual(second[1]["image_url"]["url"], "https://cdn/oggers.png")
        self.assertIn("ART-DIRECTION NOTE", second[0]["text"])
        self.assertIn("must not try to identify", second[0]["text"])
        self.assertIn("never attempt to identify", _sent_payload(mock_urlopen, 0)["messages"][0]["content"])

        # Refused twice: only then drop the image
        mock_urlopen.reset_mock()
        mock_urlopen.side_effect = [refusal(), refusal(), chat({"image_prompt": "from history only"}), chat({"caption": "y"})]
        img, _, _, _ = synthesize_contextual_image_prompt(
            prompt="using <@1> profile picture can you generate them as a pokemon sprite", context="", user_name="Hadidas", caller_role="deputy",
            target_name="oggers", target_id=1, openai_key="test-key", reference_image_urls=["https://cdn/oggers.png"],
        )
        self.assertEqual(img, "from history only")
        third = _sent_payload(mock_urlopen, 2)["messages"][1]["content"]
        self.assertIsInstance(third, str)
        self.assertIn("could not be inspected", third)

    @patch("lib.features.chat_responder.download_image_bytes", return_value=b"png-of-oggers")
    @patch("lib.features.chat_responder.edit_image_openai")
    @patch("lib.features.chat_responder.generate_image_openai")
    @patch("lib.features.chat_responder.synthesize_contextual_image_prompt")
    @patch("lib.features.chat_responder.fetch_user_bot_interactions_async", new_callable=AsyncMock, return_value=[])
    @patch("lib.features.chat_responder.fetch_user_chat_sample_async", new_callable=AsyncMock, return_value=[])
    @patch("lib.features.chat_responder.build_user_dossier", return_value=None)
    @patch("lib.features.chat_responder.fetch_user_recent_chat_async", new_callable=AsyncMock, return_value=[])
    @patch("lib.features.chat_responder.find_recent_image_attachment", new_callable=AsyncMock, return_value=None)
    @patch("lib.features.chat_responder.classify_mention_intent")
    async def test_handle_one_off_reference_photo_uses_edit_endpoint(
        self, mock_classify, mock_find_img, mock_fetch_chat, _dossier, _sample, _exchanges, mock_synth, mock_gen_img, mock_edit_img, mock_download
    ):
        mock_classify.return_value = {"intent": "generate", "subject": "mentioned", "subject_name": None, "reason": "", "input_tokens": 0, "output_tokens": 0}
        mock_synth.return_value = ("a gen 4 trainer sprite sheet of a long-haired man in a cravat", "Behold.", 10, 5)
        mock_edit_img.return_value = (b"sprite", 30, 200)

        client = MagicMock(); client.user.id = 999999999
        oggers = MagicMock(); oggers.id = USERS.OGGERS; oggers.nick = "oggers"; oggers.global_name = None; oggers.display_name = "oggers"; oggers.name = "ogme01"
        message = self._leader_message(client, f"<@{client.user.id}> using <@{USERS.OGGERS}> profile picture can you generate them as a pokemon sprite", author_id=USERS.HADIDAS, mentions=[oggers])
        att = MagicMock(); att.filename = "image.png"; att.content_type = "image/png"; att.url = "https://cdn/oggers.png"
        message.attachments = [att]

        with patch("lib.features.chat_responder.can_user_generate_image", return_value=(True, 3)), \
             patch("lib.features.chat_responder.record_user_image_generation"), \
             patch("lib.features.chat_responder.live_chat_manager.update_dashboard", new_callable=AsyncMock):
            res = await handle_one_off_owner_mention(client, message)

        self.assertTrue(res)
        mock_download.assert_called_once_with("https://cdn/oggers.png")
        mock_edit_img.assert_called_once()
        self.assertEqual(mock_edit_img.call_args[0][0], [b"png-of-oggers"])
        self.assertIn("a gen 4 trainer sprite sheet", mock_edit_img.call_args[0][1])
        mock_gen_img.assert_not_called()

        # Two attachments (style sheet + profile picture): both go to the edit endpoint, in order
        mock_download.reset_mock(); mock_edit_img.reset_mock()
        mock_download.side_effect = lambda url: b"SHEET" if "sheet" in url else b"DUCK"
        sheet = MagicMock(); sheet.filename = "sheet.png"; sheet.content_type = "image/png"; sheet.url = "https://cdn/sheet.png"
        duck = MagicMock(); duck.filename = "duck.png"; duck.content_type = "image/png"; duck.url = "https://cdn/duck.png"
        message3 = self._leader_message(client, f"<@{client.user.id}> generate me a sprite sheet in the style of Ethan using my profile picture for the design", author_id=USERS.HADIDAS)
        message3.attachments = [sheet, duck]
        mock_synth.return_value = ("match the first image's sprite style; design from the second image, a cream duck in a bow tie", "Behold.", 10, 5)
        with patch("lib.features.chat_responder.can_user_generate_image", return_value=(True, 3)), \
             patch("lib.features.chat_responder.record_user_image_generation"), \
             patch("lib.features.chat_responder.live_chat_manager.update_dashboard", new_callable=AsyncMock):
            await handle_one_off_owner_mention(client, message3)
        self.assertEqual(mock_synth.call_args[1]["reference_image_urls"], ["https://cdn/sheet.png", "https://cdn/duck.png"])
        self.assertEqual(mock_edit_img.call_args[0][0], [b"SHEET", b"DUCK"])
        self.assertTrue(mock_edit_img.call_args[0][1].startswith("Using the attached images as references, in the order attached"))

        # If the edit endpoint fails, plain generation still happens
        mock_edit_img.side_effect = RuntimeError("edit down")
        mock_gen_img.return_value = (b"sprite2", 20, 200)
        message2 = self._leader_message(client, f"<@{client.user.id}> using <@{USERS.OGGERS}> profile picture make him a sprite", author_id=USERS.HADIDAS, mentions=[oggers])
        message2.attachments = [att]
        with patch("lib.features.chat_responder.can_user_generate_image", return_value=(True, 3)), \
             patch("lib.features.chat_responder.record_user_image_generation"), \
             patch("lib.features.chat_responder.live_chat_manager.update_dashboard", new_callable=AsyncMock):
            await handle_one_off_owner_mention(client, message2)
        mock_gen_img.assert_called_once()

    @patch("asyncio.sleep", new_callable=AsyncMock)
    @patch("lib.features.chat_responder.generate_image_openai")
    @patch("lib.features.chat_responder.synthesize_contextual_image_prompt")
    @patch("lib.features.chat_responder.fetch_user_bot_interactions_async", new_callable=AsyncMock, return_value=[])
    @patch("lib.features.chat_responder.fetch_user_chat_sample_async", new_callable=AsyncMock, return_value=[])
    @patch("lib.features.chat_responder.build_user_dossier", return_value=None)
    @patch("lib.features.chat_responder.fetch_user_recent_chat_async", new_callable=AsyncMock, return_value=[])
    @patch("lib.features.chat_responder.find_recent_image_attachment", new_callable=AsyncMock, return_value=None)
    @patch("lib.features.chat_responder.classify_mention_intent")
    async def test_handle_one_off_synthesis_retries_once_before_raw_fallback(
        self, mock_classify, mock_find_img, mock_fetch_chat, _dossier, _sample, _exchanges, mock_synth, mock_gen_img, _sleep
    ):
        mock_classify.return_value = {"intent": "generate", "subject": "caller", "subject_name": None, "reason": "", "input_tokens": 0, "output_tokens": 0}
        mock_synth.side_effect = [RuntimeError("blip"), ("Oggers as a pirate", "<@1> Arr.", 10, 5)]
        mock_gen_img.return_value = (b"img", 20, 200)

        client = MagicMock(); client.user.id = 999999999
        message = self._leader_message(client, f"<@{client.user.id}> draw me as a pirate")
        with patch("lib.features.chat_responder.can_user_generate_image", return_value=(True, 999999)), \
             patch("lib.features.chat_responder.record_user_image_generation"), \
             patch("lib.features.chat_responder.live_chat_manager.update_dashboard", new_callable=AsyncMock):
            await handle_one_off_owner_mention(client, message)

        self.assertEqual(mock_synth.call_count, 2)
        mock_gen_img.assert_called_once_with("Oggers as a pirate")
        self.assertIn("Arr.", message.reply.call_args[0][0])

        # Two failures: raw prompt fallback
        mock_synth.reset_mock(); mock_gen_img.reset_mock()
        mock_synth.side_effect = [RuntimeError("blip"), RuntimeError("blip again")]
        message2 = self._leader_message(client, f"<@{client.user.id}> draw me a cup of tea")
        with patch("lib.features.chat_responder.can_user_generate_image", return_value=(True, 999999)), \
             patch("lib.features.chat_responder.record_user_image_generation"), \
             patch("lib.features.chat_responder.live_chat_manager.update_dashboard", new_callable=AsyncMock):
            await handle_one_off_owner_mention(client, message2)
        self.assertEqual(mock_synth.call_count, 2)
        self.assertIn("cup of tea", mock_gen_img.call_args[0][0])
        self.assertIn("strain your eyes", message2.reply.call_args[0][0])

    async def test_find_recent_image_attachment_reply_to_user_photo(self):
        from lib.features import chat_responder as cr
        cr.live_chat_manager.conversation_history.append({"role": "assistant", "speaker": "HMS Victory", "content": "[Generated Image: sprite sheet of Hadidas]"})
        att = MagicMock(); att.filename = "monkey.jpg"; att.content_type = "image/jpeg"
        twiggy = MagicMock(); twiggy.id = 4321; twiggy.nick = "hot-dog sized twiggy"; twiggy.global_name = None; twiggy.display_name = "hot-dog sized twiggy"; twiggy.name = "twiggy"
        ref_msg = MagicMock(); ref_msg.content = "what I imagine oggers looks like"; ref_msg.author = twiggy; ref_msg.attachments = [att]
        message = MagicMock(); message.reference = MagicMock(); message.reference.message_id = 1; message.reference.resolved = ref_msg

        with patch.object(cr.discord, "Message", MagicMock):
            res = await cr.find_recent_image_attachment(message, bot_id=777)
        self.assertIsNotNone(res)
        prev_msg, prev_att, prev_prompt = res
        self.assertIs(prev_att, att)
        self.assertEqual(prev_prompt, 'An image posted by hot-dog sized twiggy with the caption: "what I imagine oggers looks like"')

        # One of the bot's own images still uses the generated prompt
        ref_msg.author = MagicMock(); ref_msg.author.id = 777
        with patch.object(cr.discord, "Message", MagicMock):
            _, _, prev_prompt = await cr.find_recent_image_attachment(message, bot_id=777)
        self.assertEqual(prev_prompt, "sprite sheet of Hadidas")

    @patch("urllib.request.urlopen")
    def test_edit_image_openai_multiple_images(self, mock_urlopen):
        from lib.features.chat_responder import edit_image_openai
        import base64
        mock_urlopen.return_value = _mock_resp(json.dumps({"data": [{"b64_json": base64.b64encode(b"out").decode()}], "usage": {"input_tokens": 1, "output_tokens": 2}}).encode())
        img, _, _ = edit_image_openai([b"SHEET", b"DUCK"], "match the first, design from the second", openai_key="test-key")
        self.assertEqual(img, b"out")
        body = mock_urlopen.call_args[0][0].data
        self.assertEqual(body.count(b'name="image[]"'), 2)
        self.assertNotIn(b'name="image"; ', body)
        self.assertLess(body.index(b"SHEET"), body.index(b"DUCK"))
        # single image keeps the plain field name
        edit_image_openai(b"ONLY", "x", openai_key="test-key")
        body = mock_urlopen.call_args[0][0].data
        self.assertEqual(body.count(b'name="image"; '), 1)
        self.assertNotIn(b"image[]", body)

    @patch("lib.features.chat_responder.generate_image_openai")
    @patch("lib.features.chat_responder.edit_image_openai")
    @patch("lib.features.chat_responder.synthesize_image_edit_prompt")
    @patch("lib.features.chat_responder.gather_one_off_context", new_callable=AsyncMock, return_value=("ctx", {}))
    @patch("lib.features.chat_responder.find_recent_image_attachment", new_callable=AsyncMock)
    @patch("lib.features.chat_responder.classify_mention_intent")
    async def test_handle_one_off_edit_with_own_attachment_edits_the_attachment(
        self, mock_classify, mock_find_img, mock_gather, mock_synth_edit, mock_edit_img, mock_gen_img
    ):
        mock_classify.return_value = {"intent": "edit", "subject": "none", "subject_name": None, "reason": "add a mullet", "input_tokens": 0, "output_tokens": 0}
        # The bot's last image in the channel is something else entirely
        bot_att = MagicMock(); bot_att.read = AsyncMock(return_value=b"OGGERS-CARICATURE")
        mock_find_img.return_value = (MagicMock(content="Behold Oggers"), bot_att, "Oggers pub quiz caricature")
        mock_synth_edit.return_value = ("edit", "add a pink mullet to the man in the portrait", "The canvas has been amended.", 10, 5)
        mock_edit_img.return_value = (b"edited", 30, 200)

        client = MagicMock(); client.user.id = 999999999
        message = self._leader_message(client, f"<@{client.user.id}> can you give this picture a pink mullet", author_id=USERS.HADIDAS)
        own = MagicMock(); own.filename = "portrait.png"; own.content_type = "image/png"; own.url = "https://cdn/portrait.png"
        own.read = AsyncMock(return_value=b"PORTRAIT")
        message.attachments = [own]

        with patch("lib.features.chat_responder.can_user_generate_image", return_value=(True, 3)), \
             patch("lib.features.chat_responder.record_user_image_generation"), \
             patch("lib.features.chat_responder.live_chat_manager.update_dashboard", new_callable=AsyncMock):
            res = await handle_one_off_owner_mention(client, message)

        self.assertTrue(res)
        mock_edit_img.assert_called_once()
        self.assertEqual(mock_edit_img.call_args[0][0], b"PORTRAIT")          # the attachment, not the bot's caricature
        self.assertRegex(mock_synth_edit.call_args[1]["prev_prompt"], r"^An image attached by \S+ to this request$")
        self.assertEqual(mock_synth_edit.call_args[1]["reference_image_urls"], [])
        mock_gen_img.assert_not_called()
        self.assertIn("file", message.reply.call_args[1])

        # Same request with no recent bot image at all still edits the attachment (doesn't degrade to generate)
        mock_find_img.return_value = None
        mock_edit_img.reset_mock()
        message2 = self._leader_message(client, f"<@{client.user.id}> give this picture a pink mullet", author_id=USERS.HADIDAS)
        message2.attachments = [own]
        with patch("lib.features.chat_responder.can_user_generate_image", return_value=(True, 3)), \
             patch("lib.features.chat_responder.record_user_image_generation"), \
             patch("lib.features.chat_responder.live_chat_manager.update_dashboard", new_callable=AsyncMock):
            await handle_one_off_owner_mention(client, message2)
        self.assertEqual(mock_edit_img.call_args[0][0], b"PORTRAIT")
        mock_gen_img.assert_not_called()

    def test_looks_like_attachment_modification(self):
        from lib.features.chat_responder import looks_like_attachment_modification
        for yes in [
            "add a pink mullet to this fine gentleman",
            "can you give this picture a pink mullet",
            "put a hat on him",
            "make this photo black and white",
            "remove the background from this image",
            "turn this into a pokemon card",
            "this picture but with a moustache",
        ]:
            self.assertTrue(looks_like_attachment_modification(yes), yes)
        for no in [
            "here is picture of oggers, please generate a picture based on what you think he looks like",
            "draw me as a pirate",
            "what do you think of this",
            "generate a sprite sheet of hadidas (attached) as a pokemon",
        ]:
            self.assertFalse(looks_like_attachment_modification(no), no)

    @patch("lib.features.chat_responder.generate_image_openai")
    @patch("lib.features.chat_responder.edit_image_openai")
    @patch("lib.features.chat_responder.synthesize_image_edit_prompt")
    @patch("lib.features.chat_responder.synthesize_contextual_image_prompt")
    @patch("lib.features.chat_responder.gather_one_off_context", new_callable=AsyncMock, return_value=("ctx", {}))
    @patch("lib.features.chat_responder.find_recent_image_attachment", new_callable=AsyncMock, return_value=None)
    @patch("lib.features.chat_responder.classify_mention_intent")
    async def test_handle_one_off_modification_of_attachment_is_edit_even_if_classified_generate(
        self, mock_classify, mock_find_img, mock_gather, mock_synth, mock_synth_edit, mock_edit_img, mock_gen_img
    ):
        mock_classify.return_value = {"intent": "generate", "subject": "caller", "subject_name": "oggers", "reason": "new image with a mullet", "input_tokens": 0, "output_tokens": 0}
        mock_synth_edit.return_value = ("edit", "add a flamboyant pink mullet to the man in the portrait, keep everything else", "Amended.", 10, 5)
        mock_edit_img.return_value = (b"edited", 30, 200)

        client = MagicMock(); client.user.id = 999999999
        message = self._leader_message(client, f"<@{client.user.id}> add a pink mullet to this fine gentleman")
        own = MagicMock(); own.filename = "portrait.png"; own.content_type = "image/png"; own.url = "https://cdn/portrait.png"; own.read = AsyncMock(return_value=b"PORTRAIT")
        message.attachments = [own]

        with patch("lib.features.chat_responder.can_user_generate_image", return_value=(True, 999999)), \
             patch("lib.features.chat_responder.record_user_image_generation"), \
             patch("lib.features.chat_responder.live_chat_manager.update_dashboard", new_callable=AsyncMock):
            res = await handle_one_off_owner_mention(client, message)

        self.assertTrue(res)
        mock_synth.assert_not_called()                      # no dossier caricature
        mock_edit_img.assert_called_once()
        self.assertEqual(mock_edit_img.call_args[0][0], b"PORTRAIT")
        self.assertIn("pink mullet", mock_edit_img.call_args[0][1])
        mock_gen_img.assert_not_called()

    @patch("lib.features.chat_responder.download_image_bytes", return_value=b"REES-MOGG")
    @patch("lib.features.chat_responder.edit_image_openai")
    @patch("lib.features.chat_responder.generate_image_openai")
    @patch("lib.features.chat_responder.synthesize_contextual_image_prompt")
    @patch("lib.features.chat_responder.fetch_user_bot_interactions_async", new_callable=AsyncMock, return_value=[])
    @patch("lib.features.chat_responder.fetch_user_chat_sample_async", new_callable=AsyncMock, return_value=[])
    @patch("lib.features.chat_responder.build_user_dossier", return_value=None)
    @patch("lib.features.chat_responder.fetch_user_recent_chat_async", new_callable=AsyncMock, return_value=[])
    @patch("lib.features.chat_responder.find_recent_image_attachment", new_callable=AsyncMock, return_value=None)
    @patch("lib.features.chat_responder.classify_mention_intent")
    async def test_handle_one_off_delegation_inherits_replied_to_image(
        self, mock_classify, mock_find_img, mock_fetch_chat, _dossier, _sample, _exchanges, mock_synth, mock_gen_img, mock_edit_img, mock_download
    ):
        mock_classify.return_value = {"intent": "generate", "subject": "named", "subject_name": "oggers", "reason": "", "input_tokens": 0, "output_tokens": 0}
        mock_synth.return_value = ("a rubber-hose cartoon of a man in a top hat and morning suit", "Behold.", 10, 5)
        mock_edit_img.return_value = (b"img", 20, 200)

        client = MagicMock(); client.user.id = 999999999
        kim = MagicMock(); kim.id = 31337; kim.nick = "Kim John Un"; kim.global_name = None; kim.display_name = "Kim John Un"; kim.name = "kim"
        photo = MagicMock(); photo.filename = "mogg.jpg"; photo.content_type = "image/jpeg"; photo.url = "https://cdn/mogg.jpg"
        ref_msg = MagicMock()
        ref_msg.content = f"<@{client.user.id}> here is picture of oggers, please generate a picture based on what you think he looks like"
        ref_msg.author = kim; ref_msg.mentions = [client.user]; ref_msg.attachments = [photo]
        ref = MagicMock(); ref.message_id = 4242; ref.resolved = ref_msg
        message = self._leader_message(client, f"<@{client.user.id}> pls do this", reference=ref)

        with patch("lib.features.chat_responder.can_user_generate_image", return_value=(True, 999999)), \
             patch("lib.features.chat_responder.record_user_image_generation"), \
             patch("lib.features.chat_responder.live_chat_manager.update_dashboard", new_callable=AsyncMock):
            res = await handle_one_off_owner_mention(client, message)

        self.assertTrue(res)
        self.assertEqual(mock_synth.call_args[1]["reference_image_urls"], ["https://cdn/mogg.jpg"])
        self.assertTrue(mock_classify.call_args[1]["has_attached_image"])
        mock_download.assert_called_once_with("https://cdn/mogg.jpg")
        self.assertEqual(mock_edit_img.call_args[0][0], [b"REES-MOGG"])
        mock_gen_img.assert_not_called()

    def test_classify_mention_intent_without_key_returns_none(self):
        from lib.features.chat_responder import classify_mention_intent
        with patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(classify_mention_intent("draw me", openai_key=None))

    @patch("urllib.request.urlopen")
    def test_classify_mention_intent_bad_output_returns_none(self, mock_urlopen):
        from lib.features.chat_responder import classify_mention_intent

        mock_urlopen.return_value = _mock_resp(_responses_body("not json at all"))
        self.assertIsNone(classify_mention_intent("draw me", openai_key="test-key"))

        mock_urlopen.return_value = _mock_resp(_responses_body(json.dumps({"intent": "dance", "subject": "none", "subject_name": None, "reason": ""})))
        self.assertIsNone(classify_mention_intent("draw me", openai_key="test-key"))

        mock_urlopen.side_effect = OSError("down")
        self.assertIsNone(classify_mention_intent("draw me", openai_key="test-key"))

    def test_resolve_image_target(self):
        from lib.features.chat_responder import resolve_image_target

        steven = MagicMock()
        steven.id = 555
        steven.nick = "Steven <3"
        steven.global_name = None
        steven.display_name = "Steven <3"
        steven.name = "steven_x"
        steven.bot = False
        mentions = [steven]
        target_users = {"steven": 555, "johnny": 777}

        # Classifier subject wins
        self.assertEqual(resolve_image_target("what do i look like", 1, "Oggers", [], {}, subject="caller"), ("Oggers", 1))
        self.assertEqual(resolve_image_target("generate @Steven i reckon", 1, "Oggers", mentions, target_users, subject="mentioned"), ("Steven <3", 555))
        self.assertEqual(resolve_image_target("do johnny next", 1, "Oggers", [], target_users, subject="named", subject_name="Johnny"), ("Johnny", 777))
        self.assertEqual(resolve_image_target("do steven", 1, "Oggers", mentions, {}, subject="named", subject_name="steven"), ("Steven <3", 555))
        self.assertEqual(resolve_image_target("do dave", 1, "Oggers", [], {}, subject="named", subject_name="Dave"), ("Dave", None))
        self.assertEqual(resolve_image_target("draw a cat for me", 1, "Oggers", [], {}, subject="none"), (None, None))

        # A named subject that isn't the caller beats a mistaken "caller" label, resolved via the guild
        guild = MagicMock()
        hadidas = MagicMock(); hadidas.id = 198; hadidas.nick = "Hadidas"; hadidas.global_name = None; hadidas.display_name = "Hadidas"; hadidas.name = "hadidas_"; hadidas.bot = False
        guild.get_member_named.side_effect = lambda n: hadidas if n.lower().startswith("hadidas") else None
        guild.members = [hadidas, steven]
        self.assertEqual(resolve_image_target("sprite sheet of hadidas (attached)", 1, "Oggers", [], {}, subject="caller", subject_name="hadidas", guild=guild), ("Hadidas", 198))
        self.assertEqual(resolve_image_target("sprite sheet of hadidas (attached)", 1, "Oggers", [], {}, subject="named", subject_name="hadidas (attached)", guild=guild), ("Hadidas", 198))
        # partial, unique match against member names
        guild.get_member_named.side_effect = lambda n: None
        self.assertEqual(resolve_image_target("draw stev", 1, "Oggers", [], {}, subject="named", subject_name="stev", guild=guild), ("Steven <3", 555))
        # "caller" with a self-word stays the caller
        self.assertEqual(resolve_image_target("draw me", 1, "Oggers", [], {}, subject="caller", subject_name="me", guild=guild), ("Oggers", 1))
        # unknown name: passed through, no id
        self.assertEqual(resolve_image_target("draw zog", 1, "Oggers", [], {}, subject="named", subject_name="Zog", guild=guild), ("Zog", None))

        # Two mentions: the classifier's raw <@id> answer wins, then name, then position in the text
        danez = MagicMock(); danez.id = 412; danez.nick = None; danez.global_name = "Danez"; danez.display_name = "Danez"; danez.name = "danez"
        two = [danez, steven]   # Discord order: NOT message order
        prompt = "turn <@555> into an animorph of a horse and send it to <@412>"
        self.assertEqual(resolve_image_target(prompt, 1, "Oggers", two, {}, subject="mentioned", subject_name="<@555>"), ("Steven <3", 555))
        self.assertEqual(resolve_image_target(prompt, 1, "Oggers", two, {}, subject="mentioned", subject_name="Steven"), ("Steven <3", 555))
        self.assertEqual(resolve_image_target(prompt, 1, "Oggers", two, {}, subject="mentioned", subject_name=None), ("Steven <3", 555))
        self.assertEqual(resolve_image_target(prompt, 1, "Oggers", two, {}), ("Steven <3", 555))
        self.assertEqual(resolve_image_target("draw <@412> and <@555>", 1, "Oggers", two, {}, subject="mentioned"), ("Danez", 412))

        # Fallback without the classifier: an explicit mention beats a stray "i"
        self.assertEqual(resolve_image_target("generate what you think @Steven looks like, i reckon", 1, "Oggers", mentions, target_users), ("Steven <3", 555))
        self.assertEqual(resolve_image_target("what do i look like", 1, "Oggers", [], {}), ("Oggers", 1))
        self.assertEqual(resolve_image_target("draw a cat", 1, "Oggers", [], {}), (None, None))

    def test_context_has_user_history(self):
        from lib.features.chat_responder import context_has_user_history, format_user_chat_for_context

        ctx = format_user_chat_for_context("Steven <3", 555, [{"content": "jaffa cakes", "channel": "general", "ts": 1}])
        self.assertTrue(context_has_user_history(ctx, 555))
        # A mere ping of the user elsewhere in context is not their history
        self.assertFalse(context_has_user_history("Chin: oi <@555> you about?", 555))
        self.assertFalse(context_has_user_history(ctx, 556))

    # ---- Handler decisions driven by the classifier ----

    def _leader_message(self, client, content, author_id=USERS.OGGERS, mentions=None, reference=None):
        message = MagicMock()
        message.id = 90000000 + abs(hash(content)) % 1000000
        message.author.id = author_id
        message.author.name = "ogme01"
        message.author.nick = None
        message.author.global_name = None
        message.author.display_name = None
        message.author.bot = False
        message.content = content
        message.mentions = [client.user] + (mentions or [])
        message.attachments = []
        message.reference = reference
        message.channel.history = MagicMock()
        message.reply = AsyncMock()
        return message

    @patch("lib.features.chat_responder.generate_image_openai")
    @patch("lib.features.chat_responder.synthesize_contextual_image_prompt")
    @patch("lib.features.chat_responder.fetch_user_recent_chat_async")
    @patch("lib.features.chat_responder.find_recent_image_attachment", new_callable=AsyncMock, return_value=None)
    @patch("lib.features.chat_responder.fetch_user_bot_interactions_async", new_callable=AsyncMock, return_value=[])
    @patch("lib.features.chat_responder.fetch_user_chat_sample_async", new_callable=AsyncMock, return_value=[])
    @patch("lib.features.chat_responder.classify_mention_intent")
    async def test_handle_one_off_classifier_generate_targets_mentioned_user(
        self, mock_classify, _sample, _exchanges, mock_find_img, mock_fetch_chat, mock_synth, mock_gen_img
    ):
        mock_classify.return_value = {
            "intent": "generate", "subject": "mentioned", "subject_name": None, "reason": "portrait", "input_tokens": 0, "output_tokens": 0,
        }
        mock_fetch_chat.return_value = [{"content": "Jaffa cakes are a biscuit", "channel": "general", "ts": 1700000000}]
        mock_synth.return_value = ("A cheeky chap clutching Jaffa cakes", "<@1> Behold Steven.", 150, 40)
        mock_gen_img.return_value = (b"img", 20, 200)

        client = MagicMock()
        client.user.id = 999999999
        steven = MagicMock()
        steven.id = 555
        steven.nick = "Steven <3"
        steven.global_name = None
        steven.display_name = "Steven <3"
        steven.name = "steven_x"

        # The stray "i" here used to make the caller the target.
        message = self._leader_message(
            client,
            f"<@{client.user.id}> generate what you think <@{steven.id}> looks like based on his message history, focus on what i think is funny",
            mentions=[steven],
        )

        with patch("lib.features.chat_responder.can_user_generate_image", return_value=(True, 999999)), \
             patch("lib.features.chat_responder.record_user_image_generation"), \
             patch("lib.features.chat_responder.live_chat_manager.update_dashboard", new_callable=AsyncMock):
            res = await handle_one_off_owner_mention(client, message)

        self.assertTrue(res)
        mock_classify.assert_called_once()
        self.assertEqual(mock_classify.call_args[1]["mentioned_names"], ["Steven <3"])
        self.assertEqual(mock_synth.call_args[1]["target_name"], "Steven <3")
        self.assertIn("Jaffa cakes are a biscuit", mock_synth.call_args[1]["context"])
        fetched_ids = [c[0][1] for c in mock_fetch_chat.call_args_list]
        self.assertIn(555, fetched_ids)
        self.assertNotIn(USERS.OGGERS, fetched_ids)
        mock_gen_img.assert_called_once_with("A cheeky chap clutching Jaffa cakes")

    @patch("lib.features.chat_responder.generate_image_openai")
    @patch("lib.features.chat_responder.edit_image_openai")
    @patch("lib.features.chat_responder.generate_one_off_reply")
    @patch("lib.features.chat_responder.gather_one_off_context")
    @patch("lib.features.chat_responder.find_recent_image_attachment", new_callable=AsyncMock)
    @patch("lib.features.chat_responder.classify_mention_intent")
    async def test_handle_one_off_commentary_on_image_is_text_reply(
        self, mock_classify, mock_find_img, mock_gather, mock_generate, mock_edit_img, mock_gen_img
    ):
        """Replying to a bot image with a remark must not burn an image generation."""
        mock_classify.return_value = {
            "intent": "reply", "subject": "none", "subject_name": None, "reason": "commentary", "input_tokens": 0, "output_tokens": 0,
        }
        prev_att = MagicMock()
        prev_att.read = AsyncMock(return_value=b"orig")
        mock_find_img.return_value = (MagicMock(content="Here's Oggers"), prev_att, "A tea-drinking Brit")
        mock_gather.return_value = ("RECENT CHAT", {})
        mock_generate.return_value = ("Twice is a motif. Three times would be a problem.", 100, 20)

        client = MagicMock()
        client.user.id = 999999999
        ref = MagicMock()
        ref.message_id = 4242
        message = self._leader_message(
            client, f"<@{client.user.id}> Notice how it featured the red lion twice", author_id=USERS.HADIDAS, reference=ref,
        )
        message.guild = None

        with patch("lib.features.chat_responder.extract_image_urls", new_callable=AsyncMock, return_value=[]), \
             patch("lib.features.chat_responder.live_chat_manager.update_dashboard", new_callable=AsyncMock):
            res = await handle_one_off_owner_mention(client, message)

        self.assertTrue(res)
        mock_edit_img.assert_not_called()
        mock_gen_img.assert_not_called()
        mock_generate.assert_called_once()
        self.assertIn("Twice is a motif", message.reply.call_args[0][0])
        self.assertNotIn("file", message.reply.call_args[1])

    @patch("lib.features.chat_responder.generate_image_openai")
    @patch("lib.features.chat_responder.edit_image_openai")
    @patch("lib.features.chat_responder.synthesize_image_edit_prompt")
    @patch("lib.features.chat_responder.fetch_user_recent_chat_async")
    @patch("lib.features.chat_responder.find_recent_image_attachment", new_callable=AsyncMock)
    @patch("lib.features.chat_responder.fetch_user_bot_interactions_async", new_callable=AsyncMock, return_value=[])
    @patch("lib.features.chat_responder.fetch_user_chat_sample_async", new_callable=AsyncMock, return_value=[])
    @patch("lib.features.chat_responder.classify_mention_intent")
    async def test_handle_one_off_try_again_with_corrections_is_edit(
        self, mock_classify, _sample, _exchanges, mock_find_img, mock_fetch_chat, mock_synth_edit, mock_edit_img, mock_gen_img
    ):
        mock_classify.return_value = {
            "intent": "edit", "subject": "mentioned", "subject_name": None, "reason": "corrections to last portrait", "input_tokens": 5, "output_tokens": 2,
        }
        prev_att = MagicMock()
        prev_att.read = AsyncMock(return_value=b"orig")
        mock_find_img.return_value = (MagicMock(content="Here's Oggers"), prev_att, "A tea-drinking Brit with a flag")
        mock_fetch_chat.return_value = [{"content": "off to Lisbon again", "channel": "general", "ts": 1700000000}]
        mock_synth_edit.return_value = ("new", "A globe-trotting pub crawler with a passport and a pint", "<@1> Revised. No tea.", 100, 30)
        mock_gen_img.return_value = (b"img", 20, 200)

        client = MagicMock()
        client.user.id = 999999999
        oggers = MagicMock()
        oggers.id = USERS.OGGERS
        oggers.nick = "oggers"
        oggers.global_name = None
        oggers.display_name = "oggers"
        oggers.name = "ogme01"

        message = self._leader_message(
            client,
            f"<@{client.user.id}> can you try again <@{USERS.OGGERS}> doesn't drink tea, he does a lot of international travel, enjoys a good pub crawl",
            author_id=USERS.HADIDAS,
            mentions=[oggers],
        )

        with patch("lib.features.chat_responder.can_user_generate_image", return_value=(True, 3)), \
             patch("lib.features.chat_responder.record_user_image_generation"), \
             patch("lib.features.chat_responder.live_chat_manager.record_usage") as mock_usage, \
             patch("lib.features.chat_responder.live_chat_manager.update_dashboard", new_callable=AsyncMock):
            res = await handle_one_off_owner_mention(client, message)

        self.assertTrue(res)
        mock_synth_edit.assert_called_once()
        self.assertEqual(mock_synth_edit.call_args[1]["target_name"], "oggers")
        self.assertEqual(mock_synth_edit.call_args[1]["prev_prompt"], "A tea-drinking Brit with a flag")
        self.assertIn("off to Lisbon again", mock_synth_edit.call_args[1]["context"])
        # synth said "new", so it regenerates rather than edits the old pixels
        mock_edit_img.assert_not_called()
        mock_gen_img.assert_called_once_with("A globe-trotting pub crawler with a passport and a pint")
        self.assertIn("file", message.reply.call_args[1])
        self.assertIn("Revised. No tea.", message.reply.call_args[0][0])
        self.assertTrue(any(c[0][0] == "gpt-4o-mini" for c in mock_usage.call_args_list))

    @patch("lib.features.chat_responder.generate_image_openai")
    @patch("lib.features.chat_responder.synthesize_contextual_image_prompt")
    @patch("lib.features.chat_responder.fetch_user_recent_chat_async", new_callable=AsyncMock, return_value=[])
    @patch("lib.features.chat_responder.find_recent_image_attachment", new_callable=AsyncMock, return_value=None)
    @patch("lib.features.chat_responder.classify_mention_intent")
    async def test_handle_one_off_edit_without_recent_image_generates(
        self, mock_classify, mock_find_img, mock_fetch_chat, mock_synth, mock_gen_img
    ):
        mock_classify.return_value = {
            "intent": "edit", "subject": "caller", "subject_name": None, "reason": "wants a redo", "input_tokens": 0, "output_tokens": 0,
        }
        mock_synth.return_value = ("A portrait of Oggers", "<@1> Fine.", 10, 5)
        mock_gen_img.return_value = (b"img", 20, 200)

        client = MagicMock()
        client.user.id = 999999999
        message = self._leader_message(client, f"<@{client.user.id}> try again but make me look less like a tea advert")

        with patch("lib.features.chat_responder.can_user_generate_image", return_value=(True, 999999)), \
             patch("lib.features.chat_responder.record_user_image_generation"), \
             patch("lib.features.chat_responder.live_chat_manager.update_dashboard", new_callable=AsyncMock):
            res = await handle_one_off_owner_mention(client, message)

        self.assertTrue(res)
        mock_gen_img.assert_called_once_with("A portrait of Oggers")
        self.assertEqual(mock_synth.call_args[1]["target_name"], "ogme01")
        self.assertEqual(mock_fetch_chat.call_args[0][1], USERS.OGGERS)

    def test_strip_search_citations(self):
        cited = ["https://www.bbc.co.uk/sport/football/fixtures?utm_source=openai"]
        # Parenthetical source list is dropped whole, including the leading space
        self.assertEqual(
            strip_search_citations(
                "Luton play tonight ([BBC](https://www.bbc.co.uk/sport/football/fixtures?utm_source=openai)).", cited
            ),
            "Luton play tonight.",
        )
        # Inline citation link keeps its label text
        self.assertEqual(
            strip_search_citations("See [the fixtures](https://www.bbc.co.uk/sport/football/fixtures) for details.", cited),
            "See the fixtures for details.",
        )
        # Bare cited URL is removed but trailing punctuation survives
        self.assertEqual(
            strip_search_citations("Kick off is 8pm https://www.bbc.co.uk/sport/football/fixtures.", cited),
            "Kick off is 8pm.",
        )
        # utm_source=openai marks a citation even with no annotation
        self.assertEqual(
            strip_search_citations("Arsenal won ([sky](https://skysports.com/x?utm_source=openai))."),
            "Arsenal won.",
        )
        # A real link from server context that wasn't cited survives untouched
        self.assertEqual(
            strip_search_citations("Event's here: https://discord.com/events/1/2", cited),
            "Event's here: https://discord.com/events/1/2",
        )
        # Mixed parenthetical keeps the non-citation link and unwraps the cited one
        self.assertEqual(
            strip_search_citations(
                "Details ([a](https://example.com/real), [BBC](https://www.bbc.co.uk/sport/football/fixtures)).", cited
            ),
            "Details ([a](https://example.com/real), BBC).",
        )
        self.assertEqual(strip_search_citations("", cited), "")

    def test_parse_openai_response_output(self):
        data = json.loads(_responses_body(
            "Hello", annotations=[{"type": "url_citation", "url": "https://x.com/a"}], search_calls=2
        ))
        self.assertEqual(parse_openai_response_output(data), ("Hello", None, ["https://x.com/a"], 2))

        data = json.loads(_responses_body(None, refusal="nope"))
        self.assertEqual(parse_openai_response_output(data)[:2], ("", "nope"))

        self.assertEqual(parse_openai_response_output({"output": None}), ("", None, [], 0))

    def test_resolve_name_mentions(self):
        from lib.features.chat_responder import resolve_name_mentions, sanitize_ai_mentions
        zwsp = "\u200b"
        johnny = MagicMock(); johnny.id = 797; johnny.nick = None; johnny.global_name = "Johnny"; johnny.display_name = "Johnny"; johnny.name = "johnny_uk"
        guild = MagicMock()
        steven = MagicMock(); steven.id = 555; steven.bot = False
        guild.get_member_named.side_effect = lambda n: steven if n.lower() == "steven" else None

        # Caller written as @Name becomes a real ping, punctuation preserved
        self.assertEqual(resolve_name_mentions("@Johnny Want more enthusiasm?", guild, [johnny]), "<@797> Want more enthusiasm?")
        self.assertEqual(resolve_name_mentions("Nice one, @johnny_uk.", guild, [johnny]), "Nice one, <@797>.")
        # Guild member lookup for names the model uses that weren't in the request
        self.assertEqual(resolve_name_mentions("ask @Steven", guild, [johnny]), "ask <@555>")
        # name_map entries (gathered target users) resolve too
        self.assertEqual(resolve_name_mentions("@chin owes me", None, [], {"chin": 795}), "<@795> owes me")
        # Unknown names and existing pings are untouched, and the sanitiser still defangs them
        self.assertEqual(resolve_name_mentions("@Admin and <@123> and @everyone", guild, [johnny]), "@Admin and <@123> and @everyone")
        self.assertEqual(sanitize_ai_mentions(resolve_name_mentions("@Johnny and @everyone", guild, [johnny])), f"<@797> and @{zwsp}everyone")
        self.assertEqual(resolve_name_mentions("email me@example.com", guild, [johnny]), "email me@example.com")

    def test_strip_leading_self_address(self):
        from lib.features.chat_responder import strip_leading_self_address
        self.assertEqual(strip_leading_self_address("<@797>\n\nAh, yes, Johnny. A real bargain.", 797, ["Johnny"]), "Ah, yes, Johnny. A real bargain.")
        self.assertEqual(strip_leading_self_address("<@797>, your timing is impeccable.", 797, ["Johnny"]), "your timing is impeccable.")
        self.assertEqual(strip_leading_self_address("Johnny: go to bed.", 797, ["Johnny"]), "go to bed.")
        self.assertEqual(strip_leading_self_address("@Johnny go to bed.", 797, ["Johnny"]), "@Johnny go to bed.")  # no separator: leave it
        # Other people's pings and mid-sentence uses are untouched
        self.assertEqual(strip_leading_self_address("<@555> owes Johnny a pint.", 797, ["Johnny"]), "<@555> owes Johnny a pint.")
        self.assertEqual(strip_leading_self_address("<@797>", 797, ["Johnny"]), "<@797>")  # never empty the reply

    def test_sanitize_ai_mentions(self):
        zwsp = "\u200b"

        # Everyone and here
        self.assertEqual(sanitize_ai_mentions("Hello @everyone!"), f"Hello @{zwsp}everyone!")
        self.assertEqual(sanitize_ai_mentions("Check @here now"), f"Check @{zwsp}here now")
        self.assertEqual(sanitize_ai_mentions("SHOUTING @EVERYONE"), f"SHOUTING @{zwsp}EVERYONE")
        self.assertEqual(sanitize_ai_mentions("@HERE please"), f"@{zwsp}HERE please")

        # Roles
        self.assertEqual(sanitize_ai_mentions("Ping <@&123456789>"), f"Ping @{zwsp}role_123456789")
        self.assertEqual(sanitize_ai_mentions("Contact @Admin immediately"), f"Contact @{zwsp}Admin immediately")

        # Valid user mentions should NOT be broken
        self.assertEqual(sanitize_ai_mentions("Hi <@123456789>!"), "Hi <@123456789>!")
        self.assertEqual(sanitize_ai_mentions("Hi <@!123456789>!"), "Hi <@!123456789>!")

        # Guild role resolution if role exists
        mock_guild = MagicMock()
        mock_role = MagicMock()
        mock_role.name = "Moderator"
        mock_guild.get_role.return_value = mock_role
        self.assertEqual(sanitize_ai_mentions("Ping <@&999>", guild=mock_guild), f"Ping @{zwsp}Moderator")

    @patch("lib.features.chat_responder.generate_one_off_reply")
    async def test_handle_one_off_owner_mention_blocks_mass_pings(self, mock_generate_reply):
        mock_generate_reply.return_value = ("Hey @everyone check out <@&555666> and <@12345>", 50, 20)

        client = MagicMock()
        client.user.id = 999999999

        message = MagicMock()
        message.id = 777123
        message.author.id = USERS.OGGERS
        message.author.name = "Oggers"
        message.content = "@HMS Victory ping everyone"
        message.guild.get_role.return_value = None
        message.channel.history.return_value = MagicMock()

        async def empty_history(*a, **k):
            if False:
                yield None
        message.channel.history = empty_history
        message.attachments = []
        message.reference = None

        captured_kwargs = {}
        async def mock_reply(content, **kwargs):
            captured_kwargs["content"] = content
            captured_kwargs.update(kwargs)
            return MagicMock()

        message.reply = mock_reply

        res = await handle_one_off_owner_mention(client, message)
        self.assertTrue(res)

        zwsp = "\u200b"
        # Verify text was sanitized
        sent_text = captured_kwargs.get("content", "")
        self.assertIn(f"@{zwsp}everyone", sent_text)
        self.assertIn(f"@{zwsp}role_555666", sent_text)
        self.assertIn("<@12345>", sent_text)  # user ping kept

        # Verify allowed_mentions was enforced
        allowed = captured_kwargs.get("allowed_mentions")
        self.assertIsNotNone(allowed)
        self.assertFalse(allowed.everyone)
        self.assertFalse(allowed.roles)
        self.assertTrue(allowed.users)

    @patch("lib.features.chat_responder.generate_ai_reply")
    async def test_handle_chat_message_troll_mode_targets_bot(self, mock_generate_ai):
        mock_generate_ai.return_value = ("Shut up you glorified calculator.", 40, 10)

        dummy_target_id = 998877665544332211
        live_chat_manager.set_target_user(dummy_target_id)

        client = MagicMock()
        client.user.id = 999999999

        # Target bot message
        bot_message = MagicMock()
        bot_message.id = 888111
        bot_message.author.id = dummy_target_id
        bot_message.author.name = "TestBot"
        bot_message.author.bot = True
        bot_message.content = "We respect all people and open them"
        bot_message.channel.id = 12345
        bot_message.guild = None
        bot_message.attachments = []
        bot_message.reference = None

        typing_mock = MagicMock()
        typing_mock.__aenter__ = MagicMock(side_effect=lambda *a: asyncio.sleep(0))
        typing_mock.__aexit__ = MagicMock(side_effect=lambda *a: asyncio.sleep(0))
        bot_message.channel.typing.return_value = typing_mock

        reply_mock = MagicMock()
        async def async_reply(*a, **k):
            return MagicMock()
        bot_message.reply = async_reply

        # Handle message should trigger even though author.bot is True
        res = await handle_chat_message(client, bot_message)
        self.assertTrue(res)
        mock_generate_ai.assert_called_once()
        _, call_kwargs = mock_generate_ai.call_args
        self.assertTrue(call_kwargs.get("is_defence"))

        # Message from a different bot should NOT trigger
        diff_bot_msg = MagicMock()
        diff_bot_msg.author.id = 123999
        diff_bot_msg.author.bot = True
        diff_bot_msg.channel.id = 12345
        res_diff = await handle_chat_message(client, diff_bot_msg)
        self.assertFalse(res_diff)

        # Message from Vic himself should NEVER trigger (prevent infinite loop)
        vic_msg = MagicMock()
        vic_msg.author.id = client.user.id
        vic_msg.author.bot = True
        vic_msg.channel.id = 12345
        res_vic = await handle_chat_message(client, vic_msg)
        self.assertFalse(res_vic)

        live_chat_manager.set_target_user(None)

    @patch("lib.features.chat_responder.save_chatbot_config")
    @patch("lib.features.chat_responder.load_chatbot_config")
    def test_chatbot_config_persistence(self, mock_load, mock_save):
        mock_load.return_value = {"target_user_id": 998877665544332211}
        mgr = LiveChatManager()
        self.assertEqual(mgr.target_user_id, 998877665544332211)

        # Setting target user should persist to disk
        mgr.set_target_user(987654321)
        self.assertEqual(mgr.target_user_id, 987654321)
        mock_save.assert_called_with({"target_user_id": 987654321})

        # Disabling target user should also persist None
        mgr.set_target_user(None)
        self.assertIsNone(mgr.target_user_id)
        mock_save.assert_called_with({"target_user_id": None})

    @patch("lib.features.chat_responder.load_chatbot_config")
    @patch("os.path.getmtime")
    @patch("os.path.exists")
    async def test_config_watcher_loop_detects_change(self, mock_exists, mock_getmtime, mock_load):
        mock_exists.return_value = True
        mock_getmtime.side_effect = [100.0, 105.0]
        mock_load.side_effect = [{"target_user_id": None}, {"target_user_id": 777888999}]

        mgr = LiveChatManager()
        self.assertIsNone(mgr.target_user_id)
        update_called = asyncio.Event()

        async def fake_update():
            update_called.set()

        mgr.update_dashboard = fake_update

        task = asyncio.create_task(mgr._config_watcher_loop())
        try:
            await asyncio.wait_for(update_called.wait(), timeout=2.5)
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        self.assertEqual(mgr.target_user_id, 777888999)

    def test_is_openai_refusal(self):
        self.assertTrue(is_openai_refusal("I'm sorry, I can't assist with that."))
        self.assertTrue(is_openai_refusal("I cannot assist with that."))
        self.assertTrue(is_openai_refusal("As an AI language model, I cannot..."))
        self.assertTrue(is_openai_refusal(""))
        self.assertTrue(is_openai_refusal("   "))
        self.assertFalse(is_openai_refusal("Kaizo, you absolute donut."))

    @patch("lib.features.chat_responder.generate_one_off_reply")
    @patch("lib.features.chat_responder.gather_one_off_context")
    async def test_handle_one_off_owner_mention_retries_on_refusal(self, mock_gather, mock_generate):
        mock_gather.return_value = ("context", {"kaizo": 1283837687551361117})
        # First call returns refusal, second call succeeds with witty response
        mock_generate.side_effect = [
            ("I'm sorry, I can't assist with that.", 100, 10),
            ("Kaizo, your code is as steady as a jelly tower.", 120, 25),
        ]

        client = MagicMock()
        client.user = MagicMock()
        client.user.id = 1171842947440967770
        message = MagicMock()
        message.id = 998877665511
        message.author = MagicMock()
        message.author.id = USERS.OGGERS
        message.author.name = "ogme01"
        message.author.nick = "Oggers"
        message.content = "<@1171842947440967770> teach kaizo a lesson"
        message.channel = MagicMock()
        message.channel.send = AsyncMock()
        message.reply = AsyncMock()

        res = await handle_one_off_owner_mention(client, message)
        self.assertTrue(res)
        self.assertEqual(mock_generate.call_count, 2)
        message.reply.assert_called_once()
        sent_content = message.reply.call_args[0][0]
        self.assertIn("jelly tower", sent_content)
        self.assertIn("<@1283837687551361117>", sent_content)

    @patch("lib.features.chat_responder.generate_one_off_reply")
    @patch("lib.features.chat_responder.gather_one_off_context")
    async def test_handle_one_off_owner_mention_strips_bot_ping(self, mock_gather, mock_generate):
        mock_gather.return_value = ("context", {})
        # Model inadvertently tags bot itself:
        mock_generate.return_value = ("<@1171842947440967770> Oi Kaizo, behave.", 100, 20)

        client = MagicMock()
        client.user = MagicMock()
        client.user.id = 1171842947440967770
        message = MagicMock()
        message.id = 8877665544
        message.author = MagicMock()
        message.author.id = USERS.OGGERS
        message.author.name = "ogme01"
        message.content = "<@1171842947440967770> teach kaizo a lesson"
        message.channel = MagicMock()
        message.reply = AsyncMock()

        res = await handle_one_off_owner_mention(client, message)
        self.assertTrue(res)
        sent_content = message.reply.call_args[0][0]
        # Bot's own ID mention MUST be removed
        self.assertNotIn("<@1171842947440967770>", sent_content)
        self.assertTrue(sent_content.startswith("Oi Kaizo, behave."))

    @patch("lib.features.chat_responder.save_chatbot_config")
    @patch("lib.features.chat_responder.load_chatbot_config")
    def test_live_chat_manager_owner_mentions_paused_persistence(self, mock_load, mock_save):
        mock_load.return_value = {"owner_mentions_paused": False}
        mgr = LiveChatManager()
        self.assertFalse(mgr.owner_mentions_paused)

        # Pause direct mentions
        mgr.set_owner_mentions_paused(True)
        self.assertTrue(mgr.owner_mentions_paused)
        mock_save.assert_called_with({"owner_mentions_paused": True})

        # Resume direct mentions
        mgr.set_owner_mentions_paused(False)
        self.assertFalse(mgr.owner_mentions_paused)
        mock_save.assert_called_with({"owner_mentions_paused": False})

    @patch("lib.features.chat_responder.handle_one_off_owner_mention")
    async def test_handle_chat_message_oggers_ignored_when_paused(self, mock_handle_one_off):
        client = MagicMock()
        client.user.id = 999999999

        message = MagicMock()
        message.author.bot = False
        message.author.id = USERS.OGGERS
        message.mentions = [client.user]
        message.content = f"<@{client.user.id}> vic, are you awake?"

        # 1. When paused: ignored!
        live_chat_manager.owner_mentions_paused = True
        res = await handle_chat_message(client, message)
        self.assertFalse(res)
        mock_handle_one_off.assert_not_called()

        # 2. When active (not paused): responded!
        live_chat_manager.owner_mentions_paused = False
        res = await handle_chat_message(client, message)
        self.assertTrue(res)
        mock_handle_one_off.assert_called_once_with(client, message)

    async def test_chatbot_direct_pause_button_ui_and_callback(self):
        # 1. When active, button offers to Pause
        live_chat_manager.owner_mentions_paused = False
        btn = ChatbotDirectPauseButton()
        self.assertEqual(btn.label, "Pause Direct")
        self.assertEqual(btn.emoji, "⏸️")
        self.assertEqual(btn.custom_id, "vic_live_toggle_direct")

        # 2. When paused, button offers to Resume
        live_chat_manager.owner_mentions_paused = True
        btn_paused = ChatbotDirectPauseButton()
        self.assertEqual(btn_paused.label, "Resume Direct")
        self.assertEqual(btn_paused.emoji, "▶️")

        # 3. Callback rejected for non-Oggers
        interaction_non_owner = MagicMock()
        interaction_non_owner.user.id = 12345678
        interaction_non_owner.response.send_message = AsyncMock()
        await btn.callback(interaction_non_owner)
        interaction_non_owner.response.send_message.assert_called_once()
        self.assertIn("Only Oggers", interaction_non_owner.response.send_message.call_args[0][0])

        # 4. Callback accepted for Oggers, toggles state and updates message
        live_chat_manager.owner_mentions_paused = False
        interaction_owner = MagicMock()
        interaction_owner.user.id = USERS.OGGERS
        interaction_owner.message = MagicMock()
        interaction_owner.response.edit_message = AsyncMock()

        with patch("lib.features.chat_responder.save_chatbot_config"):
            await btn.callback(interaction_owner)

        self.assertTrue(live_chat_manager.owner_mentions_paused)
        interaction_owner.response.edit_message.assert_called_once()

    def test_chatbot_dashboard_view_includes_pause_button(self):
        view = ChatbotDashboardView()
        found_pause_btn = any(
            getattr(child, "custom_id", None) == "vic_live_toggle_direct"
            for child in view.children[0].walk_children()
        )
        self.assertTrue(found_pause_btn, "ChatbotDirectPauseButton must be present in dashboard view action row")

    def test_calculate_cost_image_model(self):
        cost = calculate_cost("gpt-image-2.5-flare", 100, 200)
        # 100 * 5/1M + 200 * 30/1M = 0.0005 + 0.006 = 0.0065
        self.assertAlmostEqual(cost, 0.0065, places=5)

    def test_looks_like_image_request(self):
        positive_cases = [
            "draw me a pirate ship",
            "generate an image of a teapot",
            "can you draw a cat",
            "paint a picture of oggers",
            "create an image of space",
            "illustrate a knight in armor",
            "photo of a golden retriever",
            "make a drawing of HMS Victory",
            "generate what you think <@555> looks like based on his message history, focusing on his relationship with you",
            "what you reckon steven looks like",
            "can you try again, he doesn't drink tea, feel free to reference his message history for his portrait",
            "generate a satirical portrait of oggers",
        ]
        for prompt in positive_cases:
            self.assertTrue(looks_like_image_request(prompt), f"Expected True for: {prompt}")

        negative_cases = [
            "what's the weather today?",
            "tell me a joke",
            "roast this guy",
            "drawings are really nice to look at",
            "how do you paint a fence?",
        ]
        for prompt in negative_cases:
            self.assertFalse(looks_like_image_request(prompt), f"Expected False for: {prompt}")

    def test_extract_image_prompt(self):
        self.assertEqual(extract_image_prompt("draw me a pirate ship"), "pirate ship")
        self.assertEqual(extract_image_prompt("generate an image of a teapot"), "a teapot")
        self.assertEqual(extract_image_prompt("can you paint a portrait of the king"), "portrait of the king")
        self.assertEqual(extract_image_prompt("a simple sunset"), "a simple sunset")

    @patch("lib.features.chat_responder.atomic_write_json")
    @patch("lib.features.chat_responder.load_json_file")
    def test_image_quota_logic(self, mock_load, mock_save):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # 1. Oggers always allowed and unlimited
        mock_load.return_value = {today: {str(USERS.OGGERS): 100}}
        allowed, rem = can_user_generate_image(USERS.OGGERS)
        self.assertTrue(allowed)
        self.assertEqual(rem, 999999)

        from lib.features.chat_responder import IMAGE_GEN_DAILY_LIMIT

        # 2. Other user with 0 used
        mock_load.return_value = {today: {"12345": 0}}
        allowed, rem = can_user_generate_image(12345)
        self.assertTrue(allowed)
        self.assertEqual(rem, IMAGE_GEN_DAILY_LIMIT)

        # 3. Other user one short of the limit
        mock_load.return_value = {today: {"12345": IMAGE_GEN_DAILY_LIMIT - 1}}
        allowed, rem = can_user_generate_image(12345)
        self.assertTrue(allowed)
        self.assertEqual(rem, 1)

        # 4. Other user at the limit (quota reached)
        mock_load.return_value = {today: {"12345": IMAGE_GEN_DAILY_LIMIT}}
        allowed, rem = can_user_generate_image(12345)
        self.assertFalse(allowed)
        self.assertEqual(rem, 0)

        # 5. Record user generation increments count
        mock_load.return_value = {today: {"12345": 1}}
        record_user_image_generation(12345)
        mock_save.assert_called_once()
        saved_data = mock_save.call_args[0][1]
        self.assertEqual(saved_data[today]["12345"], 2)

    @patch("lib.features.chat_responder.generate_image_openai")
    async def test_handle_one_off_image_request_oggers_success(self, mock_gen_img):
        mock_gen_img.return_value = (b"fake_image_bytes", 20, 200)
        client = MagicMock()
        client.user.id = 999999999

        message = MagicMock()
        message.id = 88888888
        message.author.id = USERS.OGGERS
        message.author.name = "ogme01"
        message.content = f"<@{client.user.id}> draw me a cup of tea in the rain"
        message.reply = AsyncMock()

        with patch("lib.features.chat_responder.can_user_generate_image", return_value=(True, 999999)), \
             patch("lib.features.chat_responder.record_user_image_generation"), \
             patch("lib.features.chat_responder.live_chat_manager.update_dashboard", new_callable=AsyncMock):
            res = await handle_one_off_owner_mention(client, message)

        self.assertTrue(res)
        mock_gen_img.assert_called_once()
        self.assertIn("cup of tea in the rain", mock_gen_img.call_args[0][0])
        message.reply.assert_called_once()
        call_kwargs = message.reply.call_args[1]
        self.assertIn("file", call_kwargs)
        self.assertIn("Here's your image", message.reply.call_args[0][0])

    async def test_handle_one_off_image_request_quota_exceeded(self):
        client = MagicMock()
        client.user.id = 999999999

        message = MagicMock()
        message.id = 99999991
        message.author.id = 772553171616006166  # Roshy
        message.author.name = "roshy"
        message.content = f"<@{client.user.id}> generate an image of a pirate ship"
        message.reply = AsyncMock()

        with patch("lib.features.chat_responder.can_user_generate_image", return_value=(False, 0)):
            res = await handle_one_off_owner_mention(client, message)

        self.assertTrue(res)
        message.reply.assert_called_once()
        reply_text = message.reply.call_args[0][0]
        from lib.features.chat_responder import IMAGE_GEN_DAILY_LIMIT
        self.assertIn(f"daily limit of {IMAGE_GEN_DAILY_LIMIT} image generations", reply_text)

    def test_dynamic_image_request_patterns(self):
        from lib.features.chat_responder import looks_like_image_request, is_contextual_image_request

        p1 = "go through @Top chap's message history and generate a photo of what you think they look like"
        self.assertTrue(looks_like_image_request(p1))
        self.assertTrue(is_contextual_image_request(p1))

        p2 = "draw @Top chap"
        self.assertTrue(looks_like_image_request(p2))
        self.assertTrue(is_contextual_image_request(p2))

        p3 = "paint a portrait of me based on my messages"
        self.assertTrue(looks_like_image_request(p3))
        self.assertTrue(is_contextual_image_request(p3))

        p4 = "draw a pirate ship in the fog"
        self.assertTrue(looks_like_image_request(p4))
        self.assertFalse(is_contextual_image_request(p4, other_mentions=[]))

        p5 = "do the same for @Johnny"
        history_with_image = [{"role": "assistant", "content": "[Generated Image: caricature of top chap]"}]
        self.assertTrue(looks_like_image_request(p5, history=history_with_image))
        self.assertTrue(is_contextual_image_request(p5))

    @patch("database.DatabaseManager.fetch_all")
    def test_fetch_user_recent_chat(self, mock_fetch):
        from lib.features.chat_responder import fetch_user_recent_chat, format_user_chat_for_context

        mock_fetch.return_value = [
            ("123", "Portsmouth played brilliantly today", None, 1700000000),
            ("123", "Proper cup of tea that is", None, 1700000100),
        ]
        client = MagicMock()
        ch_mock = MagicMock()
        ch_mock.name = "football-chat"
        client.get_channel.return_value = ch_mock

        res = fetch_user_recent_chat(client, 999888, limit=10)
        self.assertEqual(len(res), 2)
        self.assertEqual(res[0]["content"], "Portsmouth played brilliantly today")
        self.assertEqual(res[0]["channel"], "football-chat")

        formatted = format_user_chat_for_context("Top chap", 999888, res)
        self.assertIn("Top chap", formatted)
        self.assertIn("Portsmouth played brilliantly", formatted)

    @patch("urllib.request.urlopen")
    def test_synthesize_contextual_image_prompt(self, mock_urlopen):
        from lib.features.chat_responder import synthesize_contextual_image_prompt

        fake_resp = {
            "choices": [{
                "message": {
                    "content": json.dumps({
                        "image_prompt": "A comical oil painting of a British football fan in flat cap holding tea",
                        "caption": "Here is Top chap in all their glory. Do try to contain your admiration."
                    })
                }
            }],
            "usage": {"prompt_tokens": 120, "completion_tokens": 45}
        }
        mock_urlopen.return_value = _mock_resp(json.dumps(fake_resp).encode("utf-8"))

        prompt, caption, p_tok, c_tok = synthesize_contextual_image_prompt(
            prompt="generate a photo of what they look like",
            context="RECENT MESSAGE HISTORY FOR Top chap:\n- Portsmouth FC",
            user_name="Oggers",
            caller_role="Owner",
            target_name="Top chap",
            openai_key="test-key"
        )
        self.assertEqual(prompt, "A comical oil painting of a British football fan in flat cap holding tea")
        self.assertEqual(caption, "Here is Top chap in all their glory. Do try to contain your admiration.")
        # image prompt call + caption call
        self.assertEqual(mock_urlopen.call_count, 2)
        self.assertEqual((p_tok, c_tok), (240, 90))

    @patch("lib.features.chat_responder.generate_image_openai")
    @patch("lib.features.chat_responder.synthesize_contextual_image_prompt")
    @patch("lib.features.chat_responder.fetch_user_recent_chat_async")
    async def test_handle_one_off_contextual_image_request_full_flow(self, mock_fetch_chat, mock_synth, mock_gen_img):
        from lib.features.chat_responder import handle_one_off_owner_mention

        mock_fetch_chat.return_value = [
            {"content": "Up the Pompey!", "channel": "general", "ts": 1700000000}
        ]
        mock_synth.return_value = (
            "A satirical caricature of a Portsmouth FC fan in a muddy scarf",
            "<@404634271861571584> I inspected Top chap's records. Here is the tragic result.",
            150, 40
        )
        mock_gen_img.return_value = (b"fake_image_bytes", 20, 200)

        client = MagicMock()
        client.user.id = 999999999

        target_user = MagicMock()
        target_user.id = 11223344
        target_user.name = "topchap"
        target_user.nick = "Top chap"
        target_user.display_name = "Top chap"

        message = MagicMock()
        message.id = 77777777
        message.author.id = USERS.OGGERS
        message.author.name = "ogme01"
        message.content = f"<@{client.user.id}> go through <@{target_user.id}>'s message history and generate a photo of what you think they look like"
        message.mentions = [client.user, target_user]
        message.reference = None
        message.channel.history = MagicMock()
        message.reply = AsyncMock()

        with patch("lib.features.chat_responder.can_user_generate_image", return_value=(True, 999999)), \
             patch("lib.features.chat_responder.record_user_image_generation"), \
             patch("lib.features.chat_responder.live_chat_manager.update_dashboard", new_callable=AsyncMock):
            res = await handle_one_off_owner_mention(client, message)

        self.assertTrue(res)
        mock_synth.assert_called_once()
        synth_call = mock_synth.call_args
        self.assertIn("Top chap", synth_call[1]["target_name"])

        mock_gen_img.assert_called_once()
        self.assertEqual(mock_gen_img.call_args[0][0], "A satirical caricature of a Portsmouth FC fan in a muddy scarf")

        message.reply.assert_called_once()
        reply_caption = message.reply.call_args[0][0]
        self.assertIn("Top chap's records", reply_caption)
        self.assertIn("file", message.reply.call_args[1])

    def test_looks_like_image_edit_request(self):
        from lib.features.chat_responder import looks_like_image_edit_request

        self.assertTrue(looks_like_image_edit_request("bit generous with the hair there"))
        self.assertTrue(looks_like_image_edit_request("give that hair to piggy"))
        self.assertTrue(looks_like_image_edit_request("make him balder"))
        self.assertTrue(looks_like_image_edit_request("remove the Amazon boxes"))
        self.assertTrue(looks_like_image_edit_request("put a pint of Guinness in his hand"))
        self.assertTrue(looks_like_image_edit_request("less hair please"))
        self.assertTrue(looks_like_image_edit_request("change the background to a tavern"))
        self.assertTrue(looks_like_image_edit_request("turn him into a goblin"))

        self.assertFalse(looks_like_image_edit_request("that is hilarious"))
        self.assertFalse(looks_like_image_edit_request("hello vic how are you"))
        self.assertFalse(looks_like_image_edit_request("who won the football match?"))

    @patch("urllib.request.urlopen")
    def test_edit_image_openai_success(self, mock_urlopen):
        import base64
        from lib.features.chat_responder import edit_image_openai

        fake_resp_body = {
            "created": 1700000000,
            "data": [{"b64_json": base64.b64encode(b"edited_png_data").decode("utf-8")}],
            "usage": {"input_tokens": 40, "output_tokens": 180}
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(fake_resp_body).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        img_bytes, p_tok, c_tok = edit_image_openai(
            image_bytes=b"original_png_data",
            prompt="Make him balder",
            openai_key="test-key"
        )
        self.assertEqual(img_bytes, b"edited_png_data")
        self.assertEqual((p_tok, c_tok), (40, 180))

        req = mock_urlopen.call_args[0][0]
        self.assertEqual(req.get_method(), "POST")
        self.assertIn("multipart/form-data", req.get_header("Content-type"))
        self.assertIn(b"gpt-image-2.5-flare", req.data)
        self.assertIn(b"original_png_data", req.data)

    @patch("urllib.request.urlopen")
    def test_synthesize_image_edit_prompt(self, mock_urlopen):
        from lib.features.chat_responder import synthesize_image_edit_prompt

        fake_resp_body = {
            "choices": [{
                "message": {
                    "content": json.dumps({
                        "edit_type": "edit",
                        "image_prompt": "Remove the hair on his crown, making him noticeably balder with receding wisps",
                        "caption": "A haircut conducted with naval efficiency. Try not to blind anyone with the reflection."
                    })
                }
            }],
            "usage": {"prompt_tokens": 110, "completion_tokens": 35}
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(fake_resp_body).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        edit_type, img_prompt, caption, p_tok, c_tok = synthesize_image_edit_prompt(
            prompt="bit generous with the hair there",
            prev_prompt="A caricature of Johnny with thick wavy hair and Amazon boxes",
            user_name="Oggers",
            caller_role="Owner",
            target_name="Johnny",
            openai_key="test-key"
        )
        self.assertEqual(edit_type, "edit")
        self.assertIn("balder", img_prompt)
        self.assertIn("haircut", caption)
        self.assertEqual((p_tok, c_tok), (110, 35))

    @patch("lib.features.chat_responder.edit_image_openai")
    @patch("lib.features.chat_responder.synthesize_image_edit_prompt")
    @patch("lib.features.chat_responder.find_recent_image_attachment")
    async def test_handle_one_off_image_edit_flow(self, mock_find_img, mock_synth_edit, mock_edit_img):
        from lib.features.chat_responder import handle_one_off_owner_mention

        mock_att = MagicMock()
        mock_att.filename = "vic_creation.png"
        mock_att.read = AsyncMock(return_value=b"original_img_bytes")

        prev_msg = MagicMock()
        prev_msg.content = "Here is Johnny's caricature."
        mock_find_img.return_value = (prev_msg, mock_att, "A caricature of Johnny with thick hair")

        mock_synth_edit.return_value = (
            "edit",
            "Make him noticeably balder with thinning hair",
            "<@404634271861571584> I have revised his hairline downward.",
            100, 30
        )
        mock_edit_img.return_value = (b"edited_img_bytes", 35, 190)

        client = MagicMock()
        client.user.id = 999999999

        message = MagicMock()
        message.id = 88888888
        message.author.id = USERS.OGGERS
        message.author.name = "ogme01"
        message.content = f"<@{client.user.id}> bit generous with the hair there"
        message.mentions = [client.user]
        message.reference = MagicMock()
        message.reference.message_id = 77777777
        message.channel.history = MagicMock()
        message.reply = AsyncMock()

        with patch("lib.features.chat_responder.can_user_generate_image", return_value=(True, 999999)), \
             patch("lib.features.chat_responder.record_user_image_generation"), \
             patch("lib.features.chat_responder.live_chat_manager.update_dashboard", new_callable=AsyncMock):
            res = await handle_one_off_owner_mention(client, message)

        self.assertTrue(res)
        mock_find_img.assert_called_once()
        mock_synth_edit.assert_called_once()
        mock_edit_img.assert_called_once()
        self.assertEqual(mock_edit_img.call_args[0][0], b"original_img_bytes")
        self.assertEqual(mock_edit_img.call_args[0][1], "Make him noticeably balder with thinning hair")

        message.reply.assert_called_once()
        caption = message.reply.call_args[0][0]
        self.assertIn("revised his hairline", caption)
        self.assertIn("file", message.reply.call_args[1])

    def test_looks_like_image_request_expanded_followups(self):
        from lib.features.chat_responder import looks_like_image_request

        self.assertTrue(looks_like_image_request("do me xx"))
        self.assertTrue(looks_like_image_request("do me x"))
        self.assertTrue(looks_like_image_request("do me next"))
        self.assertTrue(looks_like_image_request("me next"))
        self.assertTrue(looks_like_image_request("now me"))
        self.assertTrue(looks_like_image_request("my turn"))
        self.assertTrue(looks_like_image_request("what do i look based on my message history"))
        self.assertTrue(looks_like_image_request("what do i look like based on my message history"))
        self.assertTrue(looks_like_image_request("what would i look like"))

    @patch("lib.features.chat_responder.generate_image_openai")
    @patch("lib.features.chat_responder.synthesize_contextual_image_prompt")
    @patch("lib.features.chat_responder.fetch_user_recent_chat_async")
    async def test_handle_one_off_do_me_flow(self, mock_fetch_chat, mock_synth, mock_gen_img):
        from lib.features.chat_responder import handle_one_off_owner_mention

        mock_fetch_chat.return_value = [
            {"content": "I love London and tea.", "channel": "general", "ts": 1700000000}
        ]
        mock_synth.return_value = (
            "A satirical caricature of Oggers as a 19th-century naval captain",
            "<@404634271861571584> Here is your caricature, Oggers.",
            130, 40
        )
        mock_gen_img.return_value = (b"fake_oggers_image", 20, 200)

        client = MagicMock()
        client.user.id = 999999999

        message = MagicMock()
        message.id = 99999991
        message.author.id = USERS.OGGERS
        message.author.name = "ogme01"
        message.author.nick = "Oggers"
        message.author.display_name = "Oggers"
        message.author.global_name = "Oggers"
        message.content = f"<@{client.user.id}> do me xx"
        message.mentions = [client.user]
        message.reference = None
        message.channel.history = MagicMock()
        message.reply = AsyncMock()

        with patch("lib.features.chat_responder.can_user_generate_image", return_value=(True, 999999)), \
             patch("lib.features.chat_responder.record_user_image_generation"), \
             patch("lib.features.chat_responder.live_chat_manager.update_dashboard", new_callable=AsyncMock):
            res = await handle_one_off_owner_mention(client, message)

        self.assertTrue(res)
        mock_synth.assert_called_once()
        synth_call = mock_synth.call_args
        self.assertIn("Oggers", synth_call[1]["target_name"])

        mock_gen_img.assert_called_once()
        message.reply.assert_called_once()
        self.assertIn("Oggers", message.reply.call_args[0][0])
        self.assertIn("file", message.reply.call_args[1])


if __name__ == "__main__":
    unittest.main()

