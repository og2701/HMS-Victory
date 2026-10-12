import asyncio
import logging
import os
import time
import discord
from config import BASE_DIR, USERS

logger = logging.getLogger(__name__)

PTB_AUDIO_PATH = os.path.join(BASE_DIR, "data", "audio", "ptb.mp3")
PTB_ALLOWED_USER_IDS = {USERS.OGGERS, USERS.CHIN}

# How many seconds before a line is sung to advance/display it so people can sing along.
LYRIC_LEAD_SECONDS = 1.2

# Synced lyrics (timestamp in seconds, lyric line) for Sugababes - Push the Button.
PTB_LYRICS: list[tuple[float, str]] = [
    (2.66, "Push the button"),
    (6.49, "Push the button"),
    (9.92, "Pu-push the button"),
    (14.28, "Push the button"),
    (15.48, "I'm busy throwing hints that he keeps missing"),
    (19.26, "Don't have to think about it, I wanna kiss and"),
    (23.23, "Everything around it, but he's too distant"),
    (26.93, "I wanna feel his body, I can't resist it"),
    (30.64, "I know my hidden looks can be deceiving"),
    (34.37, "But how obvious should a girl be?"),
    (37.75, "I was taken by the early conversation piece and"),
    (42.19, "I really like the way that he respects me"),
    (46.08, "I've been waiting patiently for him to come and get it"),
    (49.63, "I wonder if he knows that he can say it and I'm with it"),
    (53.56, "I knew I had my mind made up from the very beginning"),
    (57.40, "Catch this opportunity so you and me could feel it, 'cause"),
    (61.67, "If you're ready for me, boy, you better"),
    (65.49, "Push the button and let me know before I"),
    (69.11, "Get the wrong idea and go, you're gonna"),
    (73.17, "Miss the freak that I control"),
    (76.40, "I'm busy showing him what he's been missin'"),
    (80.15, "I'm kind of showing off for his full attention"),
    (83.94, "My sexy ass has got him in the new dimension"),
    (87.85, "I'm ready to do somethin' to relieve this mission"),
    (91.77, "After waiting patiently for him to come and get it"),
    (95.47, "He came on through and asked me if I wanted to get with him"),
    (99.14, "I knew I had my mind made up from the very beginning"),
    (103.07, "Won't miss this opportunity so you and me could feel it, 'cause"),
    (107.27, "If you're ready for me, boy, you better"),
    (111.18, "Push the button and let me know before I"),
    (114.93, "Get the wrong idea and go, you're gonna"),
    (118.73, "Miss the freak that I control"),
    (122.48, "If you're ready for me, boy, you better"),
    (126.36, "Push the button and let me know before I"),
    (130.12, "Get the wrong idea and go, you're gonna"),
    (134.07, "Miss the freak that I control"),
    (138.42, "I've been dropping so many hints (push the button, pu-push the button)"),
    (142.06, "And you're still not getting it (pu-push the button, pu-push the button)"),
    (145.30, "Now that you've heard everything I have to say (push the button, pu-push the button)"),
    (150.07, "Where we gonna go from here? (Pu-push the button, push the button)"),
    (152.93, "After waiting patiently for him to come and get it (push the button)"),
    (156.26, "He came on through and asked me if I wanted to get with him (push the button)"),
    (160.16, "I knew I had my mind made up from the very beginning (push the button)"),
    (163.91, "Won't miss this opportunity so you and me could feel it, 'cause (push the button)"),
    (168.39, "If you're ready for me, boy, you better (for me, boy)"),
    (172.13, "Push the button and let me know before I (let me know)"),
    (175.92, "Get the wrong idea and go, you're gonna"),
    (179.67, "Miss the freak that I control"),
    (183.52, "If you're ready for me, boy, you better (push the button, baby)"),
    (187.33, "Push the button and let me know before I (push the button, babe)"),
    (191.04, "Get the wrong idea and go, you're gonna (push the button, baby)"),
    (195.03, "Miss the freak that I control (I control)"),
    (198.79, "If you're ready for me, boy, you better"),
    (202.59, "Push the button and let me know before I"),
    (206.39, "Get the wrong idea and go, you're gonna"),
    (210.26, "Miss the freak that I control"),
]

# Group lyrics into 2-line cues so people can read ahead and sing along smoothly,
# while staying comfortably within Discord's channel send rate limits.
PTB_CUES: list[tuple[float, str, str | None]] = [
    (
        PTB_LYRICS[i][0],
        PTB_LYRICS[i][1],
        PTB_LYRICS[i + 1][1] if i + 1 < len(PTB_LYRICS) else None,
    )
    for i in range(0, len(PTB_LYRICS), 2)
]

_active_lyrics_task: asyncio.Task | None = None


def format_lyric_cue(line1: str, line2: str | None = None, next_line: str | None = None) -> str:
    parts = [f"🎤 **{line1}**"]
    if line2:
        parts.append(f"🎶 **{line2}**")
    if next_line:
        parts.append(f"-# ⏭️ *{next_line}...*")
    return "\n".join(parts)


async def _stream_lyrics(
    voice_client: discord.VoiceClient,
    dest_channel,
    stop_event: asyncio.Event,
):
    start = time.monotonic()
    try:
        for idx, (ts, line1, line2) in enumerate(PTB_CUES):
            target_elapsed = max(0.0, ts - LYRIC_LEAD_SECONDS)
            now_elapsed = time.monotonic() - start
            wait_sec = target_elapsed - now_elapsed
            if wait_sec > 0:
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=wait_sec)
                    return
                except asyncio.TimeoutError:
                    pass

            if stop_event.is_set() or not voice_client.is_connected():
                return

            next_line = PTB_CUES[idx + 1][1] if idx + 1 < len(PTB_CUES) else None
            text = format_lyric_cue(line1, line2, next_line)
            try:
                await dest_channel.send(text)
            except Exception:
                logger.debug("Could not send /ptb lyric line", exc_info=True)
                return
    except asyncio.CancelledError:
        return


async def handle_ptb_command(interaction: discord.Interaction):
    global _active_lyrics_task

    if interaction.user.id not in PTB_ALLOWED_USER_IDS:
        await interaction.response.send_message(
            "You do not have permission to push the button.", ephemeral=True
        )
        return

    if not interaction.guild:
        await interaction.response.send_message(
            "This command can only be used in a server.", ephemeral=True
        )
        return

    member = interaction.user
    if not isinstance(member, discord.Member):
        member = interaction.guild.get_member(interaction.user.id)

    voice_state = getattr(member, "voice", None)
    channel = getattr(voice_state, "channel", None)
    if channel is None:
        await interaction.response.send_message(
            "You need to be in a voice channel first!", ephemeral=True
        )
        return

    if not os.path.isfile(PTB_AUDIO_PATH):
        await interaction.response.send_message(
            "Audio file is missing on the server.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    if _active_lyrics_task and not _active_lyrics_task.done():
        _active_lyrics_task.cancel()

    voice_client: discord.VoiceClient = interaction.guild.voice_client
    try:
        if voice_client is not None:
            if voice_client.is_playing() or voice_client.is_paused():
                voice_client.stop()
            if voice_client.channel.id != channel.id:
                await voice_client.move_to(channel)
            elif not voice_client.is_connected():
                await voice_client.disconnect(force=True)
                voice_client = await channel.connect()
        else:
            voice_client = await channel.connect()
    except Exception as e:
        logger.exception("Failed to connect to voice channel for /ptb")
        await interaction.followup.send(
            f"Couldn't join {channel.mention}: `{e}`", ephemeral=True
        )
        return

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    async def _disconnect_when_done(vc: discord.VoiceClient, err: Exception | None):
        stop_event.set()
        if err:
            logger.error(f"Error during /ptb audio playback: {err}")
        try:
            # Give ffmpeg buffer a brief moment to flush before disconnecting
            await asyncio.sleep(0.5)
            if vc.is_connected() and not vc.is_playing():
                await vc.disconnect()
        except Exception:
            logger.debug("Error disconnecting voice client after /ptb", exc_info=True)

    def _after_play(error: Exception | None):
        asyncio.run_coroutine_threadsafe(_disconnect_when_done(voice_client, error), loop)

    try:
        source = discord.FFmpegPCMAudio(PTB_AUDIO_PATH)
        voice_client.play(source, after=_after_play)
    except Exception as e:
        stop_event.set()
        logger.exception("Failed to start /ptb playback")
        try:
            await voice_client.disconnect(force=True)
        except Exception:
            pass
        await interaction.followup.send(
            f"Joined {channel.mention}, but failed to play audio: `{e}`", ephemeral=True
        )
        return

    # Prefer the voice channel itself (its built-in text chat) or fallback to interaction.channel
    lyrics_channel = channel if hasattr(channel, "send") else interaction.channel
    if lyrics_channel is not None:
        _active_lyrics_task = asyncio.create_task(
            _stream_lyrics(voice_client, lyrics_channel, stop_event)
        )

    await interaction.followup.send(
        f"🔴 Pushing the button in {channel.mention}!", ephemeral=True
    )
