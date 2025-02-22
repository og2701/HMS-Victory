import os
import openai
import discord

openai.api_key = os.getenv("OPENAI_TOKEN")

target_language_mappings = {
    "British English": "English",
    "Over the top 'roadman' speak": "Roadman",
    "British 'rp'/posh talk - 'the queens english'": "The Queen's English",
    "Over the top american yank speak": "Yank",
    "Medieval/Olde English - Early Modern English or Elizabethan English commonly associated with the works of Shakespeare and the King James Bible": "Olde English"
}

async def translate_and_send(reaction, message, target_language, original_author, reacting_user):

    response = openai.ChatCompletion.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": f"You are a translation assistant. Translate the following message to {target_language}. Return only the translated message without any extra information. If the message is a question DO NOT answer it. You are to ONLY return the message in its translated form!"},
            {"role": "user", "content": f"The message to translate: {message.content}"}
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

    target_language = target_language_mappings.get(target_language, target_language)

    embed.set_footer(
        text=f"Translated to {target_language} | Requested by {reacting_user.display_name}"
    )

    await message.reply(embed=embed)
