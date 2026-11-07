"""Flag translations on messages the bot hasn't cached.

on_reaction_add only fires for messages in discord.py's cache, which is empty after every
restart and holds only the last ~1000 messages. Oggers put 🏴 on a message from an hour
before a deploy and nothing happened. The raw reaction event now fetches the message itself.
"""
import os
import sys
import types
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

for name in ("selenium", "selenium.webdriver", "selenium.webdriver.chrome",
             "selenium.webdriver.chrome.service", "selenium.webdriver.chrome.options",
             "selenium.webdriver.common", "selenium.webdriver.common.by",
             "webdriver_manager", "webdriver_manager.chrome"):
    if name not in sys.modules:
        mod = types.ModuleType(name)
        for attr in ("Service", "Options", "Chrome", "By", "ChromeDriverManager"):
            setattr(mod, attr, MagicMock)
        sys.modules[name] = mod

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OPENAI_TOKEN", "mock-token")

from lib.bot import event_handlers as eh

ENGLAND = "🏴\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f"


def _payload(emoji=ENGLAND, channel_id=1341454747139575869, bot=False, guild_id=1):
    member = MagicMock(id=404634271861571584, bot=bot)
    return MagicMock(emoji=emoji, channel_id=channel_id, message_id=1568690130732982493,
                     guild_id=guild_id, member=member)


def _client(message, cached=()):
    channel = MagicMock()
    channel.fetch_message = AsyncMock(return_value=message)
    client = MagicMock()
    client.cached_messages = list(cached)
    client.get_channel = MagicMock(return_value=channel)
    return client, channel


def _message(emoji=ENGLAND):
    reaction = MagicMock()
    reaction.emoji = emoji
    msg = MagicMock(id=1568690130732982493, reactions=[reaction])
    return msg, reaction


class UncachedFlagReaction(unittest.IsolatedAsyncioTestCase):
    async def test_uncached_message_is_fetched_and_translated(self):
        msg, reaction = _message()
        client, channel = _client(msg)
        payload = _payload()
        with patch.object(eh, "handle_flag_reaction", new_callable=AsyncMock) as h:
            await eh.handle_uncached_flag_reaction(client, payload)
        channel.fetch_message.assert_awaited_once_with(payload.message_id)
        h.assert_awaited_once_with(reaction, msg, payload.member)

    async def test_cached_message_is_left_to_on_reaction_add(self):
        msg, _ = _message()
        client, channel = _client(msg, cached=[msg])
        with patch.object(eh, "handle_flag_reaction", new_callable=AsyncMock) as h:
            await eh.handle_uncached_flag_reaction(client, _payload())
        channel.fetch_message.assert_not_called()
        h.assert_not_called()

    async def test_non_flag_bot_dm_and_blacklisted_are_ignored(self):
        msg, _ = _message()
        blacklisted = next(iter(eh.TRANSLATION_BLACKLIST_CHANNELS), None)
        cases = [_payload(emoji="👍"), _payload(bot=True), _payload(guild_id=None)]
        if blacklisted is not None:
            cases.append(_payload(channel_id=blacklisted))
        for p in cases:
            client, channel = _client(msg)
            with patch.object(eh, "handle_flag_reaction", new_callable=AsyncMock) as h:
                await eh.handle_uncached_flag_reaction(client, p)
            channel.fetch_message.assert_not_called()
            h.assert_not_called()

    async def test_reaction_already_gone_does_nothing(self):
        msg, _ = _message(emoji="🇫🇷")
        client, _ = _client(msg)
        with patch.object(eh, "handle_flag_reaction", new_callable=AsyncMock) as h:
            await eh.handle_uncached_flag_reaction(client, _payload())
        h.assert_not_called()

    async def test_raw_handler_runs_it_before_hall_of_fame(self):
        calls = []
        with patch.object(eh, "handle_uncached_flag_reaction",
                          new=AsyncMock(side_effect=lambda *a: calls.append("flag"))), \
             patch.object(eh, "check_hall_of_fame",
                          new=AsyncMock(side_effect=lambda *a: calls.append("hof"))):
            client = MagicMock()
            client.get_channel = MagicMock(return_value=None)
            client.fetch_channel = AsyncMock(return_value=MagicMock(parent_id=None))
            await eh.on_raw_reaction_add(client, _payload())
        self.assertEqual(calls[:2], ["flag", "hof"])


if __name__ == "__main__":
    unittest.main()
