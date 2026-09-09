import sys
import types
import unittest
from unittest.mock import MagicMock, patch
import time

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

    discord.AllowedMentions = MockAllowedMentions

    ui = types.ModuleType("discord.ui")

    class _Item:
        def __init_subclass__(cls, **kwargs):
            super().__init_subclass__()

        def __init__(self, *a, **k):
            self.callback = None
            self.value = ""
            self.default = k.get("default", "")
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
    ONE_OFF_SYSTEM_PROMPT,
    gather_one_off_context,
    generate_one_off_reply,
    handle_one_off_owner_mention,
    handle_chat_message,
    live_chat_manager,
    sanitize_ai_mentions,
)
from config import USERS


class TestLiveChatResponder(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        live_chat_manager.last_reply_time = 0
        live_chat_manager.stop(clear_target=True)

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

    @patch("urllib.request.urlopen")
    def test_generate_one_off_reply_payload(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"choices":[{"message":{"content":"A witty poem about Johnny."}}],"usage":{"prompt_tokens":120,"completion_tokens":45}}'
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        content, p_tok, c_tok = generate_one_off_reply(
            prompt="write a poem about this user",
            context="RECENT CHAT IN #general:\nJohnny: I hate tea",
            openai_key="test-key",
        )
        self.assertEqual(content, "A witty poem about Johnny.")
        self.assertEqual(p_tok, 120)
        self.assertEqual(c_tok, 45)

        # Verify request payload
        call_args, _ = mock_urlopen.call_args
        req = call_args[0]
        import json
        payload = json.loads(req.data.decode("utf-8"))
        self.assertEqual(payload["max_tokens"], 200)
        self.assertEqual(payload["model"], "gpt-4o")
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertIn("Johnny: I hate tea", payload["messages"][1]["content"])
        self.assertIn("write a poem about this user", payload["messages"][1]["content"])

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
    async def test_handle_chat_message_oggers_hi_vic_asleep(self, mock_handle_one_off):
        mock_handle_one_off.return_value = True
        client = MagicMock()
        client.user.id = 999999999

        message = MagicMock()
        message.author.bot = False
        message.author.id = USERS.OGGERS
        message.mentions = []
        message.reference = None
        message.content = "hi vic"

        live_chat_manager.active = False
        res = await handle_chat_message(client, message)
        self.assertTrue(res)
        mock_handle_one_off.assert_called_once_with(client, message)

    @patch("lib.features.chat_responder.handle_one_off_owner_mention")
    async def test_handle_chat_message_other_user_asleep(self, mock_handle_one_off):
        client = MagicMock()
        client.user.id = 999999999

        message = MagicMock()
        message.author.bot = False
        message.author.id = 11223344  # Not Oggers
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
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"choices":[{"message":{"content":"A witty critique of the meme."}}],"usage":{"prompt_tokens":150,"completion_tokens":25}}'
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        content, p_tok, c_tok = generate_one_off_reply(
            prompt="what is in this image",
            context="",
            image_urls=["https://cdn.discordapp.com/attachments/123/456/kaizo.png"],
            openai_key="test-key",
        )
        self.assertEqual(content, "A witty critique of the meme.")
        call_args, _ = mock_urlopen.call_args
        req = call_args[0]
        import json
        payload = json.loads(req.data.decode("utf-8"))
        user_msg = payload["messages"][1]
        self.assertIsInstance(user_msg["content"], list)
        self.assertEqual(user_msg["content"][0]["type"], "text")
        self.assertEqual(user_msg["content"][1]["type"], "image_url")
        self.assertEqual(user_msg["content"][1]["image_url"]["url"], "https://cdn.discordapp.com/attachments/123/456/kaizo.png")

    @patch("urllib.request.urlopen")
    def test_perform_web_search_html(self, mock_urlopen):
        from lib.features.chat_responder import perform_web_search

        html_body = b"""
        <html>
            <a class="result__a" href="https://example.com/1">Premier League Table 2026</a>
            <a class="result__snippet" href="https://example.com/1">Arsenal are top of the table after 28 games.</a>
            <a class="result__a" href="https://example.com/2">BBC Football News</a>
            <a class="result__snippet" href="https://example.com/2">All the latest scores and match reports.</a>
        </html>
        """
        mock_resp = MagicMock()
        mock_resp.read.return_value = html_body
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        res = perform_web_search("premier league table")
        self.assertIn("Premier League Table 2026", res)
        self.assertIn("Arsenal are top of the table", res)
        self.assertIn("BBC Football News", res)

    @patch("urllib.request.urlopen")
    def test_perform_web_search_fallback_to_api(self, mock_urlopen):
        from lib.features.chat_responder import perform_web_search

        # 1st call (HTML) fails, 2nd call (API) succeeds
        html_resp = MagicMock()
        html_resp.read.return_value = b"<html>No results found</html>"
        html_resp.__enter__.return_value = html_resp

        api_resp = MagicMock()
        api_resp.read.return_value = b'{"Heading": "Arsenal F.C.", "AbstractText": "A football club in North London."}'
        api_resp.__enter__.return_value = api_resp

        mock_urlopen.side_effect = [html_resp, api_resp]

        res = perform_web_search("arsenal fc")
        self.assertIn("Arsenal F.C.: A football club in North London.", res)

    @patch("urllib.request.urlopen")
    def test_generate_one_off_reply_with_web_search_tool(self, mock_urlopen):
        from lib.features.chat_responder import perform_web_search
        import json

        # Step 1: Model decides to call web_search
        step1_json = json.dumps({
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "call_123",
                        "type": "function",
                        "function": {
                            "name": "web_search",
                            "arguments": "{\"query\": \"latest premier league scores\"}"
                        }
                    }]
                }
            }],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20}
        }).encode("utf-8")

        # Step 2: DuckDuckGo HTML search
        ddg_html = b"""
        <html>
            <a class="result__a">Chelsea 2-1 Fulham</a>
            <a class="result__snippet">Chelsea secured a 2-1 victory over Fulham at Stamford Bridge.</a>
        </html>
        """

        # Step 3: Follow-up model completion with final answer
        step2_json = json.dumps({
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "Chelsea beat Fulham 2-1 at Stamford Bridge earlier today."
                }
            }],
            "usage": {"prompt_tokens": 180, "completion_tokens": 35}
        }).encode("utf-8")

        resp1 = MagicMock()
        resp1.read.return_value = step1_json
        resp1.__enter__.return_value = resp1

        resp_ddg = MagicMock()
        resp_ddg.read.return_value = ddg_html
        resp_ddg.__enter__.return_value = resp_ddg

        resp2 = MagicMock()
        resp2.read.return_value = step2_json
        resp2.__enter__.return_value = resp2

        mock_urlopen.side_effect = [resp1, resp_ddg, resp2]

        content, p_tok, c_tok = generate_one_off_reply(
            prompt="what were the scores today?",
            openai_key="test-key",
            enable_search=True,
        )

        self.assertEqual(content, "Chelsea beat Fulham 2-1 at Stamford Bridge earlier today.")
        self.assertEqual(p_tok, 100 + 180)
        self.assertEqual(c_tok, 20 + 35)
        self.assertEqual(mock_urlopen.call_count, 3)

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

        claude_bot_id = 1457814413913489480
        live_chat_manager.set_target_user(claude_bot_id)

        client = MagicMock()
        client.user.id = 999999999

        # Target bot message
        bot_message = MagicMock()
        bot_message.id = 888111
        bot_message.author.id = claude_bot_id
        bot_message.author.name = "Claude AI"
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
        mock_load.return_value = {"target_user_id": 1457814413913489480}
        mgr = LiveChatManager()
        self.assertEqual(mgr.target_user_id, 1457814413913489480)

        # Setting target user should persist to disk
        mgr.set_target_user(987654321)
        self.assertEqual(mgr.target_user_id, 987654321)
        mock_save.assert_called_with({"target_user_id": 987654321})

        # Disabling target user should also persist None
        mgr.set_target_user(None)
        self.assertIsNone(mgr.target_user_id)
        mock_save.assert_called_with({"target_user_id": None})


if __name__ == "__main__":
    unittest.main()

