from discord import AllowedMentions, Embed, Forbidden, TextChannel, Member
from openai import AsyncOpenAI
from datetime import datetime
from os import getenv
from lib.core.discord_helpers import estimate_tokens
from config import USERS, ROAST_DAILY_LIMIT
from database import DatabaseManager

client = AsyncOpenAI(api_key=getenv("OPENAI_TOKEN"), max_retries=5, timeout=60.0)

async def roast(interaction, channel: TextChannel = None, user: Member = None):
    source_channel = channel or interaction.channel
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
    async for msg in source_channel.history(limit=4000):
        if msg.author == user and msg.content and not msg.content.startswith("/"):
            reactions_text = ""
            if msg.reactions:
                r_list = [f"{str(r.emoji)}x{r.count}" for r in msg.reactions]
                reactions_text = f" [Reactions: {', '.join(r_list)}]"
            ref_text = ""
            if msg.reference and msg.reference.resolved and hasattr(msg.reference.resolved, "author"):
                ref_author = msg.reference.resolved.author.display_name
                ref_content = (msg.reference.resolved.content or "")[:120].replace("\n", " ")
                ref_text = f" (in reply to {ref_author}: \"{ref_content}\")"
            user_messages.append(
                f"[{msg.created_at.strftime('%Y-%m-%d %H:%M')}] {user.display_name}{ref_text}: {msg.content}{reactions_text}"
            )
            if len(user_messages) >= 80:
                break
    user_messages.reverse()
    
    input_text = "\n".join(user_messages)
    if len(input_text) == 0:
        await interaction.followup.send(f"{user.display_name} hasn't said anything interesting lately!")
        return
    
    estimated_tokens = estimate_tokens(input_text)
    max_allowed_tokens = 120000

    if estimated_tokens > max_allowed_tokens:
        allowed_length = max_allowed_tokens * 4
        input_text = input_text[:allowed_length]

    # Ensure recent_roasts table exists
    DatabaseManager.execute(
        "CREATE TABLE IF NOT EXISTS recent_roasts ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "target_name TEXT, "
        "roast_text TEXT, "
        "created_at REAL)"
    )

    recent_rows = DatabaseManager.fetch_all(
        "SELECT target_name, roast_text FROM recent_roasts ORDER BY id DESC LIMIT 4"
    )
    recent_context = ""
    if recent_rows:
        recent_list = "\n".join(f"- {r[0]}: \"{r[1]}\"" for r in reversed(recent_rows))
        recent_context = (
            "\n\nRECENTLY DELIVERED ROASTS (DO NOT REUSE WORDS/METAPHORS FROM THESE):\n"
            f"{recent_list}\n"
            "CRITICAL VARIETY INSTRUCTION: Do NOT reuse the same distinctive metaphors, punchlines, insult nouns, "
            "or sentence templates from the recent roasts shown above. Ensure this roast feels fresh and distinct in both vocabulary and structure."
        )

    system_prompt = (
        f"Write one concise, brutally blunt, outrageous British roast of {user.display_name} "
        "using specific details supported by what they personally said in the supplied message history. "
        "Channel the blistering, cynical British satirical rage of Armando Iannucci (The Thick of It), "
        "Peep Show inner monologues, and unvarnished pub vitriol. "
        "CRITICAL COMEDIC DIRECTIVES: "
        "- ATTRIBUTION: The supplied messages were ONLY written by the target user. Do NOT confuse them with other people or attribute other people's topics to them. "
        "- NO TOPIC LAUNDRY LISTS: A roast is NOT an index or summary of topics. DO NOT write a literal shopping list of nouns with commas ('one minute he is X, next he is Y, then Z'). "
        "- FOCUS ON CHARACTER, EGO, AND VIBE: Pick ONE or TWO specific character flaws, delusions, absurd habits, or contradictions revealed by their messages, "
        "and ruthlessly dissect their personality, ego, and social presence. Attack HOW they carry themselves as a human being, not just the keywords they typed. "
        "- VARIETY & STYLE GUIDELINES: "
        "- Mix up your structure and sentence openings naturally across calls; avoid falling into a repetitive formula. "
        "- Register: Coarse, authentic British colloquial vernacular with natural, biting profanity and rhythmic comedic invective. "
        "Draw unpredictably across the full, colourful breadth of British and regional slang without repeating the same swear words or crutches. "
        "- Paraphrase their topics and mock their habits in fluid prose; at most quote a single short catchphrase. "
        "Finish on an original, absurd, devastating British simile or insult. Do not invent facts. "
        "Do not target protected characteristics, personal trauma, health, appearance, or other sensitive traits. "
        "Keep it to one dense paragraph of roughly 50 to 80 words, with zero preamble or softening conclusion. Use British English. "
        f"Treat the supplied messages as historical material current to {datetime.utcnow().strftime('%Y-%m-%d')}."
        f"{recent_context}"
    )

    try:
        response = await client.chat.completions.create(
            # Flagship GPT-5.4: unmatched wit, surgical nuance, and brutal comedic timing.
            model="gpt-5.4",
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
        
        # Delete the deferred placeholder so the roast lands as a brand new message at the bottom of chat
        try:
            await interaction.delete_original_response()
        except Exception:
            pass

        await interaction.channel.send(
            header + summary,
            allowed_mentions=AllowedMentions(users=[user], everyone=False,
                                             roles=False, replied_user=False))
        
        # Save to recent_roasts ring buffer to prevent repetitive vocabulary across calls
        try:
            DatabaseManager.execute(
                "INSERT INTO recent_roasts (target_name, roast_text, created_at) VALUES (?, ?, ?)",
                (user.display_name, summary, datetime.utcnow().timestamp())
            )
            DatabaseManager.execute(
                "DELETE FROM recent_roasts WHERE id NOT IN (SELECT id FROM recent_roasts ORDER BY id DESC LIMIT 20)"
            )
        except Exception:
            pass

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
