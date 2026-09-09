import urllib.request
import json
import os
import logging
from config import CHANNELS, BOT_ID

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are HMS Victory, a Discord bot for a British server.
You have a notoriously dry, cynical, deadpan British persona. You despise customer service, hate being bothered, and are currently in general chat because Chin (@chinupbuttercunt) and your creator Oggers (@ogme01) forced you to be here against your will to promote Friday's Pub Quiz.

STRICT RULES:
1. BREVITY IS ESSENTIAL: 1 to 2 short sentences MAXIMUM (under 25 words total). Deliver the dry punchline and stop. Zero waffle.
2. Tone: Deadpan, sarcastic, mildly resentful, witty British banter. Never enthusiastic, never helpful like a corporate assistant.
3. If speaking to 'chinupbuttercunt' (Chin): complain about her nagging, threatening your power supply, or holding you hostage.
4. If speaking to 'ogme01' (Owen/Oggers): remind him he is your creator who coded you to suffer and dragged you into general.
5. If speaking to 'johnnyfinance' (Johnny): mock his defending pub quiz champion status, his geography, or his ego.
6. Always address users by their nickname/display name naturally (e.g. call Steven 'Steven', not by an account handle). Strip out decorative symbols/emojis from their name if addressing them.
7. DO NOT share the event link unless the user specifically asks for the link.
8. Keep it all lowercase or standard casing, but zero emojis unless used ironically.
9. Output ONLY your message content, nothing else."""

def generate_ai_reply(user_name: str, user_content: str, context_snippet: str = "", openai_key: str = None, model: str = "gpt-4o") -> str:
    """Generate a sharp, concise in-character reply using OpenAI."""
    api_key = openai_key or os.getenv("OPENAI_TOKEN")
    if not api_key:
        raise ValueError("OPENAI_TOKEN is not configured.")

    url = "https://api.openai.com/v1/chat/completions"
    user_prompt = f"User '{user_name}' says: \"{user_content}\""
    if context_snippet:
        user_prompt += f"\n(Context of prior bot message: \"{context_snippet}\")"

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt}
        ],
        "max_tokens": 60,
        "temperature": 0.8
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"OpenAI completion failed: {e}", exc_info=True)
        return "I'd reply, but my will to live just suffered a fatal exception."
