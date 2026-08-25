"""Unit tests for the Bouncy Yaris emoji chain reaction."""

import pytest
import config
from unittest.mock import AsyncMock, MagicMock
from lib.features.yaris_chain import (
    has_yaris_emoji,
    record_and_check_yaris,
    reset_yaris_state,
    handle_yaris_chain,
    BOUNCY_YARIS_EMOJI,
)


@pytest.fixture(autouse=True)
def clean_state():
    reset_yaris_state()
    yield
    reset_yaris_state()


def test_has_yaris_emoji_detection():
    assert has_yaris_emoji("<a:Bouncy_Yaris:1540334892317933599>") is True
    assert has_yaris_emoji("<:Bouncy_Yaris:1540334892317933599>") is True
    assert has_yaris_emoji("<a:bouncy_yaris:123456>") is True
    assert has_yaris_emoji(":Bouncy_Yaris:") is True
    assert has_yaris_emoji(":bouncy_yaris:") is True
    assert has_yaris_emoji("Check this out :Bouncy_Yaris: haha") is True
    assert has_yaris_emoji("<a:Bouncy_Yaris:1540334892317933599> hype") is True

    assert has_yaris_emoji("") is False
    assert has_yaris_emoji("regular message") is False
    assert has_yaris_emoji("<:other_emoji:12345>") is False
    assert has_yaris_emoji(":yaris:") is False


def test_single_user_multiple_posts_does_not_trigger():
    now = 1_000_000.0
    # User 1 posts 5 times
    assert record_and_check_yaris(101, now=now) is False
    assert record_and_check_yaris(101, now=now + 10) is False
    assert record_and_check_yaris(101, now=now + 20) is False
    assert record_and_check_yaris(101, now=now + 30) is False
    assert record_and_check_yaris(101, now=now + 40) is False


def test_three_distinct_users_triggers_on_third():
    now = 1_000_000.0
    assert record_and_check_yaris(101, now=now) is False
    assert record_and_check_yaris(102, now=now + 15) is False
    # 3rd distinct user within 2 mins
    assert record_and_check_yaris(103, now=now + 30) is True


def test_posts_older_than_two_minutes_expire():
    now = 1_000_000.0
    assert record_and_check_yaris(101, now=now) is False
    assert record_and_check_yaris(102, now=now + 60) is False
    # User 103 posts 130s after User 101 -> User 101 expired, so only 2 active users (102 and 103)
    assert record_and_check_yaris(103, now=now + 130) is False
    # User 104 posts within 2 mins of 102 and 103 -> triggers!
    assert record_and_check_yaris(104, now=now + 140) is True


@pytest.mark.asyncio
async def test_handle_yaris_chain_end_to_end():
    general_id = config.CHANNELS.GENERAL

    client = MagicMock()

    # Channel mock
    channel = MagicMock()
    channel.id = general_id
    channel.send = AsyncMock()

    # User 1
    m1 = MagicMock()
    m1.author.bot = False
    m1.author.id = 201
    m1.channel = channel
    m1.content = "look at this <a:Bouncy_Yaris:1540334892317933599>"

    # User 2
    m2 = MagicMock()
    m2.author.bot = False
    m2.author.id = 202
    m2.channel = channel
    m2.content = ":Bouncy_Yaris:"

    # User 3
    m3 = MagicMock()
    m3.author.bot = False
    m3.author.id = 203
    m3.channel = channel
    m3.content = "<a:Bouncy_Yaris:1540334892317933599>"

    res1 = await handle_yaris_chain(client, m1)
    assert res1 is False
    channel.send.assert_not_called()

    res2 = await handle_yaris_chain(client, m2)
    assert res2 is False
    channel.send.assert_not_called()

    res3 = await handle_yaris_chain(client, m3)
    assert res3 is True
    channel.send.assert_called_once_with(BOUNCY_YARIS_EMOJI)


@pytest.mark.asyncio
async def test_handle_yaris_chain_ignores_other_channels_and_bots():
    client = MagicMock()

    # Other channel
    other_channel = MagicMock()
    other_channel.id = 123456789
    other_channel.send = AsyncMock()

    m = MagicMock()
    m.author.bot = False
    m.author.id = 301
    m.channel = other_channel
    m.content = "<a:Bouncy_Yaris:1540334892317933599>"

    assert await handle_yaris_chain(client, m) is False
    other_channel.send.assert_not_called()

    # Bot author in general
    general_channel = MagicMock()
    general_channel.id = config.CHANNELS.GENERAL
    general_channel.send = AsyncMock()

    bot_msg = MagicMock()
    bot_msg.author.bot = True
    bot_msg.author.id = 999
    bot_msg.channel = general_channel
    bot_msg.content = "<a:Bouncy_Yaris:1540334892317933599>"

    assert await handle_yaris_chain(client, bot_msg) is False
    general_channel.send.assert_not_called()
