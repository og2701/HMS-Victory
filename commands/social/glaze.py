import logging
from os import getenv

from discord import AllowedMentions, Member, TextChannel
from openai import AsyncOpenAI

from config import USERS
from lib.features import glazes


client = AsyncOpenAI(api_key=getenv("OPENAI_TOKEN"), max_retries=5, timeout=60.0)
logger = logging.getLogger(__name__)


async def glaze(interaction, channel: TextChannel = None, user: Member = None):
    if interaction.user.id != USERS.OGGERS:
        await interaction.response.send_message("Only OGGERS can use this command for now.", ephemeral=True)
        return

    source_channel = channel or interaction.channel
    user = user or interaction.user
    try:
        await interaction.response.defer(ephemeral=True)
        evidence, images = await glazes.collect_evidence(source_channel, user)
        if not evidence["target_messages"]:
            await interaction.followup.send(
                f"{user.display_name} hasn't posted enough to glaze in this channel lately!",
                ephemeral=True, allowed_mentions=AllowedMentions.none(),
            )
            return

        memory = glazes.load_memory(interaction.guild_id, user.id)
        response = await client.chat.completions.create(
            model="gpt-5.4",
            messages=[
                {"role": "system", "content": glazes.SYSTEM_PROMPT},
                {"role": "user", "content": glazes.model_content(evidence, images, memory)},
            ],
            response_format=glazes.response_format(),
            max_completion_tokens=4096,
        )
        choice = response.choices[0]
        if choice.finish_reason != "stop" or choice.message.refusal or not choice.message.content:
            raise ValueError("Glaze generation was refused, empty or incomplete")
        candidate = glazes.select_glaze(choice.message.content, evidence)
        if candidate is None:
            await interaction.followup.send(
                "There's not enough usable material for a decent glaze yet. Try another channel.",
                ephemeral=True,
            )
            return

        header = (f"✨ {user.mention} ✨\n"
                  f"-# glazed at {interaction.user.display_name}'s request\n\n")
        await interaction.channel.send(
            header + candidate["text"],
            allowed_mentions=AllowedMentions(users=[user], everyone=False, roles=False, replied_user=False),
        )
    except Exception:
        logger.exception("Error in glaze command")
        error = "Couldn't deliver the glaze. Try again in a moment."
        if interaction.response.is_done():
            await interaction.followup.send(error, ephemeral=True)
        else:
            await interaction.response.send_message(error, ephemeral=True)
        return

    # Failures after delivery must never report that the successfully posted glaze failed.
    try:
        await interaction.delete_original_response()
    except Exception:
        logger.debug("Could not clear glaze interaction", exc_info=True)
    try:
        glazes.save_glaze(interaction.guild_id, user, candidate)
    except Exception:
        logger.exception("Could not save glaze memory")
