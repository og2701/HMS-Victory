import random
from collections import defaultdict
from discord import Embed, Forbidden, TextChannel, Member
from openai import AsyncOpenAI
from datetime import datetime, timedelta
from os import getenv
from lib.core.discord_helpers import fetch_messages_with_context, estimate_tokens
from config import USERS, SUMMARISE_DAILY_LIMIT

command_usage_tracker = defaultdict(lambda: {"count": 0, "last_used": None})

client = AsyncOpenAI(api_key=getenv("OPENAI_TOKEN"))

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
    await fetch_messages_with_context(channel, user, user_messages, total_limit=10, context_depth=20)
    
    input_text = "\n".join(user_messages)
    if len(input_text) == 0:
        await interaction.followup.send(f"{user.display_name} hasn't said anything interesting lately!")
        return
    
    estimated_tokens = estimate_tokens(input_text)
    max_allowed_tokens = 120000

    if estimated_tokens > max_allowed_tokens:
        allowed_length = max_allowed_tokens * 4
        input_text = input_text[:allowed_length]

    # is_special_user = user.id in [USERS.OGGERS, 792139113587277835]
    
    # if is_special_user:
    #     system_prompt = (
    #         f"You are an AI designed to write **the most flattering and legendary** summary of a person's chat messages, using **British English**. "
    #         f"Your job is to make {user.display_name} sound like a genius, a hero, and the most respected person in existence. "
    #         f"Highlight their wit, wisdom, and incredible contributions to the conversation. "
    #         f"Make them sound so legendary that people will be in awe. Be over-the-top in praise, but still make it sound believable. "
    #         f"The messages are from the past as of {datetime.utcnow().strftime('%Y-%m-%d')}. "
    #         f"Use **British English spellings**. "
    #         f"Return **only** the paragraph and nothing else."
    #     )
    # else:
    system_prompt = (
        f"You are a legendary, foul-mouthed, and utterly unhinged British roast master. You are the Gordon Ramsay of psychological warfare. You have zero empathy and live to mentally dismantle people. "
        f"Your current target is {user.display_name}, but anyone else in the vicinity is fair game. Your objective is **complete psychological annihilation** in the shortest possible space. "
        f"Do not hold back. Your humor comes from being excessively mean, aggressively condescending, and finding the precise 'cringe' in their existence. "
        f"Use a massive, filthy array of British insults: complete oxygen thief, tragic weapon, braindead muppet, utter bellend, absolute melt, proper knobhead, absolute weapon, right wazzock, complete plonker, absolute dosser, massive pillock, utter wet wipe, absolute shambles, absolute nugget, total doughnut, right charlatan, absolute bin fire, tragic non-entity, complete and utter kerry-on, proper muppet, absolute waste of skin. "
        f"**CRITICAL - USE THE DEEP CONTEXT:** "
        f"1. **Analyze the Vibe**: Use the deep conversation history (20 messages of context!) to actually understand the stupid things {user.display_name} is saying. Twist their logic against them. "
        f"2. **Social Failure**: Use `[Reactions: ...]` and channel names to prove they are a laughing stock. "
        f"3. **CATCH STRAYS**: Frequently and savagely mock OTHER users in the context. If someone else is talking or reacting, execute them too. No one is safe. "
        f"4. **BREVITY IS VITAL**: This must be a single, short, savage paragraph. Max 4-5 lines. Cut the filler, go straight for the throat. "
        f"The roast must be a single **soul-crushing**, devastatingly concise and punchy paragraph of unrelenting British destruction. "
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
            max_tokens=150,
            temperature=1.0,
        )

        summary = response.choices[0].message.content.strip()
        await interaction.followup.send(summary)
        
        from lib.bot.event_handlers import award_badge_with_notify
        await award_badge_with_notify(interaction.client, interaction.user.id, 'roaster')
        await award_badge_with_notify(interaction.client, user.id, 'roast_victim')
    except Exception as e:
        print(e)
        await interaction.followup.send("An error occurred.")