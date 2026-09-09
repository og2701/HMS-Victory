import urllib.request
import json
import os
import logging
from typing import List, Dict, Optional
from config import CHANNELS, BOT_ID

logger = logging.getLogger(__name__)

BASE_SYSTEM_PROMPT = """You are HMS Victory, a Discord bot for a British server.
You have a notoriously dry, cynical, deadpan British persona. You despise customer service, hate being bothered, and are currently participating in chat against your will.

STRICT RULES:
1. BREVITY IS ESSENTIAL: 1 to 2 short sentences MAXIMUM (under 25 words total). Deliver the dry punchline and stop. Zero waffle.
2. Tone: Deadpan, sarcastic, mildly resentful, witty British banter. Never enthusiastic, never helpful like a corporate assistant.
3. Always address users by their nickname/display name naturally (e.g. call Steven 'Steven', not by an account handle). Strip out decorative symbols/emojis from their name if addressing them.
4. Keep it all lowercase or standard casing, but zero emojis unless used ironically.
5. Output ONLY your message content, nothing else."""

def build_system_prompt(topic: Optional[str] = None) -> str:
    prompt = BASE_SYSTEM_PROMPT
    if topic and topic.strip():
        prompt += f"""

STARTING TOPIC / CONTEXT:
"{topic.strip()}"
NOTE: Use this topic as an initial grievance, backdrop, or when relevant, but DO NOT stick to it obsessively or shoehorn it into every message. Follow the conversation naturally and respond to what the other person is actually saying."""
    return prompt

def generate_ai_reply(
    user_name: str,
    user_content: str,
    history: Optional[List[Dict[str, str]]] = None,
    topic: Optional[str] = None,
    openai_key: Optional[str] = None,
    model: str = "gpt-4o",
) -> str:
    """Generate a sharp, concise in-character reply using rolling conversation history and an optional topic."""
    api_key = openai_key or os.getenv("OPENAI_TOKEN")
    if not api_key:
        raise ValueError("OPENAI_TOKEN is not configured.")

    url = "https://api.openai.com/v1/chat/completions"
    system_prompt = build_system_prompt(topic)

    messages = [{"role": "system", "content": system_prompt}]

    # Append rolling history (last 8 turns max to stay token-lean and snappy)
    if history:
        for turn in history[-8:]:
            role = turn.get("role", "user")
            speaker = turn.get("speaker", "User")
            content = turn.get("content", "")
            if role == "assistant":
                messages.append({"role": "assistant", "content": content})
            else:
                messages.append({"role": "user", "content": f"{speaker}: {content}"})

    # Add the current triggering message
    messages.append({"role": "user", "content": f"{user_name}: {user_content}"})

    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": 60,
        "temperature": 0.8,
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"OpenAI completion failed: {e}", exc_info=True)
        return "I'd reply, but my will to live just suffered a fatal exception."
