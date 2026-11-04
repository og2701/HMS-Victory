import asyncio
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import types

if "selenium" not in sys.modules:
    selenium = types.ModuleType("selenium")
    webdriver = types.ModuleType("webdriver")
    chrome = types.ModuleType("chrome")
    service = types.ModuleType("service")
    service.Service = MagicMock
    chrome.service = service
    options = types.ModuleType("options")
    options.Options = MagicMock
    chrome.options = options
    webdriver.chrome = chrome
    webdriver.Chrome = MagicMock
    common = types.ModuleType("common")
    by = types.ModuleType("by")
    by.By = MagicMock
    common.by = by
    webdriver.common = common
    selenium.webdriver = webdriver
    sys.modules["selenium"] = selenium
    sys.modules["selenium.webdriver"] = webdriver
    sys.modules["selenium.webdriver.chrome"] = chrome
    sys.modules["selenium.webdriver.chrome.service"] = service
    sys.modules["selenium.webdriver.chrome.options"] = options
    sys.modules["selenium.webdriver.common"] = common
    sys.modules["selenium.webdriver.common.by"] = by

if "webdriver_manager" not in sys.modules:
    wm = types.ModuleType("webdriver_manager")
    wm_chrome = types.ModuleType("chrome")
    wm_chrome.ChromeDriverManager = MagicMock
    wm.chrome = wm_chrome
    sys.modules["webdriver_manager"] = wm
    sys.modules["webdriver_manager.chrome"] = wm_chrome

import os
os.environ.setdefault("OPENAI_TOKEN", "mock-token")

import discord
from config import CHANNELS, ROLES
from commands.social.hof import handle_hof_context_menu
from lib.bot.event_handlers import check_hall_of_fame


class TestHallOfFameBotMessages(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bot_id = 1171842947440967770
        self.client = MagicMock()
        self.client.user = MagicMock(id=self.bot_id)

    @patch("lib.bot.event_handlers.load_json_file", return_value=[])
    @patch("lib.bot.event_handlers.save_json_file")
    @patch("lib.bot.event_handlers.award_badge_with_notify", new_callable=AsyncMock)
    @patch("lib.features.ukp_rewards.award_hof_reward", new_callable=AsyncMock)
    @patch("commands.social.hof.send_hof_post", new_callable=AsyncMock)
    async def test_other_bot_message_ignored(
        self, mock_send_hof, mock_ukp, mock_badge, mock_save, mock_load
    ):
        # Message from another bot (e.g. Ballsdex)
        channel = MagicMock()
        channel.id = 123456
        channel.type = discord.ChannelType.text

        msg = MagicMock()
        msg.id = 999111
        msg.author.bot = True
        msg.author.id = 555555  # Different bot
        msg.channel = channel
        msg.created_at = discord.utils.utcnow()

        channel.fetch_message = AsyncMock(return_value=msg)
        self.client.get_channel = MagicMock(return_value=channel)

        payload = MagicMock(channel_id=channel.id, message_id=msg.id)
        await check_hall_of_fame(self.client, payload)

        mock_send_hof.assert_not_called()
        mock_badge.assert_not_called()
        mock_ukp.assert_not_called()

    @patch("lib.bot.event_handlers.load_json_file", return_value=[])
    @patch("lib.bot.event_handlers.save_json_file")
    @patch("lib.bot.event_handlers.award_badge_with_notify", new_callable=AsyncMock)
    @patch("lib.features.ukp_rewards.award_hof_reward", new_callable=AsyncMock)
    @patch("commands.social.hof.send_hof_post", new_callable=AsyncMock)
    async def test_bot_own_message_ignored(
        self, mock_send_hof, mock_ukp, mock_badge, mock_save, mock_load
    ):
        # Message from HMS Victory itself
        channel = MagicMock()
        channel.id = 123456
        channel.type = discord.ChannelType.text

        thread = MagicMock()
        self.client.get_channel = MagicMock(
            side_effect=lambda cid: thread if cid == CHANNELS.HALL_OF_FAME_THREAD else channel
        )

        msg = MagicMock()
        msg.id = 888222
        msg.webhook_id = None
        msg.author.bot = True
        msg.author.id = self.bot_id  # The bot itself!
        msg.channel = channel
        msg.created_at = discord.utils.utcnow()

        # 6 unique reactors
        react1 = MagicMock(count=6)

        async def iter_users():
            for i in range(1, 7):
                yield MagicMock(id=i)

        react1.users = iter_users
        msg.reactions = [react1]

        channel.fetch_message = AsyncMock(return_value=msg)

        payload = MagicMock(channel_id=channel.id, message_id=msg.id)
        await check_hall_of_fame(self.client, payload)

        # Should NOT send HOF post, save, or award badges
        mock_send_hof.assert_not_called()
        mock_save.assert_not_called()
        mock_badge.assert_not_called()
        mock_ukp.assert_not_called()

    @patch("lib.bot.event_handlers.load_json_file", return_value=[])
    @patch("lib.bot.event_handlers.save_json_file")
    @patch("lib.bot.event_handlers.award_badge_with_notify", new_callable=AsyncMock)
    @patch("lib.features.ukp_rewards.award_hof_reward", new_callable=AsyncMock)
    @patch("commands.social.hof.send_hof_post", new_callable=AsyncMock)
    async def test_human_message_qualifies_with_badge_and_ukp(
        self, mock_send_hof, mock_ukp, mock_badge, mock_save, mock_load
    ):
        # Message from a human user
        channel = MagicMock()
        channel.id = 123456
        channel.type = discord.ChannelType.text

        thread = MagicMock()
        self.client.get_channel = MagicMock(
            side_effect=lambda cid: thread if cid == CHANNELS.HALL_OF_FAME_THREAD else channel
        )

        msg = MagicMock()
        msg.id = 777333
        msg.webhook_id = None
        msg.author.bot = False
        msg.author.id = 424242
        msg.channel = channel
        msg.created_at = discord.utils.utcnow()

        react1 = MagicMock(count=6)

        async def iter_users():
            for i in range(1, 7):
                yield MagicMock(id=i)

        react1.users = iter_users
        msg.reactions = [react1]

        channel.fetch_message = AsyncMock(return_value=msg)

        payload = MagicMock(channel_id=channel.id, message_id=msg.id)
        await check_hall_of_fame(self.client, payload)

        mock_send_hof.assert_called_once_with(self.client, thread, msg)
        mock_badge.assert_called_once_with(self.client, 424242, 'hof')
        mock_ukp.assert_called_once_with(self.client, 424242)

    def _bot_context_menu_setup(self, author_id, thread_history=()):
        interaction = MagicMock()
        interaction.client = self.client
        interaction.user.roles = [MagicMock(id=ROLES.DEPUTY_PM)]
        interaction.response.send_message = AsyncMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        thread = MagicMock()
        self.client.get_channel = MagicMock(return_value=thread)

        msg = MagicMock()
        msg.id = 111222
        msg.author.bot = True
        msg.author.id = author_id
        msg.channel.id = 123456
        msg.content = ""
        msg.attachments = []
        msg.message_snapshots = []
        msg.jump_url = "https://discord.com/channels/1/123456/111222"
        return interaction, thread, msg

    @patch("commands.social.hof.save_json_file")
    @patch("commands.social.hof.load_json_file", return_value=[])
    @patch("lib.bot.event_handlers.award_badge_with_notify", new_callable=AsyncMock)
    @patch("lib.features.ukp_rewards.award_hof_reward", new_callable=AsyncMock)
    @patch("commands.social.hof.send_hof_post", new_callable=AsyncMock)
    async def test_context_menu_adds_bot_own_embed_without_rewards(
        self, mock_send_hof, mock_ukp, mock_badge, mock_load, mock_save
    ):
        interaction, thread, msg = self._bot_context_menu_setup(self.bot_id)
        msg.embeds = [discord.Embed(title="🎰 Mahdi's VIP Role Case - RESULT")]
        msg.components = []

        await handle_hof_context_menu(interaction, msg)

        mock_send_hof.assert_called_once_with(self.client, thread, msg)
        mock_save.assert_called_once()
        self.assertIn("111222", mock_save.call_args[0][1])
        mock_badge.assert_not_called()
        mock_ukp.assert_not_called()
        interaction.followup.send.assert_called_with("Added to the Hall of Fame!", ephemeral=True)

    @patch("commands.social.hof.save_json_file")
    @patch("commands.social.hof.load_json_file", return_value=[])
    @patch("lib.bot.event_handlers.award_badge_with_notify", new_callable=AsyncMock)
    @patch("lib.features.ukp_rewards.award_hof_reward", new_callable=AsyncMock)
    @patch("commands.social.hof.send_hof_post", new_callable=AsyncMock)
    async def test_context_menu_adds_components_v2_bot_message(
        self, mock_send_hof, mock_ukp, mock_badge, mock_load, mock_save
    ):
        from discord.components import _component_factory

        interaction, thread, msg = self._bot_context_menu_setup(999999)
        msg.embeds = []
        msg.components = [_component_factory({
            "type": 17, "components": [{"type": 10, "content": "## 💷 No 10k bonds yet"}],
        })]

        await handle_hof_context_menu(interaction, msg)

        mock_send_hof.assert_called_once_with(self.client, thread, msg)
        mock_badge.assert_not_called()
        mock_ukp.assert_not_called()

    @patch("commands.social.hof.load_json_file", return_value=["111222"])
    @patch("commands.social.hof._hof_post_exists", new_callable=AsyncMock, return_value=True)
    @patch("commands.social.hof.send_hof_post", new_callable=AsyncMock)
    async def test_context_menu_bot_message_dedupes(self, mock_send_hof, mock_exists, mock_load):
        interaction, thread, msg = self._bot_context_menu_setup(self.bot_id)
        msg.embeds = [discord.Embed(title="x")]
        msg.components = []

        await handle_hof_context_menu(interaction, msg)

        mock_send_hof.assert_not_called()
        interaction.followup.send.assert_called_with(
            "This message is already in the Hall of Fame.", ephemeral=True
        )

    async def test_context_menu_empty_components_v2_still_rejected(self):
        interaction, thread, msg = self._bot_context_menu_setup(self.bot_id)
        msg.embeds = []
        msg.components = []

        await handle_hof_context_menu(interaction, msg)

        interaction.response.send_message.assert_called_with(
            "This message has nothing to add.", ephemeral=True
        )


class TestComponentsV2Rendering(unittest.TestCase):
    def _msg(self, components):
        msg = MagicMock()
        msg.components = components
        return msg

    def test_container_becomes_embed_and_buttons_dropped(self):
        from discord.components import _component_factory
        from lib.core.log_functions import components_v2_embeds, components_v2_text

        container = _component_factory({
            "type": 17, "accent_color": 0xE67E22,
            "components": [
                {"type": 10, "content": "## 🎰 Case result\nYou got a **30 minute** timeout"},
                {"type": 9, "components": [{"type": 10, "content": "-# small print"}],
                 "accessory": {"type": 11, "media": {"url": "https://cdn.example.com/t.png"}}},
                {"type": 12, "items": [{"media": {"url": "https://cdn.example.com/a.png"}}]},
                {"type": 1, "components": [{"type": 2, "style": 2, "label": "Try Again", "custom_id": "x"}]},
            ],
        })
        embeds = components_v2_embeds(self._msg([container]))
        self.assertEqual(len(embeds), 1)
        e = embeds[0]
        self.assertEqual(e.title, "🎰 Case result")
        self.assertEqual(e.description, "You got a 30 minute timeout\nsmall print")
        self.assertEqual(e.colour.value, 0xE67E22)
        self.assertEqual(e.image.url, "https://cdn.example.com/a.png")
        self.assertNotIn("Try Again", components_v2_text(self._msg([container])))

    def test_no_components_gives_nothing(self):
        from lib.core.log_functions import components_v2_embeds
        self.assertEqual(components_v2_embeds(self._msg([])), [])
        self.assertEqual(components_v2_embeds(MagicMock()), [])

    def test_announcement_names_triggering_user_for_bot(self):
        from commands.social.hof import hof_announcement
        msg = MagicMock()
        msg.author.bot = True
        msg.author.display_name = "HMS Victory"
        msg.interaction_metadata.user.bot = False
        msg.interaction_metadata.user.id = 42
        msg.interaction_metadata.user.mention = "<@42>"
        self.assertEqual(
            hof_announcement(msg),
            "🏆 HMS Victory's message (for <@42>) made it to the Hall of Fame!",
        )
        msg.interaction_metadata = None
        self.assertEqual(hof_announcement(msg), "🏆 HMS Victory's message made it to the Hall of Fame!")


class TestQuoteImage(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = MagicMock()

    @patch("lib.core.log_functions.screenshot_html", new_callable=AsyncMock, return_value=b"fake-png-bytes")
    @patch("lib.core.log_functions.get_avatar_data_uri", new_callable=AsyncMock, return_value="data:image/png;base64,123")
    async def test_create_quote_image_handles_enum_reference_type(self, mock_avatar, mock_screenshot):
        from lib.core.log_functions import create_quote_image

        msg = MagicMock()
        msg.content = "Look at this horse"
        msg.created_at = discord.utils.utcnow()
        msg.author.display_name = "Chin"
        msg.author.display_avatar.url = "http://example.com/avatar.png"
        msg.author.default_avatar.url = "http://example.com/default.png"
        msg.attachments = []
        msg.embeds = []
        msg.snapshots = []

        # Reference with discord.MessageReferenceType.default enum
        ref = MagicMock()
        ref.type = discord.MessageReferenceType.default
        ref.message_id = 987654
        ref.channel_id = 123456
        ref.resolved = None

        replied_msg = MagicMock()
        replied_msg.author.display_name = "Kaizo"
        replied_msg.author.display_avatar.url = "http://example.com/kaizo.png"
        replied_msg.author.default_avatar.url = "http://example.com/default.png"
        replied_msg.content = "I hate horses"
        replied_msg.attachments = []
        replied_msg.embeds = []

        msg.reference = ref
        msg.channel.fetch_message = AsyncMock(return_value=replied_msg)

        # Should not raise TypeError: int() argument must be...
        buf = await create_quote_image(self.client, msg)
        self.assertIsNotNone(buf)


if __name__ == "__main__":
    unittest.main()
