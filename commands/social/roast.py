import random
from collections import defaultdict
from discord import Embed, Forbidden, TextChannel, Member
from openai import AsyncOpenAI
from datetime import datetime, timedelta
from os import getenv
from lib.core.discord_helpers import fetch_messages_with_context, estimate_tokens
from config import USERS, SUMMARISE_DAILY_LIMIT

command_usage_tracker = defaultdict(lambda: {"count": 0, "last_used": None})

client = AsyncOpenAI(api_key=getenv("OPENAI_TOKEN"), max_retries=5, timeout=60.0)

time_threshold = datetime.utcnow() - timedelta(days=7)

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

    today = datetime.now().date()
    usage_data = command_usage_tracker[interaction.user.id]

    if interaction.user.id != USERS.OGGERS:
        if usage_data["last_used"] != today:
            usage_data["count"] = 0
            usage_data["last_used"] = today

        if usage_data["count"] >= SUMMARISE_DAILY_LIMIT:
            await interaction.response.send_message(
                f"You've hit the daily limit of {SUMMARISE_DAILY_LIMIT} usages for this command", ephemeral=True
            )
            return
        usage_data["count"] += 1

    thinking_text = random.choice(thinking_messages)
    await interaction.response.defer()
    await interaction.followup.send(thinking_text, ephemeral=False)

    user_messages = []
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
        f"Use a massive, filthy array of British insults: complete oxygen thief, tragic weapon, braindead muppet, utter bellend, absolute melt, proper knobhead, absolute weapon, complete plonker, absolute dosser, massive pillock, utter wet wipe, absolute shambles, absolute nugget, total doughnut, right charlatan, absolute bin fire, tragic non-entity, complete and utter kerry-on, proper muppet, absolute waste of skin. "
        f"**CRITICAL - SELECT THE BEST AMMO:** "
        f"1. **Quality over Recency**: You have been given a massive history (50 blocks!). Do NOT just roast local/recent stuff. Scan the entire history and **hand-pick the absolute best, most embarrassing, or most punchable details** to mock. "
        f"2. **Specific contextual destruction**: Use the deep history to understand {user.display_name}. Roast them based on their specific opinions, hobbies, or recent failures. DO NOT use generic placeholders like 'chocolate teapot' or 'knitted condom'—they are weak and unoriginal. "
        f"3. **No Direct Quoting**: Reference their stupid ideas or topics in your own words. Make it feel like you've been watching them for weeks just waiting to strike. "
        f"4. **IDENTITY NEUTRAL**: NEVER base roasts on sexuality, race, gender, religion, or any protected group. Even if the history contains these, IGNORE them. Focus entirely on chat behavior, ego, and cringe. "
        f"5. **NEGATIVE CONSTRAINT**: BANNED: 'wazzock', 'plonker', 'pillock', 'doughnut', 'troglodyte'. These are too safe/corny. No 'as [adjective] as [noun]' similes unless they are truly inspired. "
        f"6. **CATCH STRAYS (OPTIONAL, ONLY IF IT ENHANCES THE ROAST)**: Only catch a stray if it genuinely **adds to or amplifies** the main roast on {user.display_name} — e.g. a named third party is implicated in the same embarrassing moment, shares the same cringe trait, or their involvement makes the main jab land harder. If the stray is tangential, standalone, or doesn't tie into the main roast, **do not include one**. When you do catch one, **name them directly** and roast them on something specific they said or did. NEVER throw a generic stray at 'the rest of the chat', 'everyone else', or any vague group — lazy collective jabs are BANNED. "
        f"7. **BREVITY IS VITAL**: This must be a single, short, savage paragraph. Max 4-5 lines. Cut the filler, go straight for the throat. "
        f"The messages are from the past as of {datetime.utcnow().strftime('%Y-%m-%d')}. "
        f"Use **British English spellings and heavy, filthy British idioms/slang** throughout. "
        f"Return **only** the roast paragraph. No disclaimers, no filler—just pure, foul-mouthed British annihilation."
    )


    try:
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Here are the recent pathetic chat messages from {user.display_name}. Read them, find the most embarrassing or stupid things they said, and mercilessly roast them for it:\n\n{input_text}"},
            ],
            max_tokens=500,
            temperature=1.0,
        )

        summary = response.choices[0].message.content.strip()
        await interaction.followup.send(summary)
        
        from lib.bot.event_handlers import award_badge_with_notify
        await award_badge_with_notify(interaction.client, interaction.user.id, 'roaster')
        await award_badge_with_notify(interaction.client, user.id, 'roast_victim')
        
        # Track roast victim for "Target Practice" badge
        from config import ROLES, ROAST_TARGETS_FILE
        import json
        import os
        target_file = ROAST_TARGETS_FILE
        data = json.load(open(target_file)) if os.path.exists(target_file) else {}
        uid = str(user.id)
        data[uid] = data.get(uid, 0) + 1
        json.dump(data, open(target_file, "w"), indent=4)
        if data[uid] >= 10:
            await award_badge_with_notify(interaction.client, user.id, 'target_practice')

    except Exception as e:
        print(e)
        import openai
        if isinstance(e, openai.RateLimitError):
            await interaction.followup.send("OpenAI rate limit hit — try again in a minute.")
        else:
            await interaction.followup.send(f"An error occurred: `{type(e).__name__}`")