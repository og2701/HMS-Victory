import asyncio
import os
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
        channel=SimpleNamespace(send=AsyncMock()),
        response=SimpleNamespace(
            send_message=AsyncMock(),
            defer=AsyncMock(),
        ),
        followup=SimpleNamespace(
            send=AsyncMock(),
        ),
        delete_original_response=AsyncMock(),
    )
    return interaction


def _voice_channel():
    vc = MagicMock()
    vc.is_playing.return_value = False
    vc.is_paused.return_value = False
    vc.is_connected.return_value = True
    vc.disconnect = AsyncMock()
    channel = SimpleNamespace(
        id=999,
        mention="<#999>",
        connect=AsyncMock(return_value=vc),
        send=AsyncMock(),
    )
    return channel, vc


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


def test_ptb_allows_cabinet():
    interaction = _make_interaction(user_id=123456, channel=None)
    interaction.user.roles = [SimpleNamespace(id=ptb.ROLES.CABINET)]
    asyncio.run(ptb.handle_ptb_command(interaction))
    args, _ = interaction.response.send_message.await_args
    assert "voice channel" in args[0]


def test_ptb_requires_voice_channel():
    interaction = _make_interaction(user_id=USERS.OGGERS, channel=None)
    asyncio.run(ptb.handle_ptb_command(interaction))
    interaction.response.send_message.assert_awaited_once()
    args, kwargs = interaction.response.send_message.await_args
    assert "voice channel" in args[0]
    assert kwargs.get("ephemeral") is True


def test_every_song_has_its_audio():
    for song in ptb.SONGS:
        assert os.path.isfile(song.audio_path), song.audio_file
        assert song.lyrics is not None or song.lrclib_id is not None, song.key


def test_ptb_opens_an_ephemeral_picker_with_a_button_per_song():
    channel, _ = _voice_channel()
    interaction = _make_interaction(user_id=USERS.OGGERS, channel=channel)

    async def _run():
        with patch.object(ptb, "lyrics_for") as preload:
            await ptb.handle_ptb_command(interaction)
        assert preload.call_count == len(ptb.SONGS)

    asyncio.run(_run())
    _, kwargs = interaction.response.send_message.await_args
    assert kwargs.get("ephemeral") is True
    assert [b.label for b in kwargs["view"].children] == [s.label for s in ptb.SONGS]


def test_picking_a_song_deletes_the_picker_and_plays_it():
    origin = _make_interaction()
    click = _make_interaction()

    async def _run():
        picker = ptb.SongPicker(origin)
        with patch.object(ptb, "play_song", AsyncMock()) as play:
            await picker.children[1].callback(click)
            await picker.children[2].callback(click)
        play.assert_awaited_once_with(click, ptb.SONGS[1])

    asyncio.run(_run())
    click.delete_original_response.assert_awaited_once()


def test_parse_lrc():
    text = "[ar:Someone]\n[00:01.50] first\n[00:03.00]\n[01:02.25][01:10.00] chorus\n"
    assert ptb.parse_lrc(text) == [(1.5, "first"), (62.25, "chorus"), (70.0, "chorus")]
    assert ptb.parse_lrc("[00:02.00] shifted", offset=-0.5) == [(1.5, "shifted")]


def test_lyrics_load_from_the_cache_beside_the_audio(tmp_path):
    song = ptb.Song("test", "Test", "🎵", ptb.discord.ButtonStyle.primary, "x.mp3", lrclib_id=42)
    (tmp_path / "test-42.lrc").write_text("[00:05.00] cached line\n", encoding="utf-8")

    async def _run():
        with patch.object(ptb, "AUDIO_DIR", str(tmp_path)), \
             patch.object(ptb, "_fetch_lrclib", AsyncMock()) as fetch:
            assert await ptb.load_lyrics(song) == [(5.0, "cached line")]
            fetch.assert_not_awaited()

    asyncio.run(_run())


def test_format_lyric_cue():
    cue = ptb.format_lyric_cue("Push the button", "Pu-push the button", "I'm busy throwing hints")
    assert "🎤 **Push the button**" in cue
    assert "🎶 **Pu-push the button**" in cue
    assert "-# ⏭️ *I'm busy throwing hints...*" in cue


def test_play_song_joins_plays_streams_lyrics_and_disconnects():
    channel, vc = _voice_channel()
    interaction = _make_interaction(user_id=USERS.OGGERS, channel=channel)
    song = ptb.Song(
        "ptb", "Push the Button", "🔴", ptb.discord.ButtonStyle.danger, "ptb.mp3",
        lyrics=[(0.0, "Push the button"), (0.1, "Push the button")],
    )

    async def _run():
        ptb._lyrics_loads.clear()
        with patch("commands.social.ptb.discord.FFmpegPCMAudio") as mock_audio:
            await ptb.play_song(interaction, song)
            await asyncio.sleep(0.05)
            channel.connect.assert_awaited_once()
            mock_audio.assert_called_once_with(song.audio_path)
            vc.play.assert_called_once()
            channel.send.assert_awaited_once()
            after_cb = vc.play.call_args.kwargs["after"]
            after_cb(None)
            await asyncio.sleep(0.6)
            vc.disconnect.assert_awaited_once()
        ptb._lyrics_loads.clear()

    asyncio.run(_run())
    interaction.followup.send.assert_not_awaited()
