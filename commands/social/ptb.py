import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass

import aiohttp
import discord
from config import BASE_DIR, ROLES, USERS

logger = logging.getLogger(__name__)

AUDIO_DIR = os.path.join(BASE_DIR, "data", "audio")
PTB_ALLOWED_USER_IDS = {USERS.OGGERS, USERS.CHIN}
PTB_ALLOWED_ROLE_IDS = {ROLES.CABINET}

# How many seconds before a line is sung to advance/display it so people can sing along.
LYRIC_LEAD_SECONDS = 1.2

LRCLIB_URL = "https://lrclib.net/api/get/{id}"
LRCLIB_HEADERS = {"User-Agent": "HMS-Victory /ptb (Discord bot)"}

# How long the song picker stays up before it deletes itself.
PICKER_TIMEOUT_SECONDS = 60

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


# The other songs' synced lyrics come from LRCLIB (a free synced-lyrics database) when they're
# played, rather than living in this file. Each id pins the entry whose timings match the
# recording in data/audio, and lyrics_offset shifts them if a recording starts later.
@dataclass(frozen=True)
class Song:
    key: str
    label: str
    emoji: str
    style: discord.ButtonStyle
    audio_file: str
    lyrics: list[tuple[float, str]] | None = None
    lrclib_id: int | None = None
    lyrics_offset: float = 0.0

    @property
    def audio_path(self) -> str:
        return os.path.join(AUDIO_DIR, self.audio_file)


SONGS: list[Song] = [
    Song("ptb", "Push the Button", "🔴", discord.ButtonStyle.danger, "ptb.mp3", lyrics=PTB_LYRICS),
    Song("dream", "I Dreamed a Dream", "💭", discord.ButtonStyle.primary, "dream.mp3", lrclib_id=1275159),
    # Checked against a transcript of the recording: the singing lands ~0.4s before LRCLIB's times.
    Song("spice", "Spice Up Your Life", "🌶️", discord.ButtonStyle.success, "spice.mp3",
         lrclib_id=37172069, lyrics_offset=-0.4),
    Song("ready", "Ready or Not", "🙈", discord.ButtonStyle.secondary, "ready.mp3", lrclib_id=34334522),
    # Also checked against a transcript: Celine comes in ~0.9s before LRCLIB's times.
    Song("heart", "My Heart Will Go On", "🚢", discord.ButtonStyle.primary, "heart.mp3",
         lrclib_id=36975441, lyrics_offset=-0.9),
    Song("party", "We Like to Party", "🚌", discord.ButtonStyle.success, "party.mp3", lrclib_id=37169387),
    Song("brightside", "Mr. Brightside", "🌗", discord.ButtonStyle.danger, "brightside.mp3", lrclib_id=36963370),
    # Rick James comes in ~0.4s before LRCLIB's times, like Spice.
    Song("superfreak", "Super Freak", "🕺", discord.ButtonStyle.secondary, "superfreak.mp3",
         lrclib_id=37148279, lyrics_offset=-0.4),
    Song("talkingbody", "Talking Body", "💋", discord.ButtonStyle.danger, "talkingbody.mp3", lrclib_id=37467113),
    Song("girls", "Girls", "💃", discord.ButtonStyle.primary, "girls.mp3", lrclib_id=37228854),
]

_LRC_TAG = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")
_lyrics_loads: dict[str, asyncio.Task] = {}
_active_lyrics_task: asyncio.Task | None = None


def parse_lrc(text: str, offset: float = 0.0) -> list[tuple[float, str]]:
    """Turn LRC text ("[mm:ss.xx] line", sometimes several tags on one line) into sorted
    (seconds, line) pairs, dropping the blank lines LRC uses to mark instrumental gaps."""
    lines = []
    for raw in text.splitlines():
        rest = raw.strip()
        stamps = []
        while match := _LRC_TAG.match(rest):
            stamps.append(int(match.group(1)) * 60 + float(match.group(2)))
            rest = rest[match.end():].lstrip()
        if rest:
            lines.extend((stamp + offset, rest) for stamp in stamps)
    return sorted(lines, key=lambda line: line[0])


async def _fetch_lrclib(lrclib_id: int) -> str | None:
    # LRCLIB answers about a third of requests with a 503, so keep trying for a few seconds.
    url = LRCLIB_URL.format(id=lrclib_id)
    timeout = aiohttp.ClientTimeout(total=6)
    for attempt in range(4):
        try:
            async with aiohttp.ClientSession(timeout=timeout, headers=LRCLIB_HEADERS) as session:
                async with session.get(url) as resp:
                    resp.raise_for_status()
                    return (await resp.json()).get("syncedLyrics")
        except Exception as e:
            logger.info("LRCLIB fetch for %s failed (try %d): %s", lrclib_id, attempt + 1, e)
            await asyncio.sleep(0.5 * (attempt + 1))
    logger.warning("Gave up fetching LRCLIB lyrics %s", lrclib_id)
    return None


async def load_lyrics(song: Song) -> list[tuple[float, str]]:
    if song.lyrics is not None:
        return song.lyrics
    if song.lrclib_id is None:
        return []

    # Each fetched entry is kept next to the audio, so a song needs LRCLIB up only once.
    cache_path = os.path.join(AUDIO_DIR, f"{song.key}-{song.lrclib_id}.lrc")
    if os.path.isfile(cache_path):
        with open(cache_path, encoding="utf-8") as f:
            synced = f.read()
    else:
        synced = await _fetch_lrclib(song.lrclib_id)
        if synced:
            try:
                with open(cache_path, "w", encoding="utf-8") as f:
                    f.write(synced)
            except OSError:
                logger.debug("Could not cache /ptb lyrics", exc_info=True)
    return parse_lrc(synced or "", song.lyrics_offset)


def lyrics_for(song: Song) -> asyncio.Task:
    """One shared lyrics load per song, started when the picker opens so it's usually done
    by the time a button is pressed. A load that came back empty is retried next time."""
    task = _lyrics_loads.get(song.key)
    if task is None or (task.done() and (task.cancelled() or task.exception() or not task.result())):
        task = asyncio.create_task(load_lyrics(song))
        _lyrics_loads[song.key] = task
    return task


def build_cues(lyrics: list[tuple[float, str]]) -> list[tuple[float, str, str | None]]:
    # Group lyrics into 2-line cues so people can read ahead and sing along smoothly,
    # while staying comfortably within Discord's channel send rate limits.
    return [
        (lyrics[i][0], lyrics[i][1], lyrics[i + 1][1] if i + 1 < len(lyrics) else None)
        for i in range(0, len(lyrics), 2)
    ]


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
    lyrics,
    started_at: float,
):
    try:
        # Shielded so stopping this stream doesn't cancel the shared lyrics load.
        cues = build_cues(await asyncio.shield(lyrics))
        for idx, (ts, line1, line2) in enumerate(cues):
            following = cues[idx + 1] if idx + 1 < len(cues) else None
            target_elapsed = max(0.0, ts - LYRIC_LEAD_SECONDS)
            now_elapsed = time.monotonic() - started_at
            wait_sec = target_elapsed - now_elapsed
            if wait_sec > 0:
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=wait_sec)
                    return
                except asyncio.TimeoutError:
                    pass
            elif following and following[0] - LYRIC_LEAD_SECONDS <= now_elapsed:
                # The lyrics arrived late, so skip cues the song has already moved past.
                continue

            if stop_event.is_set() or not voice_client.is_connected():
                return

            text = format_lyric_cue(line1, line2, following[1] if following else None)
            try:
                await dest_channel.send(text)
            except Exception:
                logger.debug("Could not send /ptb lyric line", exc_info=True)
                return
    except asyncio.CancelledError:
        return


def can_use_ptb(user) -> bool:
    return user.id in PTB_ALLOWED_USER_IDS or any(
        role.id in PTB_ALLOWED_ROLE_IDS for role in getattr(user, "roles", None) or []
    )


def _voice_channel_of(interaction: discord.Interaction):
    member = interaction.user
    if not isinstance(member, discord.Member):
        member = interaction.guild.get_member(interaction.user.id)
    voice_state = getattr(member, "voice", None)
    return getattr(voice_state, "channel", None)


class SongPicker(discord.ui.View):
    """The ephemeral row of song buttons /ptb opens. Picking one deletes it and starts the song."""

    def __init__(self, origin: discord.Interaction):
        super().__init__(timeout=PICKER_TIMEOUT_SECONDS)
        self.origin = origin
        self.picked = False
        for song in SONGS:
            button = discord.ui.Button(label=song.label, emoji=song.emoji, style=song.style)
            button.callback = self._picker_for(song)
            self.add_item(button)

    def _picker_for(self, song: Song):
        async def callback(interaction: discord.Interaction):
            await interaction.response.defer()
            if self.picked:
                return
            self.picked = True
            self.stop()
            try:
                await interaction.delete_original_response()
            except discord.HTTPException:
                logger.debug("Could not delete the /ptb picker", exc_info=True)
            await play_song(interaction, song)

        return callback

    async def on_timeout(self):
        try:
            await self.origin.delete_original_response()
        except discord.HTTPException:
            pass


async def handle_ptb_command(interaction: discord.Interaction):
    if not can_use_ptb(interaction.user):
        await interaction.response.send_message(
            "You do not have permission to push the button.", ephemeral=True
        )
        return

    if not interaction.guild:
        await interaction.response.send_message(
            "This command can only be used in a server.", ephemeral=True
        )
        return

    channel = _voice_channel_of(interaction)
    if channel is None:
        await interaction.response.send_message(
            "You need to be in a voice channel first!", ephemeral=True
        )
        return

    for song in SONGS:
        lyrics_for(song)
    await interaction.response.send_message(
        f"🎤 Pick a song for {channel.mention}", view=SongPicker(interaction), ephemeral=True
    )


async def play_song(interaction: discord.Interaction, song: Song):
    """Join the picker's voice channel, play the song and stream its lyrics into the channel's
    chat. Runs after the button press has been deferred, so problems go out as followups."""
    global _active_lyrics_task

    channel = _voice_channel_of(interaction)
    if channel is None:
        await interaction.followup.send("You need to be in a voice channel first!", ephemeral=True)
        return

    if not os.path.isfile(song.audio_path):
        await interaction.followup.send("Audio file is missing on the server.", ephemeral=True)
        return

    if _active_lyrics_task and not _active_lyrics_task.done():
        _active_lyrics_task.cancel()

    # Usually already loaded by the picker; if not, it finishes while we join, since every
    # song's first line is several seconds in.
    lyrics = lyrics_for(song)

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
        source = discord.FFmpegPCMAudio(song.audio_path)
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
    started_at = time.monotonic()

    # Prefer the voice channel itself (its built-in text chat) or fallback to interaction.channel
    lyrics_channel = channel if hasattr(channel, "send") else interaction.channel
    if lyrics_channel is not None:
        _active_lyrics_task = asyncio.create_task(
            _stream_lyrics(voice_client, lyrics_channel, stop_event, lyrics, started_at)
        )
