import os
import openai
import discord

openai.api_key = os.getenv("OPENAI_TOKEN")

async def translate_and_send(reaction, message, target_language, original_author):
    response = openai.ChatCompletion.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": f"You are a translation assistant. Translate the following message to {target_language}. Return only the translated message without any extra information."},
            {"role": "user", "content": message.content}
        ]
    )

    translated_text = response.choices[0].message['content'].strip()

    embed = discord.Embed(
        description=translated_text,
        color=discord.Color.dark_gold()  
    )
    
    embed.set_author(
        name=original_author.display_name, 
        icon_url=original_author.avatar.url if original_author.avatar else original_author.default_avatar.url
    )

    await message.reply(embed=embed)
