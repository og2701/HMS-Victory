import discord
from openai import AsyncOpenAI
import os
from config import *

client = AsyncOpenAI(api_key=os.getenv("OPENAI_TOKEN"))


async def handle_ticket_closed_message(bot, message):
    embed = message.embeds[0]
    if (
        embed.description
        and "Ticket Closed by" in embed.description
        and message.channel.category_id == CATEGORIES.TICKETS
    ):
        staff_role_ids = {
            ROLES.DEPUTY_PM,
            ROLES.MINISTER,
            ROLES.CABINET,
            ROLES.BORDER_FORCE,
            ROLES.PCSO,
        }
        
        collected_messages = []
        users_involved = set()
        
        # Try to identify the ticket creator from the channel topic
        ticket_creator_id = None
        if message.channel.topic and "User ID:" in message.channel.topic:
            try:
                # Assuming topic format like "Ticket created by User ID: 123456789"
                ticket_creator_id = int(message.channel.topic.split("User ID:")[1].strip())
            except ValueError:
                pass
                
        async for msg in message.channel.history(limit=1000, oldest_first=True):
            if msg.author.bot:
                tag = "[Bot]"
            elif msg.author.id == ticket_creator_id or (ticket_creator_id is None and msg.author.name.lower() in message.channel.name.lower()):
                # Fallback to checking if their name is in the channel name (e.g., ticket-oggers)
                tag = "[Ticket Creator]"
            elif hasattr(msg.author, "roles") and any(role.id in staff_role_ids for role in msg.author.roles):
                tag = "[Staff]"
            else:
                tag = "[User]"

            collected_messages.append(f"{tag} {msg.author.display_name}: {msg.content}")
            if not msg.author.bot:
                users_involved.add(msg.author.display_name)
                
        chat_text = "\n".join(collected_messages)

        system_prompt = (
            "You are an expert community manager summarizing Discord support tickets. "
            "You have been provided a raw chat transcript between the [Ticket Creator] and server [Staff]. "
            "Your job is to read the transcript and provide a highly concise summary (maximum 4 sentences) outlining exactly what happened. "
            "You MUST clearly state: 1) What the [Ticket Creator]'s core issue or question was. 2) What the [Staff] did to help or respond. 3) The final outcome/resolution of the ticket. "
            "Do not list the transcript verbatim. Do not include greetings. Speak directly about the issues."
        )

        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": f"Transcript:\n{chat_text}\n\nSummary:"
                }
            ],
            temperature=0.7,
            max_tokens=150,
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
