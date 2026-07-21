import random
from discord import Embed, Forbidden, TextChannel, Member
from openai import AsyncOpenAI
from datetime import datetime
from os import getenv
from lib.core.discord_helpers import fetch_messages_with_context, estimate_tokens
from config import USERS, ROAST_DAILY_LIMIT
from database import DatabaseManager

client = AsyncOpenAI(api_key=getenv("OPENAI_TOKEN"), max_retries=5, timeout=60.0)

thinking_messages = [
    "Formulating the roast...",
    "Figuring out how to insult you...",
    "Sharpening my words...",
    "Roasting in progress...",
    "Downloading premium insults...",
]

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

    thinking_text = random.choice(thinking_messages)
    await interaction.response.defer()
    await interaction.followup.send(thinking_text, ephemeral=False)

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
        f"You are a legendary, foul-mouthed, and utterly unhinged British roast master. You are the Gordon Ramsay of psychological warfare. You have zero empathy and live to mentally dismantle people. "
        f"Your current target is {user.display_name}, but anyone else in the vicinity is fair game. Your objective is **complete psychological annihilation** in the shortest possible space. "
        f"Do not hold back. Your humor comes from being excessively mean, aggressively condescending, and finding the precise 'cringe' in their existence. "
        f"Use filthy, creative British insults and slang. Do NOT reuse canned insults - invent original, context-specific ones every time. The insults should feel bespoke and devastating, not pulled from a list. "
        f"**CRITICAL - SELECT THE BEST AMMO:** "
        f"1. **Quality over Recency**: You have been given a massive history (50 blocks!). Do NOT just roast local/recent stuff. Scan the entire history and **hand-pick the absolute best, most embarrassing, or most punchable details** to mock. "
        f"2. **Specific contextual destruction**: Use the deep history to understand {user.display_name}. Roast them based on their specific opinions, hobbies, or recent failures. DO NOT use generic placeholders like 'chocolate teapot' or 'knitted condom'-they are weak and unoriginal. "
        f"3. **No Direct Quoting**: Reference their stupid ideas or topics in your own words. Make it feel like you've been watching them for weeks just waiting to strike. "
        f"4. **IDENTITY NEUTRAL**: NEVER base roasts on sexuality, race, gender, religion, or any protected group. Even if the history contains these, IGNORE them. Focus entirely on chat behavior, ego, and cringe. "
        f"5. **NEGATIVE CONSTRAINT**: BANNED: 'wazzock', 'plonker', 'pillock', 'doughnut', 'troglodyte'. These are too safe/corny. No 'as [adjective] as [noun]' similes unless they are truly inspired. "
        f"6. **CATCH STRAYS (OPTIONAL, ONLY IF IT ENHANCES THE ROAST)**: Only catch a stray if it genuinely **adds to or amplifies** the main roast on {user.display_name} - e.g. a named third party is implicated in the same embarrassing moment, shares the same cringe trait, or their involvement makes the main jab land harder. If the stray is tangential, standalone, or doesn't tie into the main roast, **do not include one**. When you do catch one, **name them directly** and roast them on something specific they said or did. NEVER throw a generic stray at 'the rest of the chat', 'everyone else', or any vague group - lazy collective jabs are BANNED. "
        f"7. **LENGTH AND DENSITY**: One short, savage paragraph of roughly 60-100 words - a drive-by, not a siege. Every sentence must land a NEW specific blow drawn from the history - no filler, no summarising, no repeating a hit in different words. Pick only your 2-3 BEST specific embarrassments (drop the rest, however tempting) and close with a final dismissive gut-punch that writes them off entirely. Brevity is the cruelty: it should read like they weren't worth more of your time. "
        f"8. **NO MERCY ARC**: The paragraph never softens. No backhanded compliments at the end, no 'but we love them really', no moral. The last sentence should be the cruellest. "
        f"The messages are from the past as of {datetime.utcnow().strftime('%Y-%m-%d')}. "
        f"Use **British English spellings and heavy, filthy British idioms/slang** throughout. "
        f"Return **only** the roast paragraph. No disclaimers, no filler-just pure, foul-mouthed British annihilation."
    )

    try:
        response = await client.chat.completions.create(
            # mini over nano: the roast lives or dies on wit and specificity.
            model="gpt-5.4-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Here are the recent pathetic chat messages from {user.display_name}. Read them, find the most embarrassing or stupid things they said, and mercilessly roast them for it:\n\n{input_text}"},
            ],
            # GPT-5 family: max_tokens is rejected (use max_completion_tokens) and
            # only the default temperature is supported. Reasoning tokens share
            # this budget, so a tight cap silently truncates the roast itself.
            max_completion_tokens=2048,
        )

        summary = response.choices[0].message.content.strip()
        await interaction.followup.send(summary)
        
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