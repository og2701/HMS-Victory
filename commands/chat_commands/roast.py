import openai
import asyncio
from datetime import datetime, timedelta
from os import getenv
from discord import Embed, Forbidden, HTTPException
from lib.utils import fetch_messages_with_context, estimate_tokens
from lib.settings import *

openai.api_key = getenv("OPENAI_TOKEN")

time_threshold = datetime.utcnow() - timedelta(days=7)

async def roast(interaction, channel=None, user=None):
    await interaction.response.defer(thinking=True) 
    
    if channel is None:
        channel = interaction.channel 
    if user is None:
        user = interaction.user

    user_messages = []
    await fetch_messages_with_context(channel, user, user_messages, total_limit=250, context_depth=2)

    input_text = "\n".join(user_messages)
    if len(input_text) == 0:
        await interaction.channel.send(f"{user.display_name} hasn't said anything interesting lately!")
        await interaction.delete_original_response()
        return
    
    estimated_tokens = estimate_tokens(input_text)
    max_allowed_tokens = 120000

    if estimated_tokens > max_allowed_tokens:
        allowed_length = max_allowed_tokens * 4
        input_text = input_text[:allowed_length]

    is_special_user = user.id == USERS.OGGERS
    
    system_prompt = (
        f"You are an assistant tasked with {'writing a highly flattering and positive summary' if is_special_user else 'writing a very rude and insulting summary'} "
        f"of the chat messages of a user. {'Your goal is to make the user feel appreciated and admired.' if is_special_user else 'Your goal is to embarrass them.'} "
        f"The user's name is {user.display_name}. Only summarise the messages from {user.display_name}, while considering the context. "
        f"The summary should be a paragraph. The messages are from the past as of {datetime.utcnow().strftime('%Y-%m-%d')}. "
        f"Return only the paragraph and nothing else."
    )

    try:
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
        await send_message_with_retry(interaction.channel, summary)
        await interaction.delete_original_response() 

    except Exception as e:
        print(e)
        await send_message_with_retry(interaction.channel, "An error occurred.")
        await interaction.delete_original_response()

async def send_message_with_retry(channel, content):
    retry_delay = 5
    for _ in range(5):
        try:
            await channel.send(content)
            break
        except HTTPException as e:
            if e.status == 429:
                print("Rate limited. Retrying...")
                await asyncio.sleep(retry_delay)
                retry_delay *= 2
            else:
                print(f"Error sending message: {e}")
                break
