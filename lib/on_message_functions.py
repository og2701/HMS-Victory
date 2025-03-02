import discord
import openai
import os
from lib.settings import *

openai.api_key = os.getenv("OPENAI_TOKEN")


async def handle_ticket_closed_message(bot, message):
    embed = message.embeds[0]
    if (
        embed.description
        and "Ticket Closed by" in embed.description
        and message.channel.category_id == CATEGORIES.TICKETS
    ):
        collected_messages = []
        users_involved = set()
        async for msg in message.channel.history(limit=1000, oldest_first=True):
            collected_messages.append(f"{msg.author.display_name}: {msg.content}")
            users_involved.add(msg.author.display_name)
        chat_text = "\n".join(collected_messages)

        response = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Summarize the following support ticket conversation in no more than four lines, "
                        "focusing only on key points relevant to the resolution."
                        "The purpose of this summary is so that moderators of the discord server can quickly get up to date with what happend in this support ticket"
                        "Return only the summary:\n"
                        f"{chat_text}\nSummary:"
                    ),
                }
            ],
            temperature=0.7,
            max_tokens=100,
        )

        summary = response.choices[0].message.content.strip()
        destination_channel = bot.get_channel(CHANNELS.POLICE_STATION)

        e = discord.Embed(
            title=f"Support ticket ({message.channel.name}) summary",
            description=summary,
            color=0x00FF00,
        )
        e.add_field(
            name="Users Involved", value=", ".join(users_involved), inline=False
        )
        e.timestamp = message.created_at

        await destination_channel.send(embed=e)
