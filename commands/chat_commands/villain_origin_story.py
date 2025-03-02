from discord import Embed, app_commands, Forbidden
import openai
from datetime import datetime, timedelta
from os import getenv

openai.api_key = getenv("OPENAI_TOKEN")

SPECIFIED_USER_IDS = [
    404634271861571584,
    797207976548499518,
    772553171616006166,
    447010711936303115,
    198144909583056898,
]
time_threshold = datetime.utcnow() - timedelta(days=7)


async def fetch_messages_with_context(
    channel, user, user_messages, total_limit=100, context_depth=2
):
    try:
        user_message_count = 0
        message_history = []
        async for message in channel.history(
            limit=None, after=time_threshold, oldest_first=True
        ):
            if message.author.bot:
                continue

            message_history.append(message)
            if message.author == user:
                user_message_count += 1
                if user_message_count >= total_limit:
                    break

        i = 0
        while i < len(message_history):
            message = message_history[i]
            if message.author == user:
                context = []
                context_count = 0
                j = i - 1
                while context_count < context_depth and j >= 0:
                    if (
                        not message_history[j].author.bot
                        and message_history[j].author != user
                    ):
                        context.append(message_history[j])
                        context_count += 1
                    j -= 1
                context.reverse()

                user_message_block = []
                while i < len(message_history) and message_history[i].author == user:
                    user_message_block.append(
                        f"{message_history[i].created_at.strftime('%Y-%m-%d %H:%M:%S')} - {user.display_name}: {message_history[i].content}"
                    )
                    i += 1

                user_message_block_text = "\n".join(user_message_block)

                if context:
                    context_text = "\n".join(
                        [
                            f"{m.created_at.strftime('%Y-%m-%d %H:%M:%S')} - {m.author.display_name}: {m.content}"
                            for m in context
                        ]
                    )
                    user_messages.append(
                        f"Context:\n{context_text}\n{user_message_block_text}"
                    )
                else:
                    user_messages.append(user_message_block_text)
            else:
                i += 1
    except Forbidden:
        pass


def estimate_tokens(text):
    return len(text.split())


async def origin_story(interaction, channel=None, user=None):
    await interaction.response.defer(thinking=True)

    if channel is None:
        channel = interaction.channel
    if user is None:
        user = interaction.user

    user_messages = []
    await fetch_messages_with_context(
        channel, user, user_messages, total_limit=100, context_depth=2
    )

    input_text = "\n".join(user_messages)
    if len(input_text) == 0:
        await interaction.followup.send(
            f"{user.display_name} hasn't said anything interesting lately!"
        )
        return

    estimated_tokens = estimate_tokens(input_text)
    print(estimated_tokens)
    max_allowed_tokens = 120000
    if estimated_tokens > max_allowed_tokens:
        allowed_length = max_allowed_tokens * 4
        input_text = input_text[:allowed_length]

    try:
        system_prompt = (
            f"You are an assistant tasked with writing a lighthearted but dramatic argument between two Discord users. "
            f"The first user is {user.display_name}, and the second user is someone they've interacted with based on the provided chat history. "
            f"The argument should follow this structure: {user.display_name} speaks first, the second user responds, and they alternate. "
            f"{user.display_name} should have three messages in total, while the second user has two. "
            f"Quotes must reflect their personalities and speech styles, inferred from the chat history, but avoid sensitive topics like dating, politics, or anything inappropriate. "
            f"The insults should be witty, fun, and entirely playful—focused on harmless topics such as hobbies, quirks, or behavior in the server. "
            f"The argument should be entertaining and accessible for all ages. The messages are from the past as of {datetime.utcnow().strftime('%Y-%m-%d')}. "
            f"Return only the formatted dialogue and nothing else."
        )

        response = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": (
                        f"Based on the chat history, write a lighthearted argument between {user.display_name} "
                        f"and another user they’ve interacted with. Use the structure: "
                        f"{user.display_name} speaks first, followed by the second user, alternating for five messages total. "
                        f"Ensure the argument is humorous, includes witty insults, and avoids sensitive or inappropriate topics. "
                        f"Infer the reason for their argument from the following messages:\n{input_text}"
                    ),
                },
            ],
            max_tokens=500,
            temperature=0.8,
        )
        argument_description = response["choices"][0]["message"]["content"].strip()
        await interaction.followup.send(content=(f"{argument_description}"))

    except Exception as e:
        print(e)
        await interaction.followup.send(content=f"An error occurred.")
