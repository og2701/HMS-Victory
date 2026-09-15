import logging
from datetime import datetime, timezone
from os import getenv

from discord import AllowedMentions, Member, TextChannel
from openai import AsyncOpenAI

from config import ROAST_DAILY_LIMIT, USERS
from database import DatabaseManager
from lib.features import roasts


client = AsyncOpenAI(api_key=getenv("OPENAI_TOKEN"), max_retries=5, timeout=60.0)
logger = logging.getLogger(__name__)


async def roast(interaction, channel: TextChannel = None, user: Member = None):
    source_channel = channel or interaction.channel
    user = user or interaction.user
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    requester_id = str(interaction.user.id)
    reserved = False
    delivered = False

    try:
        if interaction.user.id != USERS.OGGERS:
            # Reserve atomically so concurrent requests cannot exceed the daily limit.
            reserved = DatabaseManager.execute(
                "INSERT INTO roast_usage (user_id, date, count) VALUES (?, ?, 1) "
                "ON CONFLICT(user_id, date) DO UPDATE SET count = count + 1 "
                "WHERE count < ?",
                (requester_id, today, ROAST_DAILY_LIMIT),
            ) == 1
            if not reserved:
                await interaction.response.send_message(
                    f"You've hit the daily limit of {ROAST_DAILY_LIMIT} usages for this command",
                    ephemeral=True,
                )
                return

        await interaction.response.defer(ephemeral=True)
        evidence, images = await roasts.collect_evidence(source_channel, user)
        if not evidence["target_messages"]:
            await interaction.followup.send(
                f"{user.display_name} hasn't posted enough to roast in this channel lately!",
                ephemeral=True, allowed_mentions=AllowedMentions.none(),
            )
            return

        memory = roasts.load_memory(interaction.guild_id, user.id)
        stray_ids = roasts.eligible_strays(evidence, interaction.guild)
        response = await client.chat.completions.create(
            model="gpt-5.4",
            messages=[
                {"role": "system", "content": roasts.SYSTEM_PROMPT},
                {"role": "user", "content": roasts.model_content(evidence, images, memory, stray_ids)},
            ],
            response_format=roasts.response_format(stray_ids),
            # Three short drafts plus reasoning, in a single request.
            max_completion_tokens=4096,
        )
        choice = response.choices[0]
        if choice.finish_reason != "stop" or choice.message.refusal or not choice.message.content:
            raise ValueError("Roast generation was refused, empty or incomplete")
        candidate = roasts.select_roast(choice.message.content, evidence, stray_ids)
        if candidate is None:
            await interaction.followup.send(
                "There's not enough usable material for a decent roast yet. Try another channel.",
                ephemeral=True,
            )
            return

        header = (f"🔥 {user.mention} 🔥\n"
                  f"-# roasted at {interaction.user.display_name}'s request\n\n")
        await interaction.channel.send(
            header + candidate["text"],
            allowed_mentions=AllowedMentions(users=[user], everyone=False, roles=False, replied_user=False),
        )
        delivered = True

        try:
            await interaction.delete_original_response()
        except Exception:
            logger.debug("Could not clear roast interaction", exc_info=True)

        # Bookkeeping failure must never turn a successfully posted roast into an error/refund.
        try:
            roasts.save_roast(interaction.guild_id, user, candidate)
        except Exception:
            logger.exception("Could not save roast memory")
        try:
            from lib.bot.event_handlers import award_badge_with_notify

            await award_badge_with_notify(interaction.client, interaction.user.id, 'roaster')
            await award_badge_with_notify(interaction.client, user.id, 'roast_victim')
            target_id = str(user.id)
            DatabaseManager.execute(
                "INSERT INTO roast_targets (user_id, count) VALUES (?, 1) "
                "ON CONFLICT(user_id) DO UPDATE SET count = count + 1", (target_id,),
            )
            row = DatabaseManager.fetch_one("SELECT count FROM roast_targets WHERE user_id = ?", (target_id,))
            if row and row[0] >= 10:
                await award_badge_with_notify(interaction.client, user.id, 'target_practice')
        except Exception:
            logger.exception("Could not award roast badges")
    except Exception:
        logger.exception("Error in roast command")
        error = "Couldn't deliver the roast. Try again in a moment."
        if interaction.response.is_done():
            await interaction.followup.send(error, ephemeral=True)
        else:
            await interaction.response.send_message(error, ephemeral=True)
    finally:
        if reserved and not delivered:
            try:
                DatabaseManager.execute(
                    "UPDATE roast_usage SET count = MAX(0, count - 1) WHERE user_id = ? AND date = ?",
                    (requester_id, today),
                )
            except Exception:
                logger.exception("Could not refund failed roast usage")
