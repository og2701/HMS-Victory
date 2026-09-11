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
    async def test_bot_own_message_qualifies_without_badge_or_ukp(
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

        # Should send HOF post
        mock_send_hof.assert_called_once_with(self.client, thread, msg)
        mock_save.assert_called_once()
        # Should NOT award badge or UKP to the bot
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

    @patch("commands.social.hof.load_json_file", return_value=[])
    @patch("commands.social.hof.save_json_file")
    @patch("lib.bot.event_handlers.award_badge_with_notify", new_callable=AsyncMock)
    @patch("lib.features.ukp_rewards.award_hof_reward", new_callable=AsyncMock)
    @patch("commands.social.hof.send_hof_post", new_callable=AsyncMock)
    async def test_context_menu_allows_bot_own_message(
        self, mock_send_hof, mock_ukp, mock_badge, mock_save, mock_load
    ):
        # Context menu used by Deputy PM on the bot's own message
        interaction = MagicMock()
        interaction.client = self.client
        role = MagicMock(id=ROLES.DEPUTY_PM)
        interaction.user.roles = [role]
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        thread = MagicMock()
        self.client.get_channel = MagicMock(return_value=thread)

        msg = MagicMock()
        msg.id = 111222
        msg.author.bot = True
        msg.author.id = self.bot_id
        msg.channel.id = 123456
        msg.content = "I am a sentient toaster"
        msg.attachments = []
        msg.embeds = []

        await handle_hof_context_menu(interaction, msg)

        mock_send_hof.assert_called_once_with(self.client, thread, msg)
        mock_save.assert_called_once()
        mock_badge.assert_not_called()
        mock_ukp.assert_not_called()
        interaction.followup.send.assert_called_with("Added to the Hall of Fame!", ephemeral=True)

    async def test_context_menu_rejects_other_bot(self):
        interaction = MagicMock()
        interaction.client = self.client
        role = MagicMock(id=ROLES.DEPUTY_PM)
        interaction.user.roles = [role]
        interaction.response.send_message = AsyncMock()

        msg = MagicMock()
        msg.id = 333444
        msg.author.bot = True
        msg.author.id = 999999  # Other bot
        msg.channel.id = 123456

        await handle_hof_context_menu(interaction, msg)

        interaction.response.send_message.assert_called_with(
            "Other bot messages can't be added to the Hall of Fame.", ephemeral=True
        )

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
