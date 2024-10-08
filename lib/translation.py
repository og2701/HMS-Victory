import os
import openai
import discord

openai.api_key = os.getenv("OPENAI_TOKEN")

async def translate_and_send(reaction, message, target_language):
    try:
        response = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": f"Translate the following message to {target_language}. Return only the translated message and nothing else"},
                {"role": "user", "content": message.content}
            ]
        )

        translated_text = response.choices[0].message['content'].strip()

        embed = discord.Embed(
            title=f"Translation to {target_language}",
            description=translated_text,
            color=discord.Color.blue()
        )
        embed.set_footer(text=f"Requested by {reaction.user.display_name}")

        await reaction.message.channel.send(embed=embed)

    except Exception as e:
        print(f"Error during translation: {e}")
