import asyncio
import logging
import os
import discord
from config import BASE_DIR, USERS

logger = logging.getLogger(__name__)

PTB_AUDIO_PATH = os.path.join(BASE_DIR, "data", "audio", "ptb.mp3")
PTB_ALLOWED_USER_IDS = {USERS.OGGERS, USERS.CHIN}


async def handle_ptb_command(interaction: discord.Interaction):
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

    async def _disconnect_when_done(vc: discord.VoiceClient, err: Exception | None):
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
        logger.exception("Failed to start /ptb playback")
        try:
            await voice_client.disconnect(force=True)
        except Exception:
            pass
        await interaction.followup.send(
            f"Joined {channel.mention}, but failed to play audio: `{e}`", ephemeral=True
        )
        return

    await interaction.followup.send(
        f"🔴 Pushing the button in {channel.mention}!", ephemeral=True
    )
