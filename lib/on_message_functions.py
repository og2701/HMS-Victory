import discord
import openai
import os
from lib.settings import *

openai.api_key = os.getenv("OPENAI_TOKEN")

async def handle_ticket_closed_message(bot, message):
    embed = message.embeds[0]
    if embed.description and "Ticket Closed by" in embed.description and message.channel.category_id == CATEGORIES.TICKETS:
        collected_messages = []
        users_involved = set()
        async for msg in message.channel.history(limit=1000, oldest_first=True):
            collected_messages.append(f"{msg.author.display_name}: {msg.content}")
            users_involved.add(msg.author.display_name)
        chat_text = "\n".join(collected_messages)
        response = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "user", "content": f"Summarise the following conversation concisely, highlighting key points and context. Return only the summary and nothing else:\n{chat_text}\nSummary:"}
            ],
            temperature=0.7,
            max_tokens=200
        )
        summary = response.choices[0].message.content.strip()
        destination_channel = bot.get_channel(CHANNELS.POLICE_STATION)
        e = discord.Embed(title="Chat Summary", description=summary, color=0x00FF00)
        e.add_field(name="Channel Name", value=message.channel.name, inline=False)
        e.add_field(name="Users Involved", value=", ".join(users_involved), inline=False)
        e.timestamp = message.created_at
        await destination_channel.send(embed=e)
