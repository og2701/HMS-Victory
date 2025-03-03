import random
from discord import Embed, Forbidden
import openai
from datetime import datetime, timedelta
from os import getenv
from lib.utils import fetch_messages_with_context, estimate_tokens
from lib.settings import *

openai.api_key = getenv("OPENAI_TOKEN")

time_threshold = datetime.utcnow() - timedelta(days=7)

thinking_messages = [
    "Formulating the roast...",
    "Figuring out how to insult you...",
    "Sharpening my words...",
    "Roasting in progress...",
    "Downloading premium insults...",
]

async def roast(interaction, channel=None, user=None):
    if channel is None:
        channel = interaction.channel
    if user is None:
        user = interaction.user

    thinking_text = random.choice(thinking_messages)
    await interaction.response.send_message(thinking_text, ephemeral=False)

    user_messages = []
    await fetch_messages_with_context(channel, user, user_messages, total_limit=100, context_depth=2)
    
    input_text = "\n".join(user_messages)
    if len(input_text) == 0:
        await interaction.followup.send(f"{user.display_name} hasn't said anything interesting lately!")
        return
    
    estimated_tokens = estimate_tokens(input_text)
    max_allowed_tokens = 120000

    if estimated_tokens > max_allowed_tokens:
        allowed_length = max_allowed_tokens * 4
        input_text = input_text[:allowed_length]

    is_special_user = user.id == USERS.OGGERS
    
    if is_special_user:
        system_prompt = (
            f"You are an AI designed to write **the most flattering and legendary** summary of a person's chat messages, using **British English**. "
            f"Your job is to make {user.display_name} sound like a genius, a hero, and the most respected person in existence. "
            f"Highlight their wit, wisdom, and incredible contributions to the conversation. "
            f"Make them sound so legendary that people will be in awe. Be over-the-top in praise, but still make it sound believable. "
            f"The messages are from the past as of {datetime.utcnow().strftime('%Y-%m-%d')}. "
            f"Use **British English spellings**. "
            f"Return **only** the paragraph and nothing else."
        )
    else:
        system_prompt = (
            f"You are a ruthless, unforgiving, and brutally honest insult comic who writes in **British English**. "
            f"Your job is to **obliterate** the target with **savage, no-holds-barred insults**, using sharp British wit. "
            f"Your goal is to **humiliate and roast them mercilessly** in the most **creative, exaggerated, and brutal way possible**. "
            f"Be clever, be ruthless, and make sure the insults hit hard. **No soft jokes, no kindness—just pure verbal destruction.** "
            f"The user's name is {user.display_name}. Only summarise and roast {user.display_name} based on their own messages, considering the context. "
            f"The summary should be one **devastating** paragraph, designed to make {user.display_name} feel **completely and utterly roasted.** "
            f"The messages are from the past as of {datetime.utcnow().strftime('%Y-%m-%d')}. "
            f"Use **British English spellings**. "
            f"Return **only** the paragraph and nothing else."
        )

    try:
        response = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Summarise and process the following messages from {user.display_name}:\n{input_text}"},
            ],
            max_tokens=500,
            temperature=1.0,
        )

        summary = response["choices"][0]["message"]["content"].strip()
        await interaction.followup.send(summary)
    except Exception as e:
        print(e)
        await interaction.followup.send("An error occurred.")
