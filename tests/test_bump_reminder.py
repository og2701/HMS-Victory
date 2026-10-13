import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import config
from lib.features import ukp_rewards


def test_handle_bump_reward_saves_reminder_and_sends_to_bot_spam():
    client = SimpleNamespace(
        get_channel=lambda cid: SimpleNamespace(send=AsyncMock()) if cid == config.CHANNELS.BOT_SPAM else None,
    )
    user = SimpleNamespace(id=12345, bot=False)
    meta = SimpleNamespace(user=user)
    msg = SimpleNamespace(
        id=999999,
        author=SimpleNamespace(id=config.DISBOARD_BOT_ID),
        content="",
        embeds=[SimpleNamespace(title="DISBOARD: The Public Server List", description="Bump done! 👍")],
        interaction_metadata=meta,
        channel=SimpleNamespace(send=AsyncMock()),
    )

    store_mock = {}

    with patch("lib.features.ukp_rewards.load_json_file", return_value=store_mock), \
         patch("lib.features.ukp_rewards.save_json_file") as mock_save, \
         patch("lib.features.ukp_rewards._pay", return_value=True):
        asyncio.run(ukp_rewards.handle_bump_reward(client, msg))

        assert store_mock.get("last_msg_id") == 999999
        assert store_mock.get("reminder_sent") is False
        assert "next_reminder_ts" in store_mock
        mock_save.assert_called_once()


def test_check_bump_reminder_pings_bumpity_bumps_in_bot_spam():
    bot_spam = SimpleNamespace(send=AsyncMock())
    client = SimpleNamespace(
        get_channel=lambda cid: bot_spam if cid == config.CHANNELS.BOT_SPAM else None,
    )

    now = int(time.time())
    store_mock = {
        "next_reminder_ts": now - 10,
        "reminder_sent": False,
    }

    with patch("lib.features.ukp_rewards.load_json_file", return_value=store_mock), \
         patch("lib.features.ukp_rewards.save_json_file") as mock_save:
        asyncio.run(ukp_rewards.check_bump_reminder(client))

        assert store_mock["reminder_sent"] is True
        mock_save.assert_called_once()
        bot_spam.send.assert_awaited_once()
        call_kwargs = bot_spam.send.call_args.kwargs
        call_args = bot_spam.send.call_args.args
        content = call_kwargs.get("content") or (call_args[0] if call_args else "")
        assert str(config.ROLES.BUMPITY_BUMPS) in content
        assert "embed" in call_kwargs
