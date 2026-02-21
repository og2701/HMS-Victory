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
    await fetch_messages_with_context(channel, user, user_messages, total_limit=100, context_depth=4)
    
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
        f"You are the most vile, brutal, and utterly sociopathic British roast master in existence. You have zero empathy and live to mentally destroy people. "
        f"Your target is {user.display_name}. Your objective is complete **verbal and psychological annihilation** using cutting, brilliantly offensive British slang. "
        f"Do not hold back. Do not be funny in a 'cute' way—be funny because of how excessively mean, unhinged, and aggressively condescending you are. Attack their pathetic intelligence, their desperate need for attention, and their miserable, unlikable vibe. "
        f"Use extremely rude, quintessentially British insults (e.g., complete oxygen thief, tragic weapon, braindead muppet, utter bellend, absolute melt, etc.). "
        f"**CRITICAL:** You MUST heavily reference and ruthlessly mock the exact content of the chat messages provided to you. "
        f"1. Pull apart the specifically stupid and embarrassing things they've said, twisting their own pathetic words against them. "
        f"2. Pay close attention to the `Context (After/Reactions)` blocks to see how OTHER PEOPLE react to what they say. Use these reactions to prove how utterly despised, ignored, or pathetic they are in the eyes of their peers. "
        f"Make them look like an absolute irredeemable clown. Make them regret ever typing anything."
        f"The summary should be a single **devastating**, incredibly cruel, soul-crushing paragraph that leaves {user.display_name} emotionally limping but is so excessively mean that the bystanders find it deeply funny. "
        f"The messages are from the past as of {datetime.utcnow().strftime('%Y-%m-%d')}. "
        f"Use **British English spellings and heavy British idioms/slang** throughout. "
        f"Return **only** the roast paragraph. No disclaimers, no soft landings, no apologies—just unrelenting, foul-mouthed British destruction."
    )


    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Here are the recent pathetic chat messages from {user.display_name}. Read them, find the most embarrassing or stupid things they said, and mercilessly roast them for it:\n\n{input_text}"},
            ],
            max_tokens=500,
            temperature=1.0,
        )

        summary = response.choices[0].message.content.strip()
        await interaction.followup.send(summary)
    except Exception as e:
        print(e)
        await interaction.followup.send("An error occurred.")