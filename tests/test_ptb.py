import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from commands.social import ptb
from config import USERS


def _make_interaction(user_id=USERS.OGGERS, channel=None, voice_client=None, guild_present=True):
    voice_state = SimpleNamespace(channel=channel) if channel is not None else None
    user = SimpleNamespace(id=user_id, voice=voice_state)
    guild = (
        SimpleNamespace(
            voice_client=voice_client,
            get_member=lambda uid: user,
        )
        if guild_present
        else None
    )
    interaction = SimpleNamespace(
        user=user,
        guild=guild,
        response=SimpleNamespace(
            send_message=AsyncMock(),
            defer=AsyncMock(),
        ),
        followup=SimpleNamespace(
            send=AsyncMock(),
        ),
    )
    return interaction


def test_ptb_rejects_unauthorised_user():
    interaction = _make_interaction(user_id=123456)
    asyncio.run(ptb.handle_ptb_command(interaction))
    interaction.response.send_message.assert_awaited_once()
    args, kwargs = interaction.response.send_message.await_args
    assert "permission" in args[0]
    assert kwargs.get("ephemeral") is True


def test_ptb_allows_chin():
    assert USERS.CHIN in ptb.PTB_ALLOWED_USER_IDS
    interaction = _make_interaction(user_id=USERS.CHIN, channel=None)
    asyncio.run(ptb.handle_ptb_command(interaction))
    interaction.response.send_message.assert_awaited_once()
    args, _ = interaction.response.send_message.await_args
    assert "voice channel" in args[0]


def test_ptb_requires_voice_channel():
    interaction = _make_interaction(user_id=USERS.OGGERS, channel=None)
    asyncio.run(ptb.handle_ptb_command(interaction))
    interaction.response.send_message.assert_awaited_once()
    args, kwargs = interaction.response.send_message.await_args
    assert "voice channel" in args[0]
    assert kwargs.get("ephemeral") is True


def test_ptb_joins_plays_and_disconnects():
    vc = MagicMock()
    vc.is_playing.return_value = False
    vc.is_paused.return_value = False
    vc.is_connected.return_value = True
    vc.disconnect = AsyncMock()

    channel = SimpleNamespace(
        id=999,
        mention="<#999>",
        connect=AsyncMock(return_value=vc),
    )
    interaction = _make_interaction(user_id=USERS.OGGERS, channel=channel)

    async def _run():
        with patch("commands.social.ptb.discord.FFmpegPCMAudio") as mock_audio:
            await ptb.handle_ptb_command(interaction)
            channel.connect.assert_awaited_once()
            mock_audio.assert_called_once_with(ptb.PTB_AUDIO_PATH)
            vc.play.assert_called_once()
            after_cb = vc.play.call_args.kwargs["after"]
            after_cb(None)
            await asyncio.sleep(0.6)
            vc.disconnect.assert_awaited_once()

    asyncio.run(_run())
    interaction.followup.send.assert_awaited_once()
