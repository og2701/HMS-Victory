import os
import time
from openai import AsyncOpenAI
import discord
from config import CHANNELS
from collections import defaultdict
from lib.core.constants import FLAG_LANGUAGE_MAPPINGS
from database import DatabaseManager

client = AsyncOpenAI(api_key=os.getenv("OPENAI_TOKEN"))


def _already_translated(message_id, target):
    """Has this message already been translated to this target (flag)? Survives the reaction
    being removed and re-added, and applies no matter who reacts."""
    row = DatabaseManager.fetch_one(
        "SELECT 1 FROM translation_log WHERE message_id = ? AND target = ?",
        (str(message_id), target),
    )
    return row is not None


def _claim_translation(message_id, target):
    """Atomically claim (message, target) so two near-simultaneous reactions can't both
    translate. Returns True if we got the claim, False if someone already holds it."""
    return DatabaseManager.execute(
        "INSERT OR IGNORE INTO translation_log (message_id, target) VALUES (?, ?)",
        (str(message_id), target),
    ) == 1


def _release_translation(message_id, target):
    """Drop the claim so a failed translation can be retried."""
    DatabaseManager.execute(
        "DELETE FROM translation_log WHERE message_id = ? AND target = ?",
        (str(message_id), target),
    )

target_language_mappings = {
    "British English": "English",
    "Australian bogan slang. Completely rewrite the message so every sentence is dripping in Australian insults and slang - do not just append words to the end. Weave 'cunt' naturally throughout as a filler and term of endearment (e.g. 'listen here ya cunt', 'deadset sick cunt', 'what ya on about cunt'). Replace words with Aussie equivalents and throw in insults like 'dickhead', 'gronk', 'dog', 'dropkick', 'dero', 'flog', 'ya goose', 'muppet' where they fit naturally": "🇦🇺 Aussie",
    "Over the top 'roadman' speak": "Roadman",
    "British 'rp'/posh talk - 'the queens english'": "The Queen's English",
    "Over the top american yank speak": "Yank",
    "Medieval/Olde English - Early Modern English or Elizabethan English commonly associated with the works of Shakespeare and the King James Bible": "Olde English",
    # Keyed off the constants source so the footer label can't drift from the prompt.
    FLAG_LANGUAGE_MAPPINGS["🦴"]: "🦴 Caveman",
}

user_translation_timestamps = defaultdict(list)
user_cooldowns = {}

TRANSLATION_LIMIT = 3
TRANSLATION_WINDOW = 300  # seconds
COOLDOWN_DURATION = 600  # 10 minutes


async def translate_and_send(
    reaction, message, target_language, original_author, reacting_user
):
    if original_author.bot:
        return

    # Never translate the same message to the same thing twice - even if the reaction was
    # removed and re-added, or a different person reacts later. The flag emoji is the identity
    # of "the same thing". Checked up front so a duplicate doesn't cost the user a rate slot.
    dedup_target = str(reaction.emoji)
    if _already_translated(message.id, dedup_target):
        try:
            await reaction.remove(reacting_user)
        except discord.HTTPException:
            pass
        return

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
    if len(user_translation_timestamps[user_id]) >= TRANSLATION_LIMIT:
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

    # Atomically claim this (message, target) - this is what actually blocks a duplicate when
    # two reactions land at almost the same time. Released below if the translation fails so it
    # stays retryable.
    if not _claim_translation(message.id, dedup_target):
        try:
            await reaction.remove(reacting_user)
        except discord.HTTPException:
            pass
        return

    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": f"You are a translation assistant. Translate the following message to {target_language}. Convert any temperature given in Fahrenheit to Celsius in the output (e.g. '115°F' becomes '46°C', '70 F' becomes '21°C'), keeping it natural in the sentence. Return only the translated message without any extra information. If the message is a question DO NOT answer it. You are to ONLY return the message in its translated form!",
                },
                {"role": "user", "content": f"The message to translate: {message.content}"},
            ],
        )

        translated_text = response.choices[0].message.content.strip()

        embed = discord.Embed(description=translated_text, color=discord.Color.dark_gold())

        embed.set_author(
            name=original_author.display_name,
            icon_url=(
                original_author.avatar.url
                if original_author.avatar
                else original_author.default_avatar.url
            ),
        )

        footer_target = target_language_mappings.get(target_language, target_language)

        embed.set_footer(
            text=f"Translated to {footer_target} | Requested by {reacting_user.display_name}"
        )

        await message.reply(embed=embed)

        # "Ooga Booga" badge for the author whose message got translated to Caveman.
        # Reactions carry no bot client here, so award via the sync path (no DM) - the
        # badge + its reward still land. Best-effort; never break the translation.
        if dedup_target == "\U0001f9b4":  # 🦴 caveman flag
            try:
                from database import award_badge
                if award_badge(original_author.id, "ooga_booga"):
                    from lib.economy.badge_rewards import pay_badge_reward
                    pay_badge_reward(original_author.id, "ooga_booga")
            except Exception:
                pass
    except Exception:
        _release_translation(message.id, dedup_target)
        raise
