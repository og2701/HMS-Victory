import sys
import types
import unittest
from unittest.mock import MagicMock, patch, AsyncMock
import time
import json
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

        # 2. Other user with 0 used
        mock_load.return_value = {today: {"12345": 0}}
        allowed, rem = can_user_generate_image(12345)
        self.assertTrue(allowed)
        self.assertEqual(rem, 3)

        # 3. Other user with 2 used
        mock_load.return_value = {today: {"12345": 2}}
        allowed, rem = can_user_generate_image(12345)
        self.assertTrue(allowed)
        self.assertEqual(rem, 1)

        # 4. Other user with 3 used (quota reached)
        mock_load.return_value = {today: {"12345": 3}}
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
        self.assertIn("daily limit of 3 image generations", reply_text)


if __name__ == "__main__":
    unittest.main()

