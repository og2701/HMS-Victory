from discord import AllowedMentions, Embed, Forbidden, TextChannel, Member
from openai import AsyncOpenAI
from datetime import datetime
from os import getenv
from lib.core.discord_helpers import fetch_messages_with_context, estimate_tokens
from config import USERS, ROAST_DAILY_LIMIT
from database import DatabaseManager

client = AsyncOpenAI(api_key=getenv("OPENAI_TOKEN"), max_retries=5, timeout=60.0)

async def roast(interaction, channel: TextChannel = None, user: Member = None):
    if channel is None:
        channel = interaction.channel
    if user is None:
        user = interaction.user

    if interaction.user.id != USERS.OGGERS:
        # Persisted, UTC-keyed daily limit - can't be reset by a bot restart.
        today = datetime.utcnow().strftime("%Y-%m-%d")
        uid = str(interaction.user.id)
        row = DatabaseManager.fetch_one(
            "SELECT count FROM roast_usage WHERE user_id = ? AND date = ?", (uid, today)
        )
        if (row[0] if row else 0) >= ROAST_DAILY_LIMIT:
            await interaction.response.send_message(
                f"You've hit the daily limit of {ROAST_DAILY_LIMIT} usages for this command", ephemeral=True
            )
            return
        DatabaseManager.execute(
            "INSERT INTO roast_usage (user_id, date, count) VALUES (?, ?, 1) "
            "ON CONFLICT(user_id, date) DO UPDATE SET count = count + 1",
            (uid, today),
        )

    await interaction.response.defer()

    user_messages = []
    await fetch_messages_with_context(channel, user, user_messages, total_limit=150, context_depth=20, history_limit=5000)
    
    input_text = "\n".join(user_messages)
    if len(input_text) == 0:
        await interaction.followup.send(f"{user.display_name} hasn't said anything interesting lately!")
        return
    
    estimated_tokens = estimate_tokens(input_text)
    max_allowed_tokens = 120000

    if estimated_tokens > max_allowed_tokens:
        allowed_length = max_allowed_tokens * 4
        input_text = input_text[:allowed_length]

    system_prompt = (
        f"Write one concise, outrageous British roast of {user.display_name} using only "
        "specific, non-sensitive details supported by the supplied message history. "
        "Select a small number of the strongest details from the full history, paraphrase "
        "rather than quote, and make each sentence advance a different hit. Keep the humour "
        "fresh, sharply observant, profane when it genuinely improves the line, and free of "
        "canned insults or reusable formats. Do not invent facts. Do not target protected "
        "characteristics, personal trauma, health, appearance, or other sensitive traits. "
        "A named third party may be included only when their documented involvement is essential "
        "to the same incident and materially strengthens the main roast; never add a standalone "
        "or collective stray. Keep it to one dense paragraph of roughly 45 to 75 words, with no "
        "preamble, disclaimer, explanation, moral, or softening conclusion. Use British English. "
        f"Treat the supplied messages as historical material current to {datetime.utcnow().strftime('%Y-%m-%d')}."
    )

    try:
        response = await client.chat.completions.create(
            # mini over nano: the roast lives or dies on wit and specificity.
            model="gpt-5.4-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Message history for the named target:\n\n{input_text}"},
            ],
            # GPT-5 family: max_tokens is rejected (use max_completion_tokens) and
            # only the default temperature is supported. Reasoning tokens share
            # this budget, so a tight cap silently truncates the roast itself.
            max_completion_tokens=2048,
        )

        summary = response.choices[0].message.content.strip()
        header = (f"🔥 {user.mention} 🔥\n"
                  f"-# roasted at {interaction.user.display_name}'s request\n\n")
        await interaction.followup.send(
            header + summary,
            allowed_mentions=AllowedMentions(users=[user], everyone=False,
                                             roles=False, replied_user=False))
        
        from lib.bot.event_handlers import award_badge_with_notify
        await award_badge_with_notify(interaction.client, interaction.user.id, 'roaster')
        await award_badge_with_notify(interaction.client, user.id, 'roast_victim')
        
        # Track roast victim for "Target Practice" badge
        uid = str(user.id)
        DatabaseManager.execute(
            "INSERT INTO roast_targets (user_id, count) VALUES (?, 1) ON CONFLICT(user_id) DO UPDATE SET count = count + 1",
            (uid,)
        )
        row = DatabaseManager.fetch_one("SELECT count FROM roast_targets WHERE user_id = ?", (uid,))
        if row and row[0] >= 10:
            await award_badge_with_notify(interaction.client, user.id, 'target_practice')

    except Exception as e:
        import traceback
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error in roast command: {e}\n{traceback.format_exc()}")
        await interaction.followup.send("An error occurred.")
