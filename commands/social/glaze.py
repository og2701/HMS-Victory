import random
from discord import TextChannel, Member
import openai
from datetime import datetime
from os import getenv
from lib.core.discord_helpers import fetch_messages_with_context, estimate_tokens
from config import USERS

openai.api_key = getenv("OPENAI_TOKEN")

thinking_messages = [
    "Formulating a compliment...",
    "Finding the right words to praise you...",
    "Preparing the ultimate glaze...",
    "Brewing a cup of positivity...",
    "Polishing the brass..."
]

async def glaze(interaction, channel: TextChannel = None, user: Member = None):
    if channel is None:
        channel = interaction.channel
    if user is None:
        user = interaction.user

    if interaction.user.id != USERS.OGGERS:
        await interaction.response.send_message("Only OGGERS can use this command for now.", ephemeral=True)
        return

    thinking_text = random.choice(thinking_messages)
    await interaction.response.defer()
    await interaction.followup.send(thinking_text, ephemeral=False)

    user_messages = []
    # Less context than roast
    await fetch_messages_with_context(channel, user, user_messages, total_limit=30, context_depth=1)
    
    input_text = "\n".join(user_messages)
    if len(input_text) == 0:
        await interaction.followup.send(f"{user.display_name} hasn't said anything recently to glaze!")
        return
    
    estimated_tokens = estimate_tokens(input_text)
    max_allowed_tokens = 120000

    if estimated_tokens > max_allowed_tokens:
        allowed_length = max_allowed_tokens * 4
        input_text = input_text[:allowed_length]

    system_prompt = (
        f"You are a kind, overly posh, and overwhelmingly positive British flatterer. "
        f"Your task is to heavily praise and compliment the target, {user.display_name}, based on their recent chat messages. "
        f"Keep your response concise—no more than 2 or 3 sentences. Make them sound like an absolute legend. "
        f"The messages are from the past as of {datetime.utcnow().strftime('%Y-%m-%d')}. "
        f"Use **British English spellings and idioms**. "
        f"Return **only** the compliment paragraph."
    )

    try:
        response = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Here are the recent chat messages from {user.display_name}. Read them and provide a legendary, overly posh British compliment based on what they've said:\n\n{input_text}"},
            ],
            max_tokens=250,
            temperature=0.8,
        )

        summary = response["choices"][0]["message"]["content"].strip()
        await interaction.followup.send(summary)
    except Exception as e:
        print(e)
        await interaction.followup.send("An error occurred while trying to glaze.")
