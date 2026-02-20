import os
import time
import openai
import discord
from config import CHANNELS
from collections import defaultdict

openai.api_key = os.getenv("OPENAI_TOKEN")

target_language_mappings = {
    "British English": "English",
    "Over the top 'roadman' speak": "Roadman",
    "British 'rp'/posh talk - 'the queens english'": "The Queen's English",
    "Over the top american yank speak": "Yank",
    "Medieval/Olde English - Early Modern English or Elizabethan English commonly associated with the works of Shakespeare and the King James Bible": "Olde English",
}

user_translation_timestamps = defaultdict(list)
user_cooldowns = {}

TRANSLATION_LIMIT = 3
TRANSLATION_WINDOW = 120  # seconds
COOLDOWN_DURATION = 600  # 10 minutes


async def translate_and_send(
    reaction, message, target_language, original_author, reacting_user
):
    user_id = reacting_user.id
    current_time = time.time()

    # Check if user is on cooldown
    if user_id in user_cooldowns:
        if current_time < user_cooldowns[user_id]:
            try:
                await reaction.remove(reacting_user)
            except discord.Forbidden:
                pass
            return
        else:
            del user_cooldowns[user_id]

    # Clean up old timestamps
    user_translation_timestamps[user_id] = [
        ts
        for ts in user_translation_timestamps[user_id]
        if current_time - ts < TRANSLATION_WINDOW
    ]

    # If limit reached, punish
    if len(user_translation_timestamps[user_id]) > TRANSLATION_LIMIT:
        user_cooldowns[user_id] = current_time + COOLDOWN_DURATION
        police_channel = message.guild.get_channel(CHANNELS.POLICE_STATION)

        if police_channel:
            embed = discord.Embed(
                title="🚨 Translation Spam Detected",
                description=f"{reacting_user.mention} has been put on a {COOLDOWN_DURATION // 60}-minute cooldown for spamming translations.",
                color=discord.Color.red(),
            )
            embed.add_field(name="Channel", value=message.channel.mention)
            await police_channel.send(embed=embed)

        try:
            await reacting_user.send(
                f"You have been put on a {COOLDOWN_DURATION // 60}-minute cooldown for spamming the translation feature. Please slow down and only use it when necessary."
            )
            await reaction.remove(reacting_user)
        except Exception:
            pass

        return

    # Log timestamp
    user_translation_timestamps[user_id].append(current_time)


    response = openai.ChatCompletion.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": f"You are a translation assistant. Translate the following message to {target_language}. Return only the translated message without any extra information. If the message is a question DO NOT answer it. You are to ONLY return the message in its translated form!",
            },
            {"role": "user", "content": f"The message to translate: {message.content}"},
        ],
    )

    translated_text = response.choices[0].message["content"].strip()

    embed = discord.Embed(description=translated_text, color=discord.Color.dark_gold())

    embed.set_author(
        name=original_author.display_name,
        icon_url=(
            original_author.avatar.url
            if original_author.avatar
            else original_author.default_avatar.url
        ),
    )

    target_language = target_language_mappings.get(target_language, target_language)

    embed.set_footer(
        text=f"Translated to {target_language} | Requested by {reacting_user.display_name}"
    )

    await message.reply(embed=embed)
