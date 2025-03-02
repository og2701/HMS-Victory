from discord import Embed, Forbidden
import openai
from datetime import datetime, timedelta
from os import getenv
from lib.utils import fetch_messages_with_context, estimate_tokens, handle_roast_command

openai.api_key = getenv("OPENAI_TOKEN")

time_threshold = datetime.utcnow() - timedelta(days=7)

async def roast(interaction, channel=None, user=None):
    await interaction.response.defer(thinking=True)
    if channel is None:
        channel = interaction.channel
    if user is None:
        user = interaction.user
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
    try:
        system_prompt = (
            f"You are an assistant tasked with writing a very rude and insulting summary of the chat messages of a user with the intent of embarrassing them. "
            f"The user's name is {user.display_name}. Only summarise the messages from {user.display_name}, "
            f"while considering the context. The summary should be a paragraph. The messages are from the past as of {datetime.utcnow().strftime('%Y-%m-%d')}. "
            f"Return only the paragraph and nothing else."
        )
        response = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Summarise the following messages from {user.display_name}:\n{input_text}"},
            ],
            max_tokens=500,
            temperature=0.7,
        )
        summary = response["choices"][0]["message"]["content"].strip()
        await interaction.followup.send(summary)
    except Exception as e:
        print(e)
        await interaction.followup.send("An error occurred.")
