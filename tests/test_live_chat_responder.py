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
)
from config import USERS


class TestLiveChatResponder(unittest.IsolatedAsyncioTestCase):
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
        self.assertEqual(payload["max_tokens"], 350)
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

        context = await gather_one_off_context(client, message)
        self.assertIn("DIRECT REPLY TARGET", context)
        self.assertIn("Steven (@steven_smith)", context)
        self.assertIn("Beans on toast is overrated", context)
        self.assertIn("MENTIONED USERS IN PROMPT: Johnny (@john_doe)", context)
        self.assertIn("RECENT CHAT IN #general", context)

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
        mock_gather.assert_called_once_with(client, message)
        mock_generate.assert_called_once()
        self.assertEqual(mock_generate.call_args[1]["prompt"], "write a poem about this user")


if __name__ == "__main__":
    unittest.main()

