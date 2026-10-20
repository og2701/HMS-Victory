import logging
from datetime import datetime, timezone
from os import getenv

from discord import AllowedMentions, Member, TextChannel
from openai import AsyncOpenAI

from commands.social.roast import roast
from config import GLAZE_DAILY_LIMIT, ROLES, USERS
from database import DatabaseManager
from lib.features import glazes
from lib.features.member_context import channel_problem


client = AsyncOpenAI(api_key=getenv("OPENAI_TOKEN"), max_retries=5, timeout=60.0)
logger = logging.getLogger(__name__)
# Boosters and staff get the glaze; anyone else who asks for one is roasted instead.
GLAZE_ROLES = {ROLES.SERVER_BOOSTER, ROLES.DEPUTY_PM, ROLES.CABINET, ROLES.BORDER_FORCE, ROLES.PCSO}


def can_glaze(member):
    return member.id == USERS.OGGERS or any(role.id in GLAZE_ROLES for role in getattr(member, "roles", ()))


async def glaze(interaction, channel: TextChannel = None, user: Member = None, about: str = None, style: str = None):
    if not can_glaze(interaction.user):
        name = interaction.user.display_name
        wanted = "themselves" if user is None or user.id == interaction.user.id else user.display_name
        await roast(interaction, channel, interaction.user,
                    note=f"{name} tried to glaze {wanted}. Glazes are for boosters, so {name} gets roasted instead")
        return

    source_channel = channel or interaction.channel
    user = user or interaction.user
    about = " ".join((about or "").split())[:glazes.ABOUT_LIMIT] or None
    chosen = glazes.STYLE_CHOICES.get(style)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    requester_id = str(interaction.user.id)
    reserved = False
    delivered = False

    try:
        problem = channel_problem(interaction, source_channel)
        if problem:
            await interaction.response.send_message(problem, ephemeral=True)
            return
        if interaction.user.id != USERS.OGGERS:
            # Reserve atomically so concurrent requests cannot exceed the daily limit.
            reserved = DatabaseManager.execute(
                "INSERT INTO glaze_usage (user_id, date, count) VALUES (?, ?, 1) "
                "ON CONFLICT(user_id, date) DO UPDATE SET count = count + 1 "
                "WHERE count < ?",
                (requester_id, today, GLAZE_DAILY_LIMIT),
            ) == 1
            if not reserved:
                await interaction.response.send_message(
                    f"You've used all {GLAZE_DAILY_LIMIT} of today's glazes. They're back tomorrow.",
                    ephemeral=True,
                )
                return

        await interaction.response.defer(ephemeral=True)
        evidence, images = await glazes.collect_evidence(source_channel, user, requester=interaction.user)
        if not evidence["target_messages"]:
            await interaction.followup.send(
                f"{user.display_name} hasn't posted enough to glaze in this channel lately!",
                ephemeral=True, allowed_mentions=AllowedMentions.none(),
            )
            return

        memory = glazes.load_memory(interaction.guild_id, user.id)
        forms = glazes.offered_forms(glazes.recent_forms(interaction.guild_id), chosen)
        response = await client.chat.completions.create(
            model="gpt-5.4",
            messages=[
                {"role": "system", "content": glazes.system_prompt(forms, chosen is not None)},
                {"role": "user", "content": glazes.model_content(evidence, images, memory, about)},
            ],
            response_format=glazes.response_format(forms),
            max_completion_tokens=4096,
        )
        choice = response.choices[0]
        if choice.finish_reason != "stop" or choice.message.refusal or not choice.message.content:
            raise ValueError("Glaze generation was refused, empty or incomplete")
        candidate = glazes.select_glaze(choice.message.content, evidence, forms)
        if candidate is None:
            await interaction.followup.send(
                "There's not enough usable material for a decent glaze yet. Try another channel.",
                ephemeral=True,
            )
            return

        await interaction.channel.send(
            glazes.header(user, interaction.user, candidate["form"]) + candidate["text"],
            allowed_mentions=AllowedMentions(users=[user], everyone=False, roles=False, replied_user=False),
        )
        delivered = True

        # Failures after delivery must never report that the successfully posted glaze failed.
        try:
            await interaction.delete_original_response()
        except Exception:
            logger.debug("Could not clear glaze interaction", exc_info=True)
        try:
            glazes.save_glaze(interaction.guild_id, user, candidate)
        except Exception:
            logger.exception("Could not save glaze memory")
    except Exception:
        logger.exception("Error in glaze command")
        error = "Couldn't deliver the glaze. Try again in a moment."
        if interaction.response.is_done():
            await interaction.followup.send(error, ephemeral=True)
        else:
            await interaction.response.send_message(error, ephemeral=True)
    finally:
        if reserved and not delivered:
            try:
                DatabaseManager.execute(
                    "UPDATE glaze_usage SET count = MAX(0, count - 1) WHERE user_id = ? AND date = ?",
                    (requester_id, today),
                )
            except Exception:
                logger.exception("Could not refund failed glaze usage")
