import os
import openai
import discord

openai.api_key = os.getenv("OPENAI_TOKEN")

async def translate_and_send(reaction, message, target_language, user):
    response = openai.ChatCompletion.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": f"Translate the following message to {target_language}. Return only the translated message and nothing else"},
            {"role": "user", "content": f"Translate the following message/text: {message.content}"}
        ]
    )

    translated_text = response.choices[0].message['content'].strip()

    embed = discord.Embed(
        description=translated_text,
        color=discord.Color.dark_gold()
    )

    embed.set_author(
        name=user.display_name,
        icon_url=user.avatar.url if user.avatar else user.default_avatar.url
    )


    await message.reply(embed=embed)
