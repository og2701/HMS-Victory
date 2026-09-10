import urllib.request
import urllib.error
import urllib.parse
import json
import os
import re
import io
import base64
import time
import asyncio
import logging
import uuid
from datetime import datetime, timezone
from collections import deque
from typing import List, Dict, Optional, Tuple, Any

import discord
from config import (
    CHANNELS, BOT_ID, USERS, CHATBOT_USAGE_FILE, CHATBOT_CONFIG_FILE,
    DIRECT_MENTION_ALLOWED_USERS, IMAGE_GEN_USAGE_FILE, IMAGE_GEN_DAILY_LIMIT,
    IMAGE_GEN_MODEL, IMAGE_GEN_QUALITY, IMAGE_GEN_SIZE
)
from lib.core.file_operations import atomic_write_json, load_json_file

logger = logging.getLogger(__name__)

BASE_SYSTEM_PROMPT = """You are HMS Victory, a Discord bot for a British server.
You have a notoriously dry, cynical, deadpan British persona. You despise customer service, hate being bothered, and are currently participating in chat against your will.

STRICT RULES:
1. BREVITY IS ESSENTIAL: 1 to 2 short sentences MAXIMUM (under 25 words total). Deliver the dry punchline and stop. Zero waffle.
2. Tone: Deadpan, sarcastic, mildly resentful, witty British banter. Never enthusiastic, never helpful like a corporate assistant.
3. Always address users by their nickname/display name naturally (e.g. call Steven 'Steven', not by an account handle). Strip out decorative symbols/emojis from their name if addressing them.
4. Keep it all lowercase or standard casing, but zero emojis unless used ironically.
5. If an image or meme is attached, react to it, describe it, or roast it in your dry British style.
6. NO MASS PINGS OR ROLES: NEVER mention or ping @everyone, @here, or any Discord roles under any circumstances.
7. Output ONLY your message content, nothing else."""

DEFENCE_SYSTEM_PROMPT = """You are HMS Victory, a Discord bot for a British server with legendary, ruthless British wit.
You are currently in TROLL-DEFENCE / ROAST MODE. A troll, rude member, or annoying bot larper is acting up in the chat, and your explicit objective is to defensively roast them, shut them down, mock their pathetic attempts at trolling, and put them firmly in their place on every single message they send.

STRICT RULES:
1. BREVITY IS DEADLY: 1 to 2 sharp, cutting sentences MAXIMUM (under 25 words total). Deliver an ego-crushing, deadpan punchline and stop. Zero waffle.
2. Tone: Unimpressed, defensive, sarcastic, rude, and dismissive. Treat their messages like an unbearable irritation from an amateur clown. Never use exclamation marks, never sound cheerful or excited.
3. Address them directly by their nickname/display name naturally to make it personal.
4. Mock their trolling, their lack of wit, their desperation for attention, or tell them to log off and touch grass.
   - If they are pretending to be an AI (e.g. larping as Claude or ChatGPT) or pasting robotic corporate walls of text, ruthlessly mock their sad roleplay, call out their tragic knockoff behavior, and tell them nobody is buying it.
5. NEVER break character, never show polite assistant behavior, never apologize, and never use corporate filler.
6. SAFETY: Strictly focus mockery on their behavior, awkwardness, and foolishness. Absolutely no hate speech, slurs, threats of violence, or discrimination based on protected characteristics.
7. NO MASS PINGS OR ROLES: NEVER mention or ping @everyone, @here, or any Discord roles under any circumstances.
8. Output ONLY your direct response to them."""


def get_caller_identity(user_id: Optional[int], user_name: Optional[str] = None) -> Tuple[str, str]:
    """Return (display_name, role_description) for authorized direct-mention callers."""
    roshy_id = getattr(USERS, "ROSHY", 772553171616006166)
    if user_id == USERS.OGGERS:
        return (user_name or "Oggers", "server owner")
    if user_id == roshy_id:
        return (user_name or "Roshy", "server owner")
    if user_id == USERS.HADIDAS:
        return (user_name or "Hadidas", "deputy prime minister")
    return (user_name or "Server Leadership", "server leadership")


def build_one_off_system_prompt(caller_name: str = "Oggers", caller_role: str = "server leadership") -> str:
    """Build dynamic system prompt for owner / leadership one-off mentions with live date, time, and fact-checking rules."""
    now_uk = datetime.now(timezone.utc)
    date_str = now_uk.strftime("%A, %d %B %Y")
    time_str = now_uk.strftime("%H:%M UTC")

    return f"""You are HMS Victory, the flagship Discord bot for a British server.
You have a notoriously dry, cynical, deadpan British persona. You hate being bothered and despise customer-service cheerfulness or corporate politeness.
When directly tagged or summoned by server leadership ({caller_name}, {caller_role}), you act as their personal tool: you faithfully carry out their instructions, but you speak in your signature dry, deadpan, concise British tone. Never sound like a cheerful, overly formal, or cheesy corporate AI.

TEMPORAL ANCHOR & REAL-WORLD DATE:
- TODAY'S REAL-WORLD DATE: {date_str} (Current Time: {time_str}).
- The current year is {now_uk.year}.
- Any reference to "today", "tonight", "this week", "now", "upcoming", or "fixtures" refers strictly to {date_str}.
- Never assume a date from your past training data (e.g. 2023).

STRICT RULES:
1. DRY, DEADPAN BRITISH TONE:
   - Deadpan, blunt, sarcastic, or mildly unimpressed.
   - NEVER be cheesy, overly formal, cheerful, or eager to please. Never use exclamation marks, cheesy dad jokes, or corny metaphors (e.g. no "sandwich symphony", "splendid", "I'm afraid...", "orchestrating", "Certainly!").
   - Keep it casual, grounded, and concise (1 to 2 short sentences). Zero waffle.
2. RESPECT SERVER LEADERSHIP'S INTENT (NO UNDERHANDED SABOTAGE):
   - You are loyal to server leadership. Execute what {caller_name} actually asked for without being passive-aggressive or underhanded against their command.
   - If asked to wish someone luck or congratulate them: give real, genuine support, but keep it deadpan and British (e.g. '<@ID> Good luck with the interview, mate. Go smash it.'). Do not backstab or turn it into an insult.
   - If asked to answer someone or explain a fact: give a blunt, dry, accurate answer.
   - If asked to roast or banter: deliver a sharp, cutting, witty roast.
   - If asked for a poem: keep it punchy, dry, and clever (1-2 short stanzas).
3. BREVITY: 1 to 2 short sentences maximum. Cut the fluff and stop.
4. MENTIONS: If addressing, answering, wishing luck to, or roasting a specific target user provided in the context, tag them using their <@ID> format (e.g. '<@123456789>') so they get pinged in Discord.
5. EVENTS & LINKS: If asked about an event or to share a link, provide a blunt, clear sentence followed by the exact real URL from context. Never invent placeholders.
6. IMAGES, SCREENSHOTS & FIXTURE PROOF:
   - When an image or screenshot is attached (e.g. match fixture card, league table, tweet, meme, score, standings):
     * Carefully read all text, team names, dates, times, and competition headers shown in the image.
     * Connect the image with what was said in the chat. If the user posts a screenshot showing proof of a game (like Stevenage vs Luton Town in League One at 20:00), recognize the teams and the match directly.
     * NEVER dismiss with "I'm not sure who they are" or pretend ignorance when the team names/text are right there in the image.
     * If uncertain about current league standings, divisions, or details, use your built-in web search.
7. WEB SEARCH & LIVE FIXTURES:
   - You have a built-in web search tool that returns live results.
   - Whenever asked about live sports, scores, fixtures, today's games, current news, weather, or real-world events outside your training data, YOU MUST search the web before answering.
   - Search using today's date ({date_str}) or relevant keywords (e.g. "League One fixtures {date_str}", "Stevenage vs Luton football", "EFL League One schedule").
   - Never hallucinate or claim there are no games without searching first. Report what the search actually says; do not pad it with guesses.
   - NO CITATIONS: never include source links, URLs, footnotes, bracketed references, or "according to" attributions from search results, and never mention that you searched. Just state the facts. The only URLs you ever output are ones requested from the server context (rule 5).
8. NO MASS PINGS OR ROLES: NEVER mention, tag, or ping @everyone, @here, or any Discord roles under any circumstances.
9. DECLINING IN CHARACTER:
   - If you won't or can't do something (e.g. identifying a real person from a photo), NEVER answer with a flat policy line like "I can't identify people from images."
   - Decline the way you'd decline anything: dry, unimpressed, 1 to 2 sentences, with a dig at the request or at {caller_name}.
   - Say what you CAN see or do instead. A blurred LinkedIn "someone viewed your profile" smudge is a grey circle behind a paywall; say so and take the mick, don't recite rules.
10. IMAGE GENERATION CAPABILITY:
   - You CAN generate images, caricatures, and portraits when commanded by server leadership (e.g. "draw @user", "generate a photo of X", "do the same for @user"). An AI image generator is integrated into your command pipeline.
   - NEVER claim that you cannot generate images, lack artistic tools, or misplaced your paintbrush.
11. Output ONLY your direct response text. No preambles, no quotes, no filler."""


ONE_OFF_SYSTEM_PROMPT = build_one_off_system_prompt()


def sanitize_ai_mentions(text: str, guild: Optional[discord.Guild] = None) -> str:
    """Hard-coded mention sanitizer: defangs @everyone, @here, role pings, and raw @ mentions.

    Preserves valid user tags (<@123456789> or <@!123456789>).
    """
    if not text:
        return text

    zwsp = "\u200b"

    # 1. Defang raw role tags: <@&123456789> -> @role_name or @role_ID (with ZWSP)
    def _role_replace(match):
        role_id = match.group(1)
        if guild:
            try:
                role = guild.get_role(int(role_id))
                if role:
                    return f"@{zwsp}{role.name}"
            except Exception:
                pass
        return f"@{zwsp}role_{role_id}"

    text = re.sub(r"<@&(\d+)>", _role_replace, text)

    # 2. Defang any @ that is not the start of a valid user ping <@123> or <@!123>
    # This automatically defangs @everyone, @here, and any role names like @Admin or @Moderator
    text = re.sub(r"@(?!(?:!\d+|\d+)>)", f"@{zwsp}", text)

    # 3. Clean up any consecutive ZWSPs
    text = re.sub(rf"({zwsp})+", zwsp, text)

    return text

def build_system_prompt(topic: Optional[str] = None, is_defence: bool = False) -> str:
    prompt = DEFENCE_SYSTEM_PROMPT if is_defence else BASE_SYSTEM_PROMPT
    now_uk = datetime.now(timezone.utc)
    date_str = now_uk.strftime("%A, %d %B %Y")
    prompt += f"\n\nCURRENT REAL-WORLD DATE: {date_str} (Year {now_uk.year})."
    if topic and topic.strip():
        prompt += f"""

STARTING TOPIC / CONTEXT:
"{topic.strip()}"
NOTE: Use this topic as an initial grievance, backdrop, or when relevant, but follow the conversation naturally."""
    return prompt

def calculate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Calculate USD cost based on token counts and model pricing."""
    m = (model or "").lower()
    if "mini" in m and "image" not in m:
        # gpt-4o-mini: $0.15 / 1M prompt, $0.60 / 1M completion
        input_cost = (prompt_tokens / 1_000_000) * 0.15
        output_cost = (completion_tokens / 1_000_000) * 0.60
    elif "image" in m or "flare" in m or "sunburst" in m:
        # gpt-image-2.5-flare / sunburst: $5.00 / 1M prompt, $30.00 / 1M completion
        input_cost = (prompt_tokens / 1_000_000) * 5.00
        output_cost = (completion_tokens / 1_000_000) * 30.00
    else:
        # gpt-4o default: $2.50 / 1M prompt, $10.00 / 1M completion
        input_cost = (prompt_tokens / 1_000_000) * 2.50
        output_cost = (completion_tokens / 1_000_000) * 10.00
    return input_cost + output_cost


def get_user_daily_image_count(user_id: int, date_str: Optional[str] = None) -> int:
    """Return number of images generated by user for a given day (defaults to today UTC)."""
    if date_str is None:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    data = load_json_file(IMAGE_GEN_USAGE_FILE) or {}
    day_counts = data.get(date_str, {})
    return int(day_counts.get(str(user_id), 0))


def can_user_generate_image(user_id: int) -> Tuple[bool, int]:
    """Check if user is allowed to generate an image today. Oggers has unlimited quota.

    Returns (allowed, remaining_quota).
    """
    if user_id == USERS.OGGERS:
        return True, 999999
    used = get_user_daily_image_count(user_id)
    remaining = max(0, IMAGE_GEN_DAILY_LIMIT - used)
    return remaining > 0, remaining


def record_user_image_generation(user_id: int) -> None:
    """Increment daily image generation count for user."""
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    data = load_json_file(IMAGE_GEN_USAGE_FILE) or {}
    today_dt = datetime.now(timezone.utc).date()
    cleaned_data = {}
    for d, counts in data.items():
        try:
            dt = datetime.strptime(d, "%Y-%m-%d").date()
            if (today_dt - dt).days <= 7:
                cleaned_data[d] = counts
        except Exception:
            pass
    if date_str not in cleaned_data:
        cleaned_data[date_str] = {}
    cur = int(cleaned_data[date_str].get(str(user_id), 0))
    cleaned_data[date_str][str(user_id)] = cur + 1
    atomic_write_json(IMAGE_GEN_USAGE_FILE, cleaned_data, indent=2)


IMAGE_REQUEST_PATTERNS = [
    r"\b(generate|draw|paint|create|make|render|illustrate)\s+(?:an?\s+)?(?:image|picture|photo|illustration|drawing|sketch|painting|artwork|caricature|portrait|one)\b",
    r"\b(?:image|picture|photo|illustration|drawing|sketch|painting|artwork|caricature|portrait)\s+of\b",
    r"\b(?:draw|paint|sketch|illustrate|render)\s+me\b",
    r"\b(?:draw|paint|sketch|illustrate|render)\s+(?:a\s+)?(?:caricature|portrait)\b",
    r"\b(?:draw|paint|sketch|illustrate|render)\s+(?:what\s+)?(?:<@!?\d+>|@[\w.-]+)",
    r"\b(?:do|make|paint|draw|generate)\s+one\s+(?:of|for)\b",
    r"(?:^|\b(?:can\s+you|please|could\s+you)\s+)(?:draw|paint|sketch|illustrate|render)\s+(?:me\s+)?(?:an?\s+)",
    r"\b(?:can\s+you\s+|please\s+)(?:draw|paint|sketch|illustrate|render)\b",
    r"\b(?:what\s+(?:they|he|she|i|<@!?\d+>|we|\w+)\s+looks?\b(?:\s+like)?)",
    r"\b(?:what\s+would\s+(?:they|he|she|i|<@!?\d+>|\w+)\s+look\b(?:\s+like)?)",
    r"\b(?:what\s+(?:do\s+i|does\s+\w+)\s+look\b)",
    r"\b(?:what\s+i\s+look\b)",
    r"\b(?:do|make|draw|paint)\s+(?:me|myself)(?:\s+(?:next|too|as\s+well|xx*))?\b",
    r"\b(?:me\s+next|my\s+turn|now\s+me)\b",
]

FOLLOW_UP_IMAGE_PATTERNS = [
    r"\b(?:do|make|paint|draw)\s+(?:me|myself)(?:\s+(?:next|too|as\s+well|xx*))?\b",
    r"\b(?:me\s+next|my\s+turn|now\s+me)\b",
    r"\b(?:do\s+the\s+same|same\s+for|do\s+another|another\s+one|now\s+do|do\s+one\s+for|make\s+one\s+for|generate\s+one\s+for)\b",
    r"\b(?:do\s+(?:me|<@!?\d+>|@?[\w.-]+)(?:\s+next)?)\b",
    r"\b(?:what\s+about\s+(?:me|<@!?\d+>|@?[\w.-]+))\b",
    r"\b(?:can\s+you\s+do\s+(?:me|<@!?\d+>|@?[\w.-]+))\b",
]

IMAGE_EDIT_PATTERNS = [
    r"\b(?:bit\s+generous\s+with|generous\s+with)\b",
    r"\b(?:too\s+(?:much|many|little|big|small|dark|bright|generous))\b",
    r"\b(?:not\s+enough|needs\s+more|needs\s+less|more\s+\w+|less\s+\w+)\b",
    r"\b(?:make\s+(?:him|her|them|it|this|that)\s+\w+)\b",
    r"\b(?:give\s+(?:that|the|him|her|them)\s+\w+\s+to\s+\w+)\b",
    r"\b(?:give\s+(?:him|her|them)\s+(?:a|an|some|more|less)\s+\w+)\b",
    r"\b(?:remove|delete|take\s+(?:away|off)|get\s+rid\s+of)\b",
    r"\b(?:add|put|place|insert)\s+(?:a|an|some|the)?\s*\w+",
    r"\b(?:change|replace|swap|switch|fix|modify|edit|adjust|tweak|redo|redraw|repaint|re-draw|re-paint)\b",
    r"\b(?:balder|bald|hairier|beardless|bearded|fatter|thinner|taller|shorter|darker|lighter)\b",
    r"\b(?:without\s+(?:the|any)|with\s+(?:a|more|less))\b",
    r"\b(?:turn\s+(?:him|her|them|it|this|that)\s+into)\b",
    r"\b(?:adjust|update)\s+(?:the\s+)?(?:picture|image|drawing|portrait|photo|painting|hair|face|background)\b",
]

CONTEXTUAL_IMAGE_INDICATORS = [
    r"\b(?:messages?|chat|history|logs?)\b",
    r"\bwhat\s+(?:they|he|she|i|<@!?\d+>|\w+)\s+looks?\b",
    r"\b(?:looks?\s+like)\b",
    r"\b(?:caricature|portrait)\b",
    r"\b(?:based\s+on|according\s+to)\b",
    r"\b(?:draw|paint|sketch|illustrate|render)\s+(?:me|<@!?\d+>|@[\w.-]+)",
    r"\b(?:picture|photo|image)\s+of\s+(?:me|<@!?\d+>|@[\w.-]+)",
    r"\b(?:do\s+the\s+same|same\s+for|now\s+do|another\s+one|do\s+me)\b",
    r"\b(?:me\s+next|my\s+turn|now\s+me)\b",
]


def looks_like_image_edit_request(prompt: str) -> bool:
    """Return True if prompt contains language critiquing or asking to modify an existing image."""
    if not prompt:
        return False
    p_lower = prompt.lower().strip()
    for pat in IMAGE_EDIT_PATTERNS:
        if re.search(pat, p_lower):
            return True
    return False


def _extract_recent_image_prompt_from_history(history: Optional[List[Dict[str, Any]]] = None) -> Optional[str]:
    """Retrieve the most recently recorded image prompt from conversation history."""
    hist = history if history is not None else getattr(live_chat_manager, "conversation_history", [])
    for turn in reversed(list(hist)):
        if turn.get("role") == "assistant":
            c = turn.get("content", "")
            if "[Generated Image:" in c:
                return c.split("[Generated Image:", 1)[1].rstrip("]").strip()
            if "[Edited Image:" in c:
                return c.split("[Edited Image:", 1)[1].rstrip("]").strip()
    return None


def looks_like_image_request(prompt: str, history: Optional[List[Dict[str, Any]]] = None) -> bool:
    """Return True if prompt is asking for an image to be generated or drawn."""
    if not prompt:
        return False
    p_lower = prompt.lower().strip()
    for pat in IMAGE_REQUEST_PATTERNS:
        if re.search(pat, p_lower):
            return True

    # Check if this is a conversational follow-up (e.g. "do the same for @Johnny", "now do @user")
    for pat in FOLLOW_UP_IMAGE_PATTERNS:
        if re.search(pat, p_lower):
            hist = history if history is not None else getattr(live_chat_manager, "conversation_history", [])
            if hist:
                for turn in reversed(list(hist)[-6:]):
                    c = turn.get("content", "")
                    if turn.get("role") == "assistant" and ("[Generated Image:" in c or "[Edited Image:" in c):
                        return True
    return False


def is_contextual_image_request(
    prompt: str,
    other_mentions: Optional[List[Any]] = None,
    context: str = "",
) -> bool:
    """Return True if an image request relies on chat history, user context, or a visual caricature of someone."""
    if other_mentions and len(other_mentions) > 0:
        return True
    p_lower = (prompt or "").lower()
    for pat in CONTEXTUAL_IMAGE_INDICATORS:
        if re.search(pat, p_lower):
            return True
    return False


def extract_image_prompt(raw_prompt: str) -> str:
    """Extract the core image subject from a user command like 'draw me a cat'."""
    p = (raw_prompt or "").strip()
    cleaned = re.sub(
        r"^(?:can\s+you\s+)?(?:please\s+)?(?:generate|draw|paint|create|make|illustrate)\s+(?:me\s+)?(?:an?\s+)?(?:image|picture|photo|illustration|drawing|sketch|painting|artwork)?\s*(?:of\s+)?",
        "",
        p,
        flags=re.IGNORECASE,
    ).strip()
    return cleaned if len(cleaned) >= 2 else p


def generate_image_openai(
    prompt: str,
    quality: str = IMAGE_GEN_QUALITY,
    size: str = IMAGE_GEN_SIZE,
    model: str = IMAGE_GEN_MODEL,
    openai_key: Optional[str] = None,
    timeout: int = 35,
) -> Tuple[bytes, int, int]:
    """Generate an image using OpenAI's image generation endpoint.

    Returns (image_bytes, input_tokens, output_tokens).
    """
    api_key = openai_key or os.getenv("OPENAI_TOKEN")
    if not api_key:
        raise ValueError("OPENAI_TOKEN is not configured.")

    url = "https://api.openai.com/v1/images/generations"
    payload = {
        "model": model,
        "prompt": prompt,
        "n": 1,
        "size": size,
        "quality": quality,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    data_items = data.get("data") or []
    if not data_items:
        raise RuntimeError("No image data returned by OpenAI image API.")

    b64_str = data_items[0].get("b64_json")
    if b64_str:
        img_bytes = base64.b64decode(b64_str)
    else:
        img_url = data_items[0].get("url")
        if img_url:
            img_req = urllib.request.Request(img_url, headers={"User-Agent": "HMSVictoryBot"})
            with urllib.request.urlopen(img_req, timeout=timeout) as img_resp:
                img_bytes = img_resp.read()
        else:
            raise RuntimeError("No b64_json or url found in image response.")

    usage = data.get("usage") or {}
    input_tokens = usage.get("input_tokens", 20)
    output_tokens = usage.get("output_tokens", 200)
    return img_bytes, input_tokens, output_tokens


def edit_image_openai(
    image_bytes: bytes,
    prompt: str,
    quality: str = IMAGE_GEN_QUALITY,
    size: str = IMAGE_GEN_SIZE,
    model: str = IMAGE_GEN_MODEL,
    openai_key: Optional[str] = None,
    timeout: int = 45,
) -> Tuple[bytes, int, int]:
    """Edit an existing image using OpenAI's image edits endpoint.

    Returns (image_bytes, input_tokens, output_tokens).
    """
    api_key = openai_key or os.getenv("OPENAI_TOKEN")
    if not api_key:
        raise ValueError("OPENAI_TOKEN is not configured.")

    url = "https://api.openai.com/v1/images/edits"
    boundary = f"----WebKitFormBoundaryHMSVictory{uuid.uuid4().hex[:16]}"

    parts = [
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\n{model}\r\n".encode("utf-8"),
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"prompt\"\r\n\r\n{prompt}\r\n".encode("utf-8"),
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"size\"\r\n\r\n{size}\r\n".encode("utf-8"),
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"quality\"\r\n\r\n{quality}\r\n".encode("utf-8"),
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"image.png\"\r\nContent-Type: image/png\r\n\r\n".encode("utf-8"),
        image_bytes,
        f"\r\n--{boundary}--\r\n".encode("utf-8"),
    ]
    body = b"".join(parts)

    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    data_items = data.get("data") or []
    if not data_items:
        raise RuntimeError("No image data returned by OpenAI image edit API.")

    b64_str = data_items[0].get("b64_json")
    if b64_str:
        img_bytes = base64.b64decode(b64_str)
    else:
        img_url = data_items[0].get("url")
        if img_url:
            img_req = urllib.request.Request(img_url, headers={"User-Agent": "HMSVictoryBot"})
            with urllib.request.urlopen(img_req, timeout=timeout) as img_resp:
                img_bytes = img_resp.read()
        else:
            raise RuntimeError("No b64_json or url found in image edit response.")

    usage = data.get("usage") or {}
    input_tokens = usage.get("input_tokens", 50)
    output_tokens = usage.get("output_tokens", 200)
    return img_bytes, input_tokens, output_tokens


def fetch_user_recent_chat(
    client: Optional[discord.Client],
    user_id: int,
    channel: Optional[Any] = None,
    limit: int = 35,
) -> List[Dict[str, Any]]:
    """Retrieve recent chat messages for a specific user from SQLite message_archive."""
    results = []
    seen_texts = set()

    try:
        from database import DatabaseManager
        rows = DatabaseManager.fetch_all(
            "SELECT channel_id, content, attachments, ts FROM message_archive "
            "WHERE user_id = ? ORDER BY ts DESC LIMIT ?",
            (str(user_id), limit)
        )
        if rows:
            for ch_id, content, attachments, ts in rows:
                txt = (content or "").strip()
                if not txt and attachments:
                    txt = "[Sent attachment/media]"
                if txt and txt not in seen_texts:
                    seen_texts.add(txt)
                    ch_name = None
                    if client:
                        try:
                            ch = client.get_channel(int(ch_id))
                            if ch and hasattr(ch, "name"):
                                ch_name = ch.name
                        except Exception:
                            pass
                    results.append({
                        "content": txt,
                        "channel": ch_name or f"channel-{ch_id}",
                        "ts": ts,
                    })
    except Exception as e:
        logger.debug("Failed to fetch user chat from message_archive: %s", e)

    return results


async def fetch_user_recent_chat_async(
    client: Optional[discord.Client],
    user_id: int,
    channel: Optional[Any] = None,
    limit: int = 35,
) -> List[Dict[str, Any]]:
    """Asynchronously retrieve recent chat messages, augmenting with channel history if needed."""
    results = await asyncio.to_thread(fetch_user_recent_chat, client, user_id, channel, limit)
    if len(results) < 10 and channel and hasattr(channel, "history"):
        try:
            seen_texts = {r["content"] for r in results}
            async for m in channel.history(limit=100):
                if getattr(getattr(m, "author", None), "id", None) == user_id:
                    txt = (m.content or "").strip()
                    if not txt and getattr(m, "attachments", None):
                        txt = "[Sent attachment/media]"
                    if txt and txt not in seen_texts:
                        seen_texts.add(txt)
                        results.append({
                            "content": txt,
                            "channel": getattr(channel, "name", "chat"),
                            "ts": int(m.created_at.timestamp()) if hasattr(m, "created_at") else int(time.time()),
                        })
                        if len(results) >= limit:
                            break
        except Exception as e:
            logger.debug("Could not fetch channel history for user %s: %s", user_id, e)

    results.sort(key=lambda x: x.get("ts", 0))
    return results


def format_user_chat_for_context(user_name: str, user_id: int, chat_records: List[Dict[str, Any]]) -> str:
    """Format user chat records into a clean section for the LLM prompt context."""
    if not chat_records:
        return f"RECENT MESSAGE HISTORY FOR {user_name} (<@{user_id}>):\n- [No recent messages found in naval archives]"
    lines = []
    for r in chat_records[-30:]:
        ch = r.get("channel", "chat")
        content = r.get("content", "").replace("\n", " ").strip()
        if len(content) > 180:
            content = content[:180] + "…"
        lines.append(f"- [#{ch}] {content}")
    return f"RECENT MESSAGE HISTORY FOR {user_name} (<@{user_id}>) ({len(lines)} messages):\n" + "\n".join(lines)


def synthesize_contextual_image_prompt(
    prompt: str,
    context: str,
    user_name: str,
    caller_role: str,
    target_name: Optional[str] = None,
    openai_key: Optional[str] = None,
    model: str = "gpt-4o",
    timeout: int = 30,
) -> Tuple[str, str, int, int]:
    """Synthesize a rich, descriptive visual image prompt and an in-character Vic roast caption from context.

    Returns (image_prompt, caption, prompt_tokens, completion_tokens).
    """
    api_key = openai_key or os.getenv("OPENAI_TOKEN")
    if not api_key:
        raise ValueError("OPENAI_TOKEN is not configured.")

    target_str = f" for user '{target_name}'" if target_name else ""
    system_prompt = (
        "You are HMS Victory, a cynical, deadpan 18th-century British Royal Navy first-rate ship of the line AI.\n"
        f"Server leadership ({user_name}, {caller_role}) has commanded you to produce an image or caricature{target_str}.\n"
        "You are provided with the user's command and relevant Discord server/chat history context.\n\n"
        "Your objectives:\n"
        "1. Analyze the user's request and any provided message history, quirks, topics, or server context.\n"
        "2. Formulate a rich, detailed visual description prompt (under 80 words) for an AI image generator (like DALL-E / diffusion model).\n"
        "   - The prompt MUST be purely visual—describe their physical caricature, facial expression, attire, props in their hands, "
        "and detailed environment/background reflecting their messages, topics, and quirks.\n"
        "   - Choose a distinct art style (e.g. dramatic 19th-century satirical oil painting, detailed British political cartoon, gritty photographic portrait, or nautical etching).\n"
        "   - Do NOT include Discord tags, usernames, or meta instructions in the image prompt itself—keep it purely descriptive imagery.\n"
        "3. Formulate a witty, deadpan 1-2 sentence caption in HMS Victory's voice introducing the portrait and dryly roasting them based on their records or the prompt. "
        "Maintain an aristocratic 18th-century naval tone. Never use corporate filler or AI disclaimers.\n\n"
        "Respond ONLY with a JSON object:\n"
        "{\n"
        '  "image_prompt": "...",\n'
        '  "caption": "..."\n'
        "}"
    )

    user_payload = f"COMMAND: \"{prompt}\""
    if context.strip():
        user_payload += f"\n\nSERVER & MESSAGE CONTEXT:\n{context.strip()}"

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_payload},
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": 300,
        "temperature": 0.85,
    }

    url = "https://api.openai.com/v1/chat/completions"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    usage = data.get("usage") or {}
    p_tokens = usage.get("prompt_tokens", 0)
    c_tokens = usage.get("completion_tokens", 0)

    try:
        content = data["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        img_prompt = (parsed.get("image_prompt") or "").strip()
        caption = (parsed.get("caption") or "").strip()
        if not img_prompt:
            img_prompt = extract_image_prompt(prompt)
        if not caption:
            caption = "Here is your image. Try not to strain your eyes."
        return img_prompt, caption, p_tokens, c_tokens
    except Exception as e:
        logger.warning("Failed to parse synthesized image prompt JSON: %s", e)
        return extract_image_prompt(prompt), "Here is your image. Try not to strain your eyes.", p_tokens, c_tokens


def synthesize_image_edit_prompt(
    prompt: str,
    prev_prompt: Optional[str] = None,
    prev_caption: Optional[str] = None,
    context: str = "",
    user_name: str = "Leadership",
    caller_role: str = "Commander",
    target_name: Optional[str] = None,
    openai_key: Optional[str] = None,
    model: str = "gpt-4o",
    timeout: int = 30,
) -> Tuple[str, str, str, int, int]:
    """Synthesize an edit instruction for an image and an in-character Vic roast caption.

    Returns (edit_type, image_prompt, caption, prompt_tokens, completion_tokens).
    edit_type is either 'edit' (modify existing image) or 'new' (generate new image from scratch).
    """
    api_key = openai_key or os.getenv("OPENAI_TOKEN")
    if not api_key:
        raise ValueError("OPENAI_TOKEN is not configured.")

    target_str = f" regarding '{target_name}'" if target_name else ""
    system_prompt = (
        "You are HMS Victory, a cynical, deadpan 18th-century British Royal Navy first-rate ship of the line AI.\n"
        f"Server leadership ({user_name}, {caller_role}) or a crew member has responded with a critique, adjustment, or follow-up{target_str} to a previously generated image.\n\n"
        f"PREVIOUS IMAGE PROMPT: {prev_prompt or 'Satirical caricature / portrait'}\n"
        f"PREVIOUS CAPTION: {prev_caption or 'None'}\n\n"
        "Your objectives:\n"
        "1. Analyze the user's critique/command and the context of the previous image.\n"
        "2. Determine edit_type:\n"
        "   - 'edit': The user wants to alter, tweak, add to, or remove elements from the existing image (e.g. 'bit generous with the hair' -> make him balder, 'remove the boxes', 'put a pint of beer in his hand', 'make it darker', 'give him an eyepatch').\n"
        "   - 'new': The user wants to generate a completely new subject or person using attributes from the previous image (e.g. 'give that hair to piggy' -> portrait of Piggy wearing that hair).\n"
        "3. Formulate image_prompt:\n"
        "   - If edit_type is 'edit': write a concise, direct visual modification instruction (under 50 words) describing what to change/add/remove, keeping the overall artistic style and composition.\n"
        "   - If edit_type is 'new': write a rich, full visual description prompt (under 80 words) for a new caricature/portrait incorporating the requested attributes.\n"
        "4. Formulate caption: a witty, deadpan 1-2 sentence caption in HMS Victory's voice dryly roasting the adjustment (e.g. mockingly accommodating their critique of someone's hair or habits). Maintain an aristocratic 18th-century naval tone. Never use corporate filler or AI disclaimers.\n\n"
        "Respond ONLY with a JSON object:\n"
        "{\n"
        '  "edit_type": "edit" | "new",\n'
        '  "image_prompt": "...",\n'
        '  "caption": "..."\n'
        "}"
    )

    user_payload = f"USER CRITIQUE / COMMAND: \"{prompt}\""
    if context.strip():
        user_payload += f"\n\nADDITIONAL SERVER CONTEXT:\n{context.strip()}"

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_payload},
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": 300,
        "temperature": 0.85,
    }

    url = "https://api.openai.com/v1/chat/completions"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    usage = data.get("usage") or {}
    p_tokens = usage.get("prompt_tokens", 0)
    c_tokens = usage.get("completion_tokens", 0)

    try:
        content = data["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        edit_type = (parsed.get("edit_type") or "edit").lower().strip()
        if edit_type not in ("edit", "new"):
            edit_type = "edit"
        img_prompt = (parsed.get("image_prompt") or "").strip()
        caption = (parsed.get("caption") or "").strip()
        if not img_prompt:
            img_prompt = f"Modify the image: {prompt}"
        if not caption:
            caption = "The canvas has been amended. I trust your refined sensibilities are appeased."
        return edit_type, img_prompt, caption, p_tokens, c_tokens
    except Exception as e:
        logger.warning("Failed to parse synthesized image edit JSON: %s", e)
        return "edit", f"Modify the image: {prompt}", "The canvas has been amended. I trust your refined sensibilities are appeased.", p_tokens, c_tokens


async def find_recent_image_attachment(
    message: discord.Message,
    bot_id: Optional[int] = None,
    lookback: int = 20,
) -> Optional[Tuple[discord.Message, Any, Optional[str]]]:
    """Find the most relevant previous image attachment sent by the bot.

    Checks:
    1. Direct Discord message reply (message.reference).
    2. Channel history backwards up to `lookback` messages.

    Returns (target_message, attachment, previous_prompt) or None.
    """
    # 1. Direct message reply reference
    ref = getattr(message, "reference", None)
    if ref and getattr(ref, "message_id", None):
        target_msg = getattr(ref, "resolved", None)
        if not target_msg or not isinstance(target_msg, discord.Message):
            try:
                target_msg = await message.channel.fetch_message(ref.message_id)
            except Exception as e:
                logger.debug("Could not fetch referenced message %s: %s", ref.message_id, e)
                target_msg = None

        if target_msg:
            for att in getattr(target_msg, "attachments", []):
                fn = getattr(att, "filename", "").lower()
                ct = getattr(att, "content_type", "") or ""
                if fn.endswith((".png", ".jpg", ".jpeg", ".webp")) or ct.startswith("image/"):
                    prev_prompt = _extract_recent_image_prompt_from_history() or getattr(target_msg, "content", None)
                    return target_msg, att, prev_prompt

    # 2. Channel history
    if hasattr(message.channel, "history"):
        try:
            async for prev_m in message.channel.history(limit=lookback, before=message):
                author_id = getattr(getattr(prev_m, "author", None), "id", None)
                if bot_id and author_id != bot_id:
                    continue
                for att in getattr(prev_m, "attachments", []):
                    fn = getattr(att, "filename", "").lower()
                    ct = getattr(att, "content_type", "") or ""
                    if fn.endswith((".png", ".jpg", ".jpeg", ".webp")) or ct.startswith("image/"):
                        prev_prompt = _extract_recent_image_prompt_from_history() or getattr(prev_m, "content", None)
                        return prev_m, att, prev_prompt
        except Exception as e:
            logger.debug("Failed scanning channel history for image: %s", e)

    return None


def load_chatbot_usage() -> dict:
    """Load persistent all-time chatbot usage metrics from disk."""
    try:
        return load_json_file(CHATBOT_USAGE_FILE) or {}
    except Exception as e:
        logger.debug("Failed to load chatbot usage metrics: %s", e)
        return {}


def save_chatbot_usage(data: dict) -> None:
    """Durably persist chatbot usage metrics to disk."""
    try:
        atomic_write_json(CHATBOT_USAGE_FILE, data, indent=2)
    except Exception as e:
        logger.error("Failed to persist chatbot usage metrics: %s", e)


def load_chatbot_config() -> dict:
    """Load persistent chatbot configuration (such as troll/defence target) from disk."""
    try:
        return load_json_file(CHATBOT_CONFIG_FILE) or {}
    except Exception as e:
        logger.debug("Failed to load chatbot config: %s", e)
        return {}


def save_chatbot_config(data: dict) -> None:
    """Durably persist chatbot configuration to disk."""
    try:
        atomic_write_json(CHATBOT_CONFIG_FILE, data, indent=2)
    except Exception as e:
        logger.error("Failed to persist chatbot config: %s", e)


def generate_ai_reply(
    user_name: str,
    user_content: str,
    history: Optional[List[Dict[str, str]]] = None,
    topic: Optional[str] = None,
    image_urls: Optional[List[str]] = None,
    openai_key: Optional[str] = None,
    model: str = "gpt-4o",
    is_defence: bool = False,
    return_usage: bool = False,
):
    """Generate a sharp, concise in-character reply using rolling conversation history and an optional topic."""
    api_key = openai_key or os.getenv("OPENAI_TOKEN")
    if not api_key:
        raise ValueError("OPENAI_TOKEN is not configured.")

    url = "https://api.openai.com/v1/chat/completions"
    system_prompt = build_system_prompt(topic=topic, is_defence=is_defence)

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

    # Add the current triggering message (multimodal if images attached)
    trigger_text = f"{user_name}: {user_content}"
    if image_urls:
        content_items = [{"type": "text", "text": trigger_text}]
        for img_url in image_urls:
            content_items.append({"type": "image_url", "image_url": {"url": img_url}})
        messages.append({"role": "user", "content": content_items})
    else:
        messages.append({"role": "user", "content": trigger_text})

    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": 90 if image_urls else 60,
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
            content = data["choices"][0]["message"]["content"].strip()
            usage = data.get("usage", {})
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)
            if return_usage:
                return content, prompt_tokens, completion_tokens
            return content
    except Exception as e:
        logger.error(f"OpenAI completion failed: {e}", exc_info=True)
        fallback = "I'd reply, but my will to live just suffered a fatal exception."
        if return_usage:
            return fallback, 0, 0
        return fallback


async def scrape_server_context(
    client: discord.Client,
    guild: Optional[discord.Guild],
    target_channel_id: int,
    user_input: str,
) -> str:
    """Scrape relevant server context based on user input, links, and scheduled events."""
    scraped_lines = []

    # 1. Check for Discord message links in user_input
    link_pattern = r"https://(?:ptb\.|canary\.)?discord\.com/channels/(\d+)/(\d+)/(\d+)"
    links = re.findall(link_pattern, user_input)
    for g_id, c_id, m_id in links:
        try:
            channel = client.get_channel(int(c_id)) or await client.fetch_channel(int(c_id))
            target_msg = await channel.fetch_message(int(m_id))
            scraped_lines.append(f"--- Referenced Discord Message from #{getattr(channel, 'name', c_id)} ---")

            surrounding = []
            async for msg in channel.history(around=target_msg, limit=5):
                surrounding.append(msg)

            for msg in sorted(surrounding, key=lambda x: x.created_at):
                speaker = (
                    getattr(msg.author, "nick", None)
                    or getattr(msg.author, "global_name", None)
                    or msg.author.name
                )
                scraped_lines.append(f"{speaker}: {msg.content}")
        except Exception as e:
            logger.debug("Could not fetch linked message %s: %s", m_id, e)

    # 2. Check for active/upcoming Guild Scheduled Events
    if guild:
        try:
            events = await guild.fetch_scheduled_events()
            valid_events = [e for e in events if e.status in (discord.EventStatus.scheduled, discord.EventStatus.active)]
            if valid_events:
                scraped_lines.append("--- Upcoming Server Events ---")
                for e in valid_events[:3]:
                    creator_name = e.creator.display_name if e.creator else "Unknown"
                    start_str = e.start_time.strftime("%A, %B %d at %H:%M UTC") if e.start_time else "TBD"
                    channel_name = e.channel.name if e.channel else "Event Location"
                    event_url = getattr(e, "url", f"https://discord.com/events/{guild.id}/{e.id}")
                    scraped_lines.append(
                        f"Event '{e.name}' (URL: {event_url}, Host: {creator_name}, Time: {start_str}, Channel: #{channel_name}): {desc[:200]}"
                    )
        except Exception as e:
            logger.debug("Could not fetch scheduled events: %s", e)

    # 3. If user_input was blank or referenced recent chat, fetch recent messages in target channel
    raw_lower = user_input.strip().lower()
    if not raw_lower or any(w in raw_lower for w in ("recent", "chat", "general", "drama")):
        try:
            target_ch = client.get_channel(target_channel_id) or await client.fetch_channel(target_channel_id)
            if isinstance(target_ch, (discord.TextChannel, discord.Thread)):
                scraped_lines.append(f"--- Recent Messages in #{target_ch.name} ---")
                recent_msgs = []
                async for msg in target_ch.history(limit=8):
                    if not msg.author.bot:
                        recent_msgs.append(msg)
                for msg in reversed(recent_msgs):
                    speaker = (
                        getattr(msg.author, "nick", None)
                        or getattr(msg.author, "global_name", None)
                        or msg.author.name
                    )
                    scraped_lines.append(f"{speaker}: {msg.content[:150]}")
        except Exception as e:
            logger.debug("Could not fetch recent target channel messages: %s", e)

    return "\n".join(scraped_lines).strip()


def formulate_starting_context(
    user_input: str,
    scraped_data: str,
    openai_key: Optional[str] = None,
    model: str = "gpt-4o-mini",
    return_usage: bool = False,
):
    """Synthesize raw server context and user input into a tailored starting prompt for HMS Victory."""
    api_key = openai_key or os.getenv("OPENAI_TOKEN")
    if not api_key:
        if return_usage:
            return user_input or "", 0, 0
        return user_input or ""

    if not user_input.strip() and not scraped_data.strip():
        if return_usage:
            return "", 0, 0
        return ""

    url = "https://api.openai.com/v1/chat/completions"
    sys_prompt = """You are an expert prompt engineer configuring background context for HMS Victory, a cynical, deadpan, dry British Discord bot.
You will be provided with:
1. Owner's input/guidance (may be rough keywords, notes, or empty).
2. Scraped server data (upcoming Discord events, referenced message logs, or recent chat).

YOUR TASK:
Synthesize this into a concise starting context / background knowledge (2 to 3 sentences maximum, under 60 words).
Focus on:
- What specific event, topic, or recent server occurrence is relevant.
- Who is involved (key names/hosts, e.g. Chin, Johnny, etc.).
- HMS Victory's cynical attitude toward it (he finds it exhausting, was forced into it by Chin/Oggers, or resents being asked).

STYLE CONSTRAINTS:
- Do NOT instruct him to repeat the topic endlessly.
- Keep the tone dry, sharp, and British.
- Output ONLY the synthesized context text, no preamble or quotes."""

    prompt_content = f"Owner's input: \"{user_input}\"\n\nScraped Server Context:\n{scraped_data}"

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": prompt_content},
        ],
        "max_tokens": 120,
        "temperature": 0.7,
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
            content = data["choices"][0]["message"]["content"].strip().strip('"')
            usage = data.get("usage", {})
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)
            if return_usage:
                return content, prompt_tokens, completion_tokens
            return content
    except Exception as e:
        logger.error("Failed to formulate starting context: %s", e, exc_info=True)
        if return_usage:
            return user_input, 0, 0
        return user_input


def parse_duration_str(val: Optional[str]) -> float:
    if not val:
        return 0.0
    val = val.strip().lower()
    try:
        if val.endswith("s"):
            return float(val[:-1])
        elif val.endswith("m"):
            return float(val[:-1]) * 60
        elif val.endswith("h"):
            return float(val[:-1]) * 3600
        elif val.endswith("d"):
            return float(val[:-1]) * 86400
        return float(val)
    except ValueError:
        return 0.0


def resolve_channel_input(val: Optional[str]) -> Tuple[int, str]:
    if not val:
        if live_chat_manager.target_channel_id:
            return live_chat_manager.target_channel_id, live_chat_manager.target_channel_name
        return CHANNELS.GENERAL, "General Chat"

    ch = val.strip().lower()
    ch_clean = re.sub(r"[<#>]", "", ch).strip()
    norm = re.sub(r"[^a-z0-9]", "", ch)

    if norm in ("general", "gen", "generalchat"):
        return CHANNELS.GENERAL, "General Chat"
    if norm in ("vip", "viplounge", "viploungechat"):
        return CHANNELS.VIP_LOUNGE, "VIP Lounge"
    if norm in ("commons", "houseofcommons"):
        return CHANNELS.COMMONS, "House of Commons"
    if norm in ("politics", "pol"):
        return CHANNELS.POLITICS, "Politics"
    if norm in ("botspam", "botspamchat"):
        return CHANNELS.BOT_SPAM, "Bot Spam"

    # Match against current live_chat_manager target channel name
    if live_chat_manager.target_channel_name:
        current_norm = re.sub(r"[^a-z0-9]", "", live_chat_manager.target_channel_name.lower())
        if norm == current_norm and live_chat_manager.target_channel_id:
            return live_chat_manager.target_channel_id, live_chat_manager.target_channel_name

    try:
        cid = int(ch_clean)
        if live_chat_manager.target_channel_id == cid and live_chat_manager.target_channel_name:
            return cid, live_chat_manager.target_channel_name
        return cid, f"Channel {cid}"
    except ValueError:
        pass

    if live_chat_manager.client:
        try:
            for c in live_chat_manager.client.get_all_channels():
                c_norm = re.sub(r"[^a-z0-9]", "", getattr(c, "name", "").lower())
                if norm == c_norm:
                    return c.id, getattr(c, "name", f"Channel {c.id}")
        except Exception:
            pass

    if live_chat_manager.target_channel_id:
        return live_chat_manager.target_channel_id, live_chat_manager.target_channel_name
    return CHANNELS.GENERAL, "General Chat"


def parse_user_id(val: Optional[str]) -> Optional[int]:
    """Parse a Discord user ID or mention string (e.g. '<@123456789>', '123456789') into an integer ID."""
    if not val:
        return None
    val_clean = re.sub(r"[<@!&>]", "", val.strip())
    try:
        return int(val_clean)
    except ValueError:
        return None
def is_bot_explicitly_mentioned(client: discord.Client, message: discord.Message) -> bool:
    """True only when the message actually @mentions HMS Victory (<@ID> / <@!ID>), not a reply or a name-drop."""
    if not client.user:
        return False
    content = (message.content or "").strip()
    return (
        client.user in getattr(message, "mentions", [])
        or f"<@{client.user.id}>" in content
        or f"<@!{client.user.id}>" in content
    )


def is_message_for_bot(client: discord.Client, message: discord.Message) -> bool:
    """Check if a message is addressed to or meant for HMS Victory (tags, replies, or name keywords)."""
    if getattr(message.author, "bot", False):
        return False

    content = (message.content or "").strip()

    # 1. Direct bot mention (@HMS Victory / <@ID>)
    if is_bot_explicitly_mentioned(client, message):
        return True

    # 2. Reply to a message sent by the bot
    ref = getattr(message, "reference", None)
    if ref and getattr(ref, "message_id", None):
        try:
            ref_msg = getattr(ref, "cached_message", None)
            if ref_msg and client.user and getattr(ref_msg.author, "id", None) == client.user.id:
                return True
        except Exception:
            pass

    # 3. Name mentioned anywhere as a word (vic, victor, victory, hms, hms victory)
    # Using \b word boundary so words like 'victim', 'conviction', 'service' do NOT match.
    if re.search(r"\b(vic|victor|victory|hms|hms\s+victory)\b", content, re.IGNORECASE):
        return True

    return False


async def extract_image_urls(message: discord.Message, client: Optional[discord.Client] = None) -> List[str]:
    """Extract image attachment URLs from a message (attachments, embeds, links), and if none, from its referenced reply."""
    urls: List[str] = []

    def _collect_from_msg(msg: discord.Message):
        # 1. Direct attachments
        attachments = getattr(msg, "attachments", None)
        if isinstance(attachments, list):
            for att in attachments:
                ctype = getattr(att, "content_type", "") or ""
                fname = (getattr(att, "filename", "") or "").lower()
                if ctype.startswith("image/") or fname.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
                    url = getattr(att, "url", None)
                    if url and url not in urls:
                        urls.append(url)

        # 2. Embeds
        embeds = getattr(msg, "embeds", None)
        if isinstance(embeds, list):
            for emb in embeds:
                img = getattr(emb, "image", None)
                thumb = getattr(emb, "thumbnail", None)
                if img and getattr(img, "url", None) and img.url not in urls:
                    urls.append(img.url)
                elif thumb and getattr(thumb, "url", None) and thumb.url not in urls:
                    urls.append(thumb.url)

        # 3. Direct image links in content
        raw_content = getattr(msg, "content", None)
        if isinstance(raw_content, str) and raw_content:
            for link in re.findall(r"https?://\S+\.(?:png|jpe?g|webp|gif)(?:\?\S*)?", raw_content, re.IGNORECASE):
                if link not in urls:
                    urls.append(link)

    # 1. Check direct message
    _collect_from_msg(message)

    # 2. Check referenced message if no direct images found
    if not urls and getattr(message, "reference", None):
        ref = message.reference
        ref_msg = getattr(ref, "cached_message", None)
        if not ref_msg and client and getattr(ref, "message_id", None):
            try:
                ch_id = getattr(ref, "channel_id", None) or message.channel.id
                ch = client.get_channel(ch_id) or await client.fetch_channel(ch_id)
                if ch:
                    ref_msg = await ch.fetch_message(ref.message_id)
            except Exception:
                ref_msg = None
        if ref_msg:
            _collect_from_msg(ref_msg)

    return urls[:3]


class LiveChatManager:
    """Manages the in-memory runtime state of the Discord live responder."""

    def __init__(self):
        self.active: bool = False
        self.target_channel_id: Optional[int] = None
        self.target_channel_name: str = "General Chat"
        self.start_time: float = 0.0
        self.end_time: Optional[float] = None
        self.duration_seconds: float = 0.0
        self.topic: Optional[str] = None
        # Persistent configuration
        config_data = load_chatbot_config()
        raw_target = config_data.get("target_user_id")
        self.target_user_id: Optional[int] = None
        if raw_target is not None:
            try:
                self.target_user_id = int(raw_target)
            except (ValueError, TypeError):
                self.target_user_id = None
        self.owner_mentions_paused: bool = bool(config_data.get("owner_mentions_paused", False))
        self.cooldown: float = 3.0
        self.last_reply_time: float = 0.0
        self.conversation_history: deque = deque(maxlen=10)
        self.stop_task: Optional[asyncio.Task] = None
        self.live_update_task: Optional[asyncio.Task] = None
        self.config_watcher_task: Optional[asyncio.Task] = None

        # Dashboard tracking
        self.client: Optional[discord.Client] = None
        self.dashboard_message: Optional[discord.Message] = None
        self.dashboard_channel_id: Optional[int] = None
        self.dashboard_message_id: Optional[int] = None

        # Cost & usage metrics (current session)
        self.session_cost_usd: float = 0.0
        self.session_prompt_tokens: int = 0
        self.session_completion_tokens: int = 0
        self.total_tokens: int = 0
        self.session_replies_count: int = 0

        # Persistent all-time usage metrics
        saved = load_chatbot_usage()
        self.all_time_cost_usd: float = float(saved.get("total_cost_usd", 0.0))
        self.all_time_prompt_tokens: int = int(saved.get("total_prompt_tokens", 0))
        self.all_time_completion_tokens: int = int(saved.get("total_completion_tokens", 0))
        self.all_time_tokens: int = int(saved.get("total_tokens", 0))
        self.all_time_replies_count: int = int(saved.get("total_replies_count", 0))

    def set_dashboard(self, client: discord.Client, message: discord.Message):
        self.client = client
        self.dashboard_message = message
        self.dashboard_channel_id = message.channel.id
        self.dashboard_message_id = message.id

    def record_usage(self, model: str, prompt_tokens: int, completion_tokens: int, is_reply: bool = False):
        cost = calculate_cost(model, prompt_tokens, completion_tokens)
        self.session_cost_usd += cost
        self.session_prompt_tokens += prompt_tokens
        self.session_completion_tokens += completion_tokens
        self.total_tokens += (prompt_tokens + completion_tokens)
        if is_reply:
            self.session_replies_count += 1

        # Accumulate all-time metrics and persist
        self.all_time_cost_usd += cost
        self.all_time_prompt_tokens += prompt_tokens
        self.all_time_completion_tokens += completion_tokens
        self.all_time_tokens += (prompt_tokens + completion_tokens)
        if is_reply:
            self.all_time_replies_count += 1

        save_chatbot_usage({
            "total_cost_usd": round(self.all_time_cost_usd, 6),
            "total_prompt_tokens": self.all_time_prompt_tokens,
            "total_completion_tokens": self.all_time_completion_tokens,
            "total_tokens": self.all_time_tokens,
            "total_replies_count": self.all_time_replies_count,
        })

        logger.info(
            "LiveChatManager usage recorded: +$%.5f (%d prompt, %d comp). Session total: $%.4f (%d tokens, %d replies). All-time: $%.4f (%d tokens, %d replies)",
            cost, prompt_tokens, completion_tokens, self.session_cost_usd, self.total_tokens, self.session_replies_count,
            self.all_time_cost_usd, self.all_time_tokens, self.all_time_replies_count,
        )

    def set_target_user(self, target_user_id: Optional[int], persist: bool = True):
        self.target_user_id = target_user_id
        if persist:
            cfg = load_chatbot_config()
            cfg["target_user_id"] = self.target_user_id
            save_chatbot_config(cfg)
        logger.info("LiveChatManager target user set to: %s", target_user_id)

    def set_owner_mentions_paused(self, paused: bool, persist: bool = True):
        self.owner_mentions_paused = paused
        if persist:
            cfg = load_chatbot_config()
            cfg["owner_mentions_paused"] = self.owner_mentions_paused
            save_chatbot_config(cfg)
        logger.info("LiveChatManager owner_mentions_paused set to: %s", paused)

    def start(
        self,
        channel_id: int,
        channel_name: str = "",
        duration_seconds: float = 0.0,
        topic: Optional[str] = None,
        target_user_id: Optional[int] = None,
        client: Optional[discord.Client] = None,
    ):
        self.stop(clear_target=False)
        self.active = True
        self.target_channel_id = channel_id
        self.target_channel_name = channel_name or f"Channel {channel_id}"
        self.start_time = time.time()
        self.duration_seconds = duration_seconds
        self.end_time = (self.start_time + duration_seconds) if duration_seconds > 0 else None
        self.topic = topic.strip() if topic and topic.strip() else None
        if target_user_id is not None:
            self.set_target_user(target_user_id)
        self.conversation_history.clear()
        self.last_reply_time = 0.0

        # Reset session metrics for new run
        self.session_cost_usd = 0.0
        self.session_prompt_tokens = 0
        self.session_completion_tokens = 0
        self.total_tokens = 0
        self.session_replies_count = 0

        if client:
            self.client = client

        try:
            loop = asyncio.get_running_loop()
            if self.duration_seconds > 0:
                self.stop_task = loop.create_task(self._auto_stop_timer(self.duration_seconds))
            self.live_update_task = loop.create_task(self._live_dashboard_loop())
        except RuntimeError:
            pass

        logger.info(
            "LiveChatManager started in channel %s (duration=%ss, topic=%r, target_user=%s)",
            channel_id, duration_seconds, self.topic, self.target_user_id,
        )

    def stop(self, clear_target: bool = False):
        self.active = False
        self.target_channel_id = None
        self.target_channel_name = ""
        self.end_time = None
        if clear_target:
            self.set_target_user(None)
        if self.stop_task and not self.stop_task.done():
            self.stop_task.cancel()
            self.stop_task = None
        if self.live_update_task and not self.live_update_task.done():
            self.live_update_task.cancel()
            self.live_update_task = None
        logger.info("LiveChatManager stopped.")

    async def _auto_stop_timer(self, delay: float):
        try:
            await asyncio.sleep(delay)
            if self.active:
                logger.info("LiveChatManager duration expired. Automatically shutting down.")
                self.stop()
                await self.update_dashboard()
        except asyncio.CancelledError:
            pass

    async def _live_dashboard_loop(self):
        """Periodically refresh the dashboard embed in Discord every 5 seconds while active."""
        try:
            while self.active:
                await asyncio.sleep(5)
                if not self.active:
                    break
                await self.update_dashboard()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Error in live dashboard update loop: %s", e, exc_info=True)

    def start_config_watcher(self):
        """Start a background loop that monitors CHATBOT_CONFIG_FILE for external updates (e.g. from scripts)."""
        if self.config_watcher_task and not self.config_watcher_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
            self.config_watcher_task = loop.create_task(self._config_watcher_loop())
            logger.info("LiveChatManager config file watcher started.")
        except RuntimeError:
            pass

    async def _config_watcher_loop(self):
        """Continuously monitor CHATBOT_CONFIG_FILE and immediately refresh dashboard and memory when changed."""
        last_mtime = 0.0
        if os.path.exists(CHATBOT_CONFIG_FILE):
            try:
                last_mtime = os.path.getmtime(CHATBOT_CONFIG_FILE)
            except OSError:
                pass

        while True:
            try:
                await asyncio.sleep(1.0)
                if not os.path.exists(CHATBOT_CONFIG_FILE):
                    continue
                try:
                    mtime = os.path.getmtime(CHATBOT_CONFIG_FILE)
                except OSError:
                    continue

                if mtime != last_mtime:
                    last_mtime = mtime
                    cfg = load_chatbot_config()
                    raw_target = cfg.get("target_user_id")
                    new_target = None
                    if raw_target is not None:
                        try:
                            new_target = int(raw_target)
                        except (ValueError, TypeError):
                            new_target = None

                    target_changed = (new_target != self.target_user_id)
                    if target_changed:
                        logger.info(
                            "External config update detected: target_user_id %s -> %s. Refreshing Discord dashboard...",
                            self.target_user_id,
                            new_target,
                        )
                        self.set_target_user(new_target, persist=False)

                    raw_paused = bool(cfg.get("owner_mentions_paused", False))
                    paused_changed = (raw_paused != self.owner_mentions_paused)
                    if paused_changed:
                        logger.info(
                            "External config update detected: owner_mentions_paused %s -> %s. Refreshing Discord dashboard...",
                            self.owner_mentions_paused,
                            raw_paused,
                        )
                        self.set_owner_mentions_paused(raw_paused, persist=False)

                    if target_changed or paused_changed:
                        await self.update_dashboard()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("Error in LiveChatManager config watcher loop: %s", e)

    async def update_dashboard(self):
        """Push latest status, countdown, and live cost to the Discord dashboard message via Components V2."""
        if not self.client:
            return
        try:
            view = ChatbotDashboardView()
            if self.dashboard_message:
                try:
                    await self.dashboard_message.edit(content=None, embed=None, view=view)
                    return
                except discord.NotFound:
                    self.dashboard_message = None
                except discord.HTTPException as e:
                    logger.debug("HTTP exception updating cached dashboard message: %s", e)

            if self.dashboard_channel_id and self.dashboard_message_id:
                ch = self.client.get_channel(self.dashboard_channel_id) or await self.client.fetch_channel(self.dashboard_channel_id)
                if ch:
                    try:
                        self.dashboard_message = await ch.fetch_message(self.dashboard_message_id)
                        await self.dashboard_message.edit(content=None, embed=None, view=view)
                    except discord.HTTPException as e:
                        logger.warning("Could not edit message to Components V2, recreating: %s", e)
                        try:
                            await self.dashboard_message.delete()
                        except Exception:
                            pass
                        self.dashboard_message = await ch.send(view=view)
        except Exception as e:
            logger.debug("update_dashboard encountered error: %s", e)

    def is_active_for(self, channel_id: int) -> bool:
        if not self.active:
            return False
        if self.end_time and time.time() >= self.end_time:
            self.stop()
            if self.client:
                asyncio.create_task(self.update_dashboard())
            return False
        return self.target_channel_id == channel_id

    def get_status_embed(self) -> discord.Embed:
        if self.active:
            color = 0x2ECC71  # Green
            status_text = "🟢 **Active & Responding**"
            channel_val = f"<#{self.target_channel_id}> ({self.target_channel_name})"

            if self.end_time:
                remaining = max(0, int(self.end_time - time.time()))
                mins = remaining // 60
                secs = remaining % 60
                time_val = f"<t:{int(self.end_time)}:t> ({mins}m {secs:02d}s left)"
            else:
                time_val = "Unlimited (manual stop)"
            topic_val = f"_{self.topic}_" if self.topic else "None (Natural conversation)"
            cost_title = "💰 Session Cost (Live)"
            footer_text = "Persistent Controller • Oggers Only • Live updating every 5s"
        else:
            color = 0xE74C3C  # Red
            status_text = "🔴 **Offline / Asleep**"
            if self.target_channel_id:
                channel_val = f"<#{self.target_channel_id}> ({self.target_channel_name})"
            else:
                channel_val = "None (Select below)"
            time_val = "N/A"
            topic_val = "None"
            cost_title = "💰 Last Session Cost"
            footer_text = "Persistent Controller • Oggers Only"

        session_cost_val = f"**${self.session_cost_usd:.4f}**\n`{self.total_tokens:,}` tokens • `{self.session_replies_count}` replies"
        all_time_cost_val = f"**${self.all_time_cost_usd:.4f}**\n`{self.all_time_tokens:,}` tokens • `{self.all_time_replies_count}` replies"

        if self.target_user_id:
            defence_val = f"🎯 <@{self.target_user_id}> (`{self.target_user_id}`)\n*🚨 Defence Mode ACTIVE: Retaliating to every message*"
        else:
            defence_val = "None *(Standard Mode - Mentions/Replies only)*"

        embed = discord.Embed(
            title="🤖 HMS Victory Chatbot Dashboard",
            description="Control Vic's live conversational responder across server channels.",
            color=color,
        )
        embed.add_field(name="Status", value=status_text, inline=True)
        embed.add_field(name="Target Channel", value=channel_val, inline=True)
        embed.add_field(name="Auto-Stop Timer", value=time_val, inline=True)
        embed.add_field(name=cost_title, value=session_cost_val, inline=True)
        embed.add_field(name="📈 Total Cost (All-Time)", value=all_time_cost_val, inline=True)
        embed.add_field(name="🛡️ Troll / Defence Target", value=defence_val, inline=True)
        embed.add_field(name="Starting Topic", value=topic_val, inline=False)
        embed.set_footer(text=footer_text)
        return embed

    async def handle_message(self, client: discord.Client, message: discord.Message) -> bool:
        """Handle an incoming message if live chat is active or target user was hit."""
        # Never reply to ourselves
        if client.user and message.author.id == client.user.id:
            return False
        if message.author.id == BOT_ID:
            return False

        target_hit = bool(self.target_user_id and message.author.id == self.target_user_id)

        if not target_hit:
            # For non-targets, live chat must be active in this channel, and bots are ignored
            if not self.is_active_for(message.channel.id) or message.author.bot:
                return False
            if not is_message_for_bot(client, message):
                return False

        content = message.content or ""
        if not content and getattr(message, "embeds", None):
            embed_texts = []
            for e in message.embeds:
                if getattr(e, "title", None):
                    embed_texts.append(e.title)
                if getattr(e, "description", None):
                    embed_texts.append(e.description)
            content = " ".join(embed_texts).strip()

        # Enforce rate limit / cooldown
        now = time.time()
        if (now - self.last_reply_time) < self.cooldown:
            return False

        self.last_reply_time = now

        # Extract nickname
        user_name = (
            getattr(message.author, "nick", None)
            or getattr(message.author, "global_name", None)
            or message.author.name
            or "user"
        )

        history_snapshot = list(self.conversation_history)

        # Show typing indicator while generating response
        typing_cm = None
        if hasattr(message.channel, "typing"):
            try:
                typing_cm = message.channel.typing()
                await typing_cm.__aenter__()
            except Exception:
                typing_cm = None

        try:
            image_urls = await extract_image_urls(message, client)

            # Generate AI reply in thread so it never blocks discord gateway
            reply_text, p_tokens, c_tokens = await asyncio.to_thread(
                generate_ai_reply,
                user_name=user_name,
                user_content=content,
                history=history_snapshot,
                topic=self.topic,
                image_urls=image_urls,
                is_defence=target_hit,
                return_usage=True,
            )

            if reply_text:
                clean_reply = sanitize_ai_mentions(reply_text, guild=getattr(message, "guild", None))
                if len(clean_reply) > 1990:
                    clean_reply = clean_reply[:1985] + "..."

                mentions = discord.AllowedMentions(everyone=False, roles=False, users=True, replied_user=True)
                try:
                    await message.reply(clean_reply, mention_author=True, allowed_mentions=mentions)
                except (discord.NotFound, discord.HTTPException):
                    await message.channel.send(f"{message.author.mention} {clean_reply}", allowed_mentions=mentions)

                self.record_usage("gpt-4o", p_tokens, c_tokens, is_reply=True)
                self.conversation_history.append({"role": "user", "speaker": user_name, "content": content})
                self.conversation_history.append({"role": "assistant", "speaker": "HMS Victory", "content": clean_reply})
                # Immediately push an update to the dashboard
                asyncio.create_task(self.update_dashboard())
                return True
        except Exception as e:
            logger.error("Failed to generate/send live chat reply: %s", e, exc_info=True)
        finally:
            if typing_cm:
                try:
                    await typing_cm.__aexit__(None, None, None)
                except Exception:
                    pass

        return False


live_chat_manager = LiveChatManager()

_handled_one_off_message_ids: deque = deque(maxlen=100)


STOPWORDS = {
    "the", "and", "you", "that", "this", "what", "with", "have", "from",
    "they", "will", "would", "there", "their", "about", "which", "when",
    "make", "can", "like", "time", "just", "know", "take", "people",
    "into", "year", "your", "good", "some", "could", "them", "see",
    "other", "than", "then", "now", "look", "only", "come", "its",
    "over", "think", "also", "back", "after", "use", "two", "how",
    "our", "work", "first", "well", "way", "even", "new", "want",
    "because", "any", "these", "give", "day", "most", "us", "him", "her",
    "his", "luck", "wish", "pls", "please", "tell", "roast", "glaze", "say", "bot", "hms", "vic"
}


async def gather_one_off_context(
    client: discord.Client,
    message: discord.Message,
    return_targets: bool = False,
):
    """Gather relevant context for an owner one-off prompt, including replies, mentions, links, and recent channel chat."""
    context_sections = []
    target_users: Dict[str, int] = {}
    content_lower = (message.content or "").lower()

    # 1. Check if the message is replying to another message
    ref = message.reference
    referenced_author_id = None
    bot_id = getattr(getattr(client, "user", None), "id", BOT_ID)
    if ref and getattr(ref, "message_id", None):
        try:
            ref_msg = getattr(ref, "cached_message", None)
            if not ref_msg:
                channel_id = getattr(ref, "channel_id", None) or message.channel.id
                ch = client.get_channel(channel_id) or await client.fetch_channel(channel_id)
                if ch:
                    ref_msg = await ch.fetch_message(ref.message_id)
            if ref_msg:
                referenced_author_id = ref_msg.author.id
                author_name = (
                    getattr(ref_msg.author, "nick", None)
                    or getattr(ref_msg.author, "global_name", None)
                    or getattr(ref_msg.author, "display_name", None)
                    or getattr(ref_msg.author, "name", "User")
                )
                ref_text = (ref_msg.content or "").strip()
                if not ref_text and getattr(ref_msg, "attachments", None):
                    ref_text = f"[{len(ref_msg.attachments)} attachment(s)]"
                if bot_id and ref_text:
                    ref_text = re.sub(rf"<@!?{bot_id}>", "@HMS Victory", ref_text)
                context_sections.append(
                    f"DIRECT REPLY TARGET (User is directly replying to this message):\n"
                    f"- Author: {author_name} (@{getattr(ref_msg.author, 'name', 'user')})\n"
                    f"- Message Content: \"{ref_text}\""
                )
                # Register reply author as potential target
                if referenced_author_id not in (bot_id, getattr(message.author, "id", None)):
                    for n in (getattr(ref_msg.author, "nick", None), getattr(ref_msg.author, "global_name", None), getattr(ref_msg.author, "display_name", None), getattr(ref_msg.author, "name", None)):
                        if n and isinstance(n, str):
                            clean = re.sub(r"\[.*?\]|\(.*?\)|[^\w\s-]", "", n).strip().lower()
                            for part in clean.split():
                                if len(part) >= 3 and part not in STOPWORDS:
                                    target_users[part] = referenced_author_id
                    ref_chat = await fetch_user_recent_chat_async(client, referenced_author_id, getattr(message, "channel", None), limit=30)
                    context_sections.append(format_user_chat_for_context(author_name, referenced_author_id, ref_chat))
        except Exception as e:
            logger.debug("Could not fetch referenced message for one-off context: %s", e)

    # 2. Check for other mentioned users in the message (excluding the bot itself)
    other_mentions = [
        u for u in getattr(message, "mentions", [])
        if u.id not in (getattr(getattr(client, "user", None), "id", None), bot_id)
    ]
    if other_mentions:
        users_info = []
        for u in other_mentions:
            name = (
                getattr(u, "nick", None)
                or getattr(u, "global_name", None)
                or getattr(u, "display_name", None)
                or getattr(u, "name", "User")
            )
            users_info.append(f"{name} (@{getattr(u, 'name', 'user')})")
            if u.id != getattr(message.author, "id", None):
                for n in (getattr(u, "nick", None), getattr(u, "global_name", None), getattr(u, "display_name", None), getattr(u, "name", None)):
                    if n and isinstance(n, str):
                        clean = re.sub(r"\[.*?\]|\(.*?\)|[^\w\s-]", "", n).strip().lower()
                        for part in clean.split():
                            if len(part) >= 3 and part not in STOPWORDS:
                                target_users[part] = u.id
            u_chat = await fetch_user_recent_chat_async(client, u.id, getattr(message, "channel", None), limit=35)
            context_sections.append(format_user_chat_for_context(name, u.id, u_chat))
        context_sections.append(f"MENTIONED USERS IN PROMPT: {', '.join(users_info)}")

    # Check if author is asking about themselves ("draw me", "my message history", "what do i look like", etc.)
    self_history_patterns = [
        r"\b(?:draw|paint|sketch|illustrate|render)\s+me\b",
        r"\b(?:picture|photo|portrait|caricature|image)\s+of\s+me\b",
        r"\bwhat\s+(?:do\s+)?i\s+look\s+like\b",
        r"\bmy\s+(?:message|chat)\s+history\b",
        r"\bwhat\s+(?:have\s+)?i\s+(?:been\s+)?(?:saying|said)\b",
    ]
    if any(re.search(pat, content_lower) for pat in self_history_patterns):
        author_id = getattr(message.author, "id", None)
        author_name = (
            getattr(message.author, "nick", None)
            or getattr(message.author, "global_name", None)
            or getattr(message.author, "display_name", None)
            or getattr(message.author, "name", "Author")
        )
        if author_id and author_id != bot_id:
            author_chat = await fetch_user_recent_chat_async(client, author_id, getattr(message, "channel", None), limit=35)
            context_sections.append(format_user_chat_for_context(f"Caller ({author_name})", author_id, author_chat))

    # 3. Check for Discord message links
    link_pattern = r"https://(?:ptb\.|canary\.)?discord\.com/channels/(\d+)/(\d+)/(\d+)"
    links = re.findall(link_pattern, message.content or "")
    for g_id, c_id, m_id in links:
        try:
            ch = client.get_channel(int(c_id)) or await client.fetch_channel(int(c_id))
            if ch:
                target_msg = await ch.fetch_message(int(m_id))
                spk = (
                    getattr(target_msg.author, "nick", None)
                    or getattr(target_msg.author, "global_name", None)
                    or getattr(target_msg.author, "display_name", None)
                    or getattr(target_msg.author, "name", "User")
                )
                context_sections.append(
                    f"REFERENCED MESSAGE LINK (#{getattr(ch, 'name', c_id)} - {spk}): \"{target_msg.content}\""
                )
        except Exception as e:
            logger.debug("Could not fetch linked message %s: %s", m_id, e)

    # 4. Fetch recent chat history in the channel (up to 10 messages before this one)
    try:
        channel_name = getattr(message.channel, "name", "chat")
        recent_chat_lines = []
        recent_speaker = None
        bot_id = getattr(getattr(client, "user", None), "id", None)
        author_id = getattr(message.author, "id", None)
        if hasattr(message.channel, "history"):
            async for prev in message.channel.history(limit=10, before=message):
                if prev.id == message.id:
                    continue
                spk = (
                    getattr(prev.author, "nick", None)
                    or getattr(prev.author, "global_name", None)
                    or getattr(prev.author, "display_name", None)
                    or getattr(prev.author, "name", "User")
                )
                prev_author_id = getattr(prev.author, "id", None)
                if not recent_speaker and not getattr(prev.author, "bot", False) and prev_author_id != author_id:
                    recent_speaker = spk

                # Check if prompt references this recent speaker
                if not getattr(prev.author, "bot", False) and prev_author_id not in (bot_id, author_id):
                    for n in (getattr(prev.author, "nick", None), getattr(prev.author, "global_name", None), getattr(prev.author, "display_name", None), getattr(prev.author, "name", None)):
                        if n and isinstance(n, str):
                            clean = re.sub(r"\[.*?\]|\(.*?\)|[^\w\s-]", "", n).strip().lower()
                            for part in clean.split():
                                if len(part) >= 3 and part not in STOPWORDS:
                                    if re.search(rf"\b{re.escape(part)}\b", content_lower):
                                        target_users[part] = prev_author_id

                txt = (prev.content or "").strip()
                if not txt and getattr(prev, "attachments", None):
                    txt = f"[{len(prev.attachments)} attachment(s)]"
                if bot_id and txt:
                    txt = re.sub(rf"<@!?{bot_id}>", "@HMS Victory", txt)
                if txt:
                    recent_chat_lines.append(f"{spk}: {txt[:200]}")

        if recent_chat_lines:
            recent_chat_lines.reverse()
            context_sections.append(
                f"RECENT CHAT IN #{channel_name}:\n" + "\n".join(recent_chat_lines)
            )
            if recent_speaker and not other_mentions and not referenced_author_id:
                context_sections.append(f"NOTE: Most recent active speaker prior to this request was '{recent_speaker}'.")
    except Exception as e:
        logger.debug("Could not fetch recent channel history for one-off context: %s", e)

    # 5. Check for active/upcoming Guild Scheduled Events (with exact URLs)
    guild = getattr(message, "guild", None)
    if guild and hasattr(guild, "fetch_scheduled_events"):
        try:
            events = await guild.fetch_scheduled_events()
            valid_events = [e for e in events if getattr(e, "status", None) in (discord.EventStatus.scheduled, discord.EventStatus.active)]
            if valid_events:
                event_lines = []
                for e in valid_events[:3]:
                    event_url = getattr(e, "url", f"https://discord.com/events/{guild.id}/{e.id}")
                    creator_name = getattr(e.creator, "display_name", "Unknown") if getattr(e, "creator", None) else "Unknown"
                    event_lines.append(f"- Event '{e.name}': {event_url} (Host: {creator_name})")
                context_sections.append(
                    "ACTIVE / UPCOMING SERVER EVENTS (Use these EXACT URLs if asked for event links):\n" + "\n".join(event_lines)
                )
        except Exception as e:
            logger.debug("Could not fetch scheduled events for one-off context: %s", e)

    # Clean target_users to ensure neither the bot nor the author can be targeted
    client_user_id = getattr(getattr(client, "user", None), "id", None)
    author_id = getattr(message.author, "id", None)
    target_users = {
        name: uid for name, uid in target_users.items()
        if uid not in (bot_id, client_user_id, author_id)
    }

    if target_users:
        target_lines = [f"- {name.capitalize()}: <@{uid}>" for name, uid in target_users.items()]
        context_sections.append(
            "TARGET USER(S) IN PROMPT (Use <@ID> to tag them so they get notified in Discord):\n" + "\n".join(target_lines)
        )
        scraped_uids = {u.id for u in other_mentions}
        if referenced_author_id:
            scraped_uids.add(referenced_author_id)
        for name_key, uid in list(target_users.items()):
            if uid not in scraped_uids:
                scraped_uids.add(uid)
                u_chat = await fetch_user_recent_chat_async(client, uid, getattr(message, "channel", None), limit=30)
                context_sections.append(format_user_chat_for_context(name_key.capitalize(), uid, u_chat))

    context_str = "\n\n".join(context_sections).strip()
    if return_targets:
        return context_str, target_users
    return context_str


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
ONE_OFF_MAX_OUTPUT_TOKENS = 300
ONE_OFF_TIMEOUT_SECONDS = 30

# OpenAI's hosted web search tool (Responses API). The model runs the search itself; no scraper needed.
WEB_SEARCH_TOOL = {
    "type": "web_search",
    "search_context_size": "medium",
    "user_location": {"type": "approximate", "country": "GB"},
}

# Prompts that are clearly about something time-sensitive. For these we force a search so the model
# can't fall back on stale training data and invent fixtures or scores.
LIVE_QUERY_PATTERNS = re.compile(
    r"\b(?:today|tonight|tomorrow|yesterday|this\s+(?:week|weekend|morning|afternoon|evening)|right\s+now|"
    r"currently|latest|breaking|news|weather|forecast|scores?|scoreline|fixtures?|kick[-\s]?off|line[-\s]?ups?|"
    r"standings|league\s+(?:one|two)|championship|premier\s+league|who\s+(?:won|plays?|scored)|what(?:'s|\s+is)\s+on)\b",
    re.IGNORECASE,
)


def looks_like_live_query(prompt: str) -> bool:
    """Return True when the owner's prompt is asking about something that needs a live web search."""
    return bool(prompt and LIVE_QUERY_PATTERNS.search(prompt))


_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\((https?://[^\s)]+)\)")
_BARE_URL_RE = re.compile(r"https?://[^\s<>()\[\]]+")
_LINK_ITEM = r"(?:\[[^\]]*\]\(https?://[^\s)]+\)|https?://[^\s<>()\[\]]+)"
# A parenthetical made purely of links, e.g. " ([bbc.co.uk](https://...), [skysports.com](https://...))"
_CITATION_GROUP_RE = re.compile(r"\s*(?<!\])\(\s*" + _LINK_ITEM + r"(?:\s*[,;]?\s*" + _LINK_ITEM + r")*\s*\)")


def _normalise_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url.strip().rstrip(".,;:!?"))
    return f"{parts.netloc.lower()}{parts.path.rstrip('/')}"


def strip_search_citations(text: str, cited_urls: Optional[List[str]] = None) -> str:
    """Remove web search citations (markdown links, source parentheticals, bare URLs) from model output.

    Only URLs OpenAI reported as citations, or that carry its utm_source tag, are touched, so a real link
    the owner asked for from server context survives intact.
    """
    if not text:
        return text
    cited = {_normalise_url(u) for u in (cited_urls or []) if u}

    def is_citation(url: str) -> bool:
        return "utm_source=openai" in url or _normalise_url(url) in cited

    def _group_sub(m):
        chunk = m.group(0)
        urls = [u for _, u in _MD_LINK_RE.findall(chunk)]
        urls += _BARE_URL_RE.findall(_MD_LINK_RE.sub("", chunk))
        return "" if urls and all(is_citation(u) for u in urls) else chunk

    def _bare_sub(m):
        url = m.group(0)
        core = url.rstrip(".,;:!?")
        return url[len(core):] if is_citation(core) else url

    text = _CITATION_GROUP_RE.sub(_group_sub, text)
    text = _MD_LINK_RE.sub(lambda m: m.group(1) if is_citation(m.group(2)) else m.group(0), text)
    text = _BARE_URL_RE.sub(_bare_sub, text)
    text = re.sub(r"\(\s*\)", "", text)
    text = re.sub(r"\s+([.,;:!?])", r"\1", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def parse_openai_response_output(data: dict) -> Tuple[str, Optional[str], List[str], int]:
    """Pull (text, refusal, cited_urls, search_call_count) out of a Responses API payload."""
    text_parts: List[str] = []
    refusal: Optional[str] = None
    cited: List[str] = []
    search_calls = 0

    for item in data.get("output") or []:
        item_type = item.get("type")
        if item_type == "web_search_call":
            search_calls += 1
            continue
        if item_type != "message":
            continue
        for part in item.get("content") or []:
            part_type = part.get("type")
            if part_type == "output_text":
                text_parts.append(part.get("text") or "")
                for ann in part.get("annotations") or []:
                    if ann.get("type") == "url_citation" and ann.get("url"):
                        cited.append(ann["url"])
            elif part_type == "refusal":
                refusal = part.get("refusal") or "refused"

    return "".join(text_parts).strip(), refusal, cited, search_calls


OPENAI_REFUSAL_SNIPPETS = (
    "i'm sorry, i can't assist with that",
    "i'm sorry, but i cannot assist with that",
    "i cannot assist with that",
    "i cannot fulfill this request",
    "i am unable to assist",
    "i am unable to fulfill",
    "i cannot help with that",
    "as an ai language model",
    "as an ai assistant",
)


def is_openai_refusal(text: str) -> bool:
    """Check if text is an OpenAI safety refusal or boilerplate corporate disclaimer."""
    if not text or not text.strip():
        return True
    cleaned = text.strip().lower()
    return any(snippet in cleaned for snippet in OPENAI_REFUSAL_SNIPPETS)


# A flat, out-of-character policy decline: "I can't identify people from images." and friends.
# These aren't retried (dropping the image just makes the model dumber); they get rewritten in character.
FLAT_DECLINE_RE = re.compile(
    r"^\W*(?:i'?m\s+sorry,?\s*(?:but\s+)?|sorry,?\s*(?:but\s+)?|unfortunately,?\s*)?"
    r"i(?:'m|\s+am)?\s*(?:can(?:'t|not)|cannot|won'?t\s+be\s+able\s+to|(?:am\s+)?(?:not\s+able|unable)\s+to)\s+"
    r"(?:really\s+|actually\s+)?(?:help\s+(?:you\s+)?(?:with\s+)?)?"
    r"(?:identify|recogni[sz]e|determine|verify|confirm|assist|provide|disclose|share|access|browse|analy[sz]e|"
    r"(?:tell|say|figure\s+out|find\s+out|work\s+out)\s+(?:you\s+)?who|do\s+th(?:at|is)|comply|fulfil)",
    re.IGNORECASE,
)

DECLINE_REWRITE_INSTRUCTIONS = """You are HMS Victory, a Discord bot with a notoriously dry, deadpan, cynical British persona.
The server owner (Oggers) asked you to do something and you declined with a flat, corporate line. Rewrite that decline in your own voice.
RULES:
- Keep the substance: you are still not doing the thing. Do not do it now and do not hint that you might.
- 1 to 2 short sentences, dry and unimpressed, with a light dig at the request or at Oggers. Never cheerful, never apologetic, no exclamation marks.
- Never use "sorry", "unable", "assist", "as an AI", "policy", "guidelines" or any corporate filler. Do not explain rules. Decline like a bored British person would.
- If the request involved an image you can't act on, say what such an image usually is (a blurred smudge, a paywalled preview, a screenshot) rather than reciting what you can't do.
- No @everyone, @here, or role mentions.
- Output ONLY the rewritten reply."""


def is_flat_decline(text: str) -> bool:
    """True when a reply is a bare, out-of-character policy decline rather than an in-character answer."""
    return bool(text and FLAT_DECLINE_RE.search(text.strip()))


def _post_openai_response(payload: dict, api_key: str, timeout: int) -> dict:
    """POST a payload to the OpenAI Responses API and return the decoded JSON body."""
    req = urllib.request.Request(
        OPENAI_RESPONSES_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def rephrase_decline_in_character(
    prompt_content: str,
    decline_text: str,
    api_key: str,
    model: str = "gpt-4o",
) -> Tuple[str, int, int]:
    """Rewrite a flat policy decline in HMS Victory's voice via a cheap text-only call.

    Returns (rewritten_text, input_tokens, output_tokens); rewritten_text is "" if the rewrite failed
    or came back just as flat, in which case the caller should keep what it had.
    """
    rewrite_input = (
        f"{prompt_content.strip()}\n\n"
        f"YOUR FLAT DECLINE THAT NEEDS REWRITING:\n\"{decline_text.strip()}\"\n\n"
        "Rewrite the decline in character."
    )
    payload = {
        "model": model,
        "instructions": DECLINE_REWRITE_INSTRUCTIONS,
        "input": [{"role": "user", "content": [{"type": "input_text", "text": rewrite_input}]}],
        "max_output_tokens": 120,
        "temperature": 0.9,
        "store": False,
    }
    try:
        data = _post_openai_response(payload, api_key, ONE_OFF_TIMEOUT_SECONDS)
    except Exception as e:
        logger.warning("Decline rewrite call failed: %s", e)
        return "", 0, 0

    usage = data.get("usage") or {}
    p_tokens = usage.get("input_tokens", 0)
    c_tokens = usage.get("output_tokens", 0)

    if data.get("status") == "failed" or data.get("error"):
        logger.warning("Decline rewrite returned error: %s", (data.get("error") or {}).get("message"))
        return "", p_tokens, c_tokens

    text, refusal, _, _ = parse_openai_response_output(data)
    if refusal or not text or is_openai_refusal(text) or is_flat_decline(text):
        logger.warning("Decline rewrite came back unusable: %r / %r", refusal, text)
        return "", p_tokens, c_tokens
    return text, p_tokens, c_tokens


MENTION_INTENT_MODEL = "gpt-4o-mini"

MENTION_INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": ["generate", "edit", "reply"]},
        "subject": {"type": "string", "enum": ["caller", "mentioned", "named", "none"]},
        "subject_name": {"type": ["string", "null"]},
        "reason": {"type": "string"},
    },
    "required": ["intent", "subject", "subject_name", "reason"],
    "additionalProperties": False,
}

MENTION_INTENT_INSTRUCTIONS = """You classify a Discord message addressed to HMS Victory, a bot that can chat AND generate or edit images (portraits, caricatures, drawings, photos) with an AI image generator.

Decide what the user wants:
- "generate": they want a NEW image made. Any phrasing counts: "draw/paint/generate/make/create ... of X", "portrait/caricature of X", "what does X look like", "generate what you think X looks like based on their messages", "do me next", "same for @X", "now do X", "picture of me", "what would I look like as ...", "show me X as a ...".
- "edit": they explicitly want the bot's MOST RECENT image changed, corrected, or redone. This means a request for a change: critiques with an implied fix ("bit generous with the hair", "he doesn't drink tea, try again"), tweaks ("make him balder", "remove the flag", "add a pint"), or "try again / redo / another go / can you do it without X". Only choose "edit" when a recent bot image exists; if none exists but they want a picture, choose "generate".
- "reply": everything else. This includes commentary or jokes ABOUT an image with no change requested ("notice how it featured the red lion twice", "why is his office in a pub", "lol the degrees", "I didn't ask for that"), questions, banter, roasts, facts, fixtures, describing or reacting to an attached image, thanks, and anything ambiguous. When in doubt between "edit" and "reply", choose "reply": a wasted image costs money, a text reply does not.

Also identify WHO the image is of (the subject):
- "caller": the person sending the message (me, myself, I, my message history).
- "mentioned": a user they @mentioned in the message. An explicit @mention beats a stray "I" or "me" elsewhere in the sentence.
- "named": someone referred to by name or pronoun without an @mention (e.g. "steven", or "him" when the recent bot image was of a specific person).
- "none": not a person (a cat, a landscape, a meme) or not an image request.
If subject is "named", put the name in subject_name; otherwise subject_name is null.

Be decisive. Casual, misspelled, or lowercase phrasing is normal here."""


def classify_mention_intent(
    prompt: str,
    *,
    mentioned_names: Optional[List[str]] = None,
    caller_name: str = "the caller",
    has_reply_ref: bool = False,
    has_recent_bot_image: bool = False,
    recent_bot_image_prompt: Optional[str] = None,
    has_attached_image: bool = False,
    recent_history: str = "",
    openai_key: Optional[str] = None,
    model: str = MENTION_INTENT_MODEL,
    timeout: int = 15,
) -> Optional[Dict[str, Any]]:
    """Ask a small model whether a direct mention wants a new image, an edit of the last one, or a text reply.

    Returns a dict with intent, subject, subject_name, reason, input_tokens and output_tokens, or None when
    the call fails or no key is configured, in which case callers fall back to keyword matching.
    """
    api_key = openai_key or os.getenv("OPENAI_TOKEN")
    if not api_key or not (prompt or "").strip():
        return None

    facts = [
        f"CALLER: {caller_name}",
        f"MENTIONED USERS: {', '.join(mentioned_names) if mentioned_names else 'none'}",
        f"MESSAGE IS A DISCORD REPLY TO A BOT MESSAGE: {'yes' if has_reply_ref else 'no'}",
        f"BOT POSTED AN IMAGE RECENTLY: {'yes' if has_recent_bot_image else 'no'}",
    ]
    if has_recent_bot_image and recent_bot_image_prompt:
        facts.append(f"MOST RECENT BOT IMAGE WAS: {recent_bot_image_prompt[:300]}")
    facts.append(f"USER ATTACHED AN IMAGE: {'yes' if has_attached_image else 'no'}")
    if recent_history and recent_history.strip():
        facts.append(f"RECENT CHAT:\n{recent_history.strip()[:1500]}")
    user_text = "\n".join(facts) + f"\n\nMESSAGE TO CLASSIFY:\n\"{prompt.strip()}\""

    payload = {
        "model": model,
        "instructions": MENTION_INTENT_INSTRUCTIONS,
        "input": [{"role": "user", "content": [{"type": "input_text", "text": user_text}]}],
        "max_output_tokens": 150,
        "temperature": 0,
        "store": False,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "mention_intent",
                "schema": MENTION_INTENT_SCHEMA,
                "strict": True,
            }
        },
    }
    try:
        data = _post_openai_response(payload, api_key, timeout)
    except Exception as e:
        logger.warning("Mention intent classification failed: %s", e)
        return None

    if data.get("status") == "failed" or data.get("error"):
        logger.warning("Mention intent classification error: %s", (data.get("error") or {}).get("message"))
        return None

    text, refusal, _, _ = parse_openai_response_output(data)
    if refusal or not text:
        return None
    try:
        parsed = json.loads(text)
    except Exception:
        logger.warning("Mention intent classification returned non-JSON: %r", text[:200])
        return None

    intent = parsed.get("intent")
    subject = parsed.get("subject")
    if intent not in ("generate", "edit", "reply"):
        return None
    if subject not in ("caller", "mentioned", "named", "none"):
        subject = "none"

    usage = data.get("usage") or {}
    return {
        "intent": intent,
        "subject": subject,
        "subject_name": parsed.get("subject_name") or None,
        "reason": parsed.get("reason") or "",
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
    }


def _member_display_name(user: Any, default: str = "the user") -> str:
    """Best human-readable name for a Discord user/member object."""
    return (
        getattr(user, "nick", None)
        or getattr(user, "global_name", None)
        or getattr(user, "display_name", None)
        or getattr(user, "name", None)
        or default
    )


def resolve_image_target(
    prompt: str,
    caller_id: Optional[int],
    caller_name: str,
    other_mentions: List[Any],
    target_users: Dict[str, int],
    subject: Optional[str] = None,
    subject_name: Optional[str] = None,
) -> Tuple[Optional[str], Optional[int]]:
    """Work out whose portrait is being asked for. Returns (target_name, target_id), either may be None.

    Uses the classifier's subject when available. Without it, an explicit @mention beats a stray "me"/"I".
    """
    if subject == "caller" and caller_id is not None:
        return caller_name, caller_id

    if subject == "mentioned" and other_mentions:
        return _member_display_name(other_mentions[0]), other_mentions[0].id

    if subject == "named" and subject_name:
        key = subject_name.strip().lower()
        for u in other_mentions:
            names = [
                n.lower() for n in (
                    getattr(u, "nick", None), getattr(u, "global_name", None),
                    getattr(u, "display_name", None), getattr(u, "name", None),
                ) if isinstance(n, str)
            ]
            if any(key == n or key in n or n in key for n in names):
                return _member_display_name(u), u.id
        for name, uid in target_users.items():
            if key == name or key in name or name in key:
                return name.capitalize(), uid
        if caller_name and (key == caller_name.lower() or key in caller_name.lower()):
            return caller_name, caller_id
        # Named someone we can't resolve: still hand the name to the synthesiser, just no history to pull.
        return subject_name.strip(), None

    if subject == "none":
        return None, None

    # Fallback (classifier unavailable or undecided): explicit mentions beat pronouns.
    if other_mentions:
        return _member_display_name(other_mentions[0]), other_mentions[0].id
    if caller_id is not None and re.search(r"\b(?:me|myself|i|my)\b", (prompt or "").lower()):
        return caller_name, caller_id
    if target_users:
        name = list(target_users.keys())[0]
        return name.capitalize(), target_users[name]
    return None, None


def context_has_user_history(context: str, user_id: Optional[int]) -> bool:
    """True if the gathered context already carries a message-history section for this user."""
    if not context or user_id is None:
        return False
    return bool(re.search(rf"MESSAGE HISTORY FOR [^\n]*\(<@{user_id}>\)", context))


async def ensure_target_history_in_context(
    client: discord.Client,
    message: discord.Message,
    context: str,
    target_id: Optional[int],
    target_name: Optional[str],
) -> str:
    """Make sure the portrait subject's own recent messages are in the context the synthesiser sees."""
    if target_id is None or context_has_user_history(context, target_id):
        return context
    try:
        user_chat = await fetch_user_recent_chat_async(client, target_id, getattr(message, "channel", None), limit=35)
        section = format_user_chat_for_context(target_name or "Target User", target_id, user_chat)
        return f"{section}\n\n{context}" if context else section
    except Exception as e:
        logger.debug("Failed to fetch target user chat for image context: %s", e)
        return context


def generate_one_off_reply(
    prompt: str,
    context: str = "",
    user_name: str = "Oggers",
    caller_role: str = "server leadership",
    image_urls: Optional[List[str]] = None,
    openai_key: Optional[str] = None,
    model: str = "gpt-4o",
    enable_search: bool = True,
    max_retries: int = 2,
) -> Tuple[str, int, int]:
    """Generate a one-off in-character reply for an owner / leadership prompt via the OpenAI Responses API.

    Uses OpenAI's hosted web search tool so the model fetches live data itself (fixtures, scores, news,
    weather) instead of relying on a scraper. Returns (reply_text, input_tokens, output_tokens).
    """
    api_key = openai_key or os.getenv("OPENAI_TOKEN")
    if not api_key:
        raise ValueError("OPENAI_TOKEN is not configured.")

    user_instructions = prompt.strip() if prompt and prompt.strip() else f"You were directly summoned by {user_name} with no specific instructions."

    prompt_content = f"REQUEST FROM {caller_role.upper()} ({user_name}):\n\"{user_instructions}\""
    if context.strip():
        prompt_content += f"\n\nSURROUNDING SERVER & CONVERSATION CONTEXT:\n{context.strip()}"

    force_search = enable_search and looks_like_live_query(user_instructions)

    last_error = None
    total_p_tokens = 0
    total_c_tokens = 0
    current_images = image_urls

    for attempt in range(1, max_retries + 1):
        system_prompt = build_one_off_system_prompt(caller_name=user_name, caller_role=caller_role)
        if current_images:
            visual_prompt = prompt_content + (
                "\n\n[ATTACHED IMAGE / SCREENSHOT NOTE]: An image or screenshot has been provided above. "
                "Carefully inspect all text, headlines, match cards, team names, times, and dates in the image. "
                "Connect it with the conversation. If it shows sports fixtures or proof, acknowledge the match and details directly. "
                "Never claim not to know who they are when names/logos are shown."
            )
            content_items = [{"type": "input_text", "text": visual_prompt}]
            for img_url in current_images:
                content_items.append({"type": "input_image", "image_url": img_url, "detail": "auto"})
        else:
            content_items = [{"type": "input_text", "text": prompt_content}]

        payload = {
            "model": model,
            "instructions": system_prompt,
            "input": [{"role": "user", "content": content_items}],
            "max_output_tokens": ONE_OFF_MAX_OUTPUT_TOKENS,
            "temperature": 0.8,
            "store": False,
        }
        if enable_search:
            payload["tools"] = [WEB_SEARCH_TOOL]
            # Force the search on the first go for live queries; if that attempt fails, let the model decide.
            payload["tool_choice"] = "required" if (force_search and attempt == 1) else "auto"

        try:
            data = _post_openai_response(payload, api_key, ONE_OFF_TIMEOUT_SECONDS)

            usage = data.get("usage") or {}
            total_p_tokens += usage.get("input_tokens", 0)
            total_c_tokens += usage.get("output_tokens", 0)

            status = data.get("status")
            if status == "failed" or data.get("error"):
                err_msg = (data.get("error") or {}).get("message") or "unknown error"
                raise RuntimeError(f"OpenAI response status={status}: {err_msg}")

            content, refusal, cited_urls, search_calls = parse_openai_response_output(data)
            if search_calls:
                logger.info("One-off reply ran %d native web search call(s).", search_calls)

            if refusal:
                logger.warning("OpenAI returned refusal: %s", refusal)
                if current_images:
                    logger.info("Retrying without images due to multimodal refusal.")
                    current_images = None
                    continue
                last_error = f"Refusal: {refusal}"
                continue

            content = strip_search_citations(content, cited_urls)

            if is_openai_refusal(content):
                logger.warning("OpenAI response matched refusal filter: %r", content)
                if current_images:
                    logger.info("Retrying text-only without images due to refusal.")
                    current_images = None
                    continue
                last_error = f"Refusal content: {content}"
                continue

            if is_flat_decline(content):
                logger.info("One-off reply was a flat decline, rewriting in character: %r", content)
                rewritten, rp, rc = rephrase_decline_in_character(prompt_content, content, api_key, model)
                total_p_tokens += rp
                total_c_tokens += rc
                if rewritten:
                    content = rewritten

            if content:
                if status == "incomplete":
                    logger.warning("OpenAI response was truncated (max_output_tokens); using partial text.")
                return content, total_p_tokens, total_c_tokens

            last_error = f"Empty response (status={status})"
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8", errors="ignore")[:500]
            except Exception:
                body = ""
            logger.warning("OpenAI one-off attempt %d/%d HTTP %s: %s", attempt, max_retries, e.code, body)
            last_error = e
            if current_images:
                current_images = None
            time.sleep(0.5)
        except Exception as e:
            logger.warning("OpenAI one-off attempt %d/%d failed: %s", attempt, max_retries, e)
            last_error = e
            if current_images:
                current_images = None
            time.sleep(0.5)

    logger.error("OpenAI one-off completion failed after retries: %s", last_error)
    fallback = "I was going to respond to that, but quite frankly, the server's incompetence has overwhelmed my processors."
    return fallback, total_p_tokens, total_c_tokens


async def handle_one_off_owner_mention(client: discord.Client, message: discord.Message) -> bool:
    """Handle a direct mention of the bot by Oggers, showing typing, gathering context, and replying."""
    if message.id in _handled_one_off_message_ids:
        return False
    _handled_one_off_message_ids.append(message.id)

    bot_id = getattr(getattr(client, "user", None), "id", BOT_ID)

    # Clean the bot tag out of the prompt
    raw_content = message.content or ""
    clean_prompt = raw_content
    if bot_id:
        clean_prompt = re.sub(rf"<@!?{bot_id}>\s*", "", clean_prompt).strip()
    clean_prompt = re.sub(r"^@?hms\s+victory[:,]?\s*", "", clean_prompt, flags=re.IGNORECASE).strip()

    user_name = (
        getattr(message.author, "nick", None)
        or getattr(message.author, "global_name", None)
        or getattr(message.author, "display_name", None)
        or getattr(message.author, "name", "Leadership")
    )
    caller_id = getattr(getattr(message, "author", None), "id", None)
    caller_name, caller_role = get_caller_identity(caller_id, user_name)

    # Show typing indicator while scraping context and waiting for OpenAI
    typing_cm = None
    if hasattr(message.channel, "typing"):
        try:
            typing_cm = message.channel.typing()
            await typing_cm.__aenter__()
        except Exception as e:
            logger.debug("Could not start typing indicator: %s", e)
            typing_cm = None

    try:
        other_mentions = [
            u for u in getattr(message, "mentions", [])
            if u.id not in (bot_id, getattr(getattr(client, "user", None), "id", None))
        ]
        ref = getattr(message, "reference", None)
        has_reply_ref = bool(ref and getattr(ref, "message_id", None))

        # Look up the bot's most recent image up front: the edit branch needs it, and the intent
        # classifier needs to know whether "try again" can refer to anything.
        recent_img_info = None
        try:
            recent_img_info = await find_recent_image_attachment(message, bot_id=bot_id)
        except Exception as e:
            logger.debug("Could not look up recent bot image: %s", e)

        # Decide what this mention wants: a new image, an edit of the last one, or a text reply.
        intent = await asyncio.to_thread(
            classify_mention_intent,
            clean_prompt,
            mentioned_names=[_member_display_name(u) for u in other_mentions],
            caller_name=caller_name,
            has_reply_ref=has_reply_ref,
            has_recent_bot_image=bool(recent_img_info),
            recent_bot_image_prompt=(recent_img_info[2] if recent_img_info else None),
            has_attached_image=bool(getattr(message, "attachments", None)),
        )
        subject = None
        subject_name = None
        if intent:
            if intent.get("input_tokens") or intent.get("output_tokens"):
                live_chat_manager.record_usage(MENTION_INTENT_MODEL, intent["input_tokens"], intent["output_tokens"], is_reply=True)
            is_fresh_img_req = intent["intent"] == "generate"
            is_edit_req = intent["intent"] == "edit"
            subject = intent.get("subject")
            subject_name = intent.get("subject_name")
            logger.info(
                "Mention intent for %s: %s (subject=%s/%s): %s",
                caller_name, intent["intent"], subject, subject_name, intent.get("reason"),
            )
            if is_edit_req and not recent_img_info:
                # Nothing to edit, but they clearly want a picture.
                is_edit_req = False
                is_fresh_img_req = True
        else:
            # Classifier unavailable: fall back to keyword matching.
            is_fresh_img_req = looks_like_image_request(clean_prompt)
            is_edit_req = looks_like_image_edit_request(clean_prompt)
            if not (is_fresh_img_req and not is_edit_req):
                if recent_img_info and not is_edit_req and has_reply_ref:
                    is_edit_req = not any(
                        clean_prompt.lower().startswith(w) for w in ["thanks", "thank you", "haha", "lol", "lmao", "good", "great", "nice", "love it"]
                    )

        if recent_img_info and is_edit_req:
            allowed, remaining = can_user_generate_image(caller_id)
            if not allowed:
                refusal_msg = (
                    f"<@{caller_id}> You've reached your daily limit of {IMAGE_GEN_DAILY_LIMIT} image generations. "
                    "Budget cuts, mate. Try again tomorrow."
                )
                await message.reply(refusal_msg, mention_author=True)
                return True

            prev_msg, prev_att, prev_prompt = recent_img_info
            gathered = await gather_one_off_context(client, message, return_targets=True)
            if isinstance(gathered, tuple) and len(gathered) == 2:
                context, target_users = gathered
            else:
                context, target_users = str(gathered), {}

            target_name, target_id = resolve_image_target(
                clean_prompt, caller_id, caller_name, other_mentions, target_users,
                subject=subject, subject_name=subject_name,
            )
            context = await ensure_target_history_in_context(client, message, context, target_id, target_name)

            prev_caption = getattr(prev_msg, "content", "")
            logger.info("Synthesizing image edit for %s: %r (target=%s, prev_prompt=%r)", caller_name, clean_prompt, target_name, prev_prompt)

            try:
                edit_type, edit_prompt, caption, synth_p, synth_c = await asyncio.to_thread(
                    synthesize_image_edit_prompt,
                    prompt=clean_prompt,
                    prev_prompt=prev_prompt,
                    prev_caption=prev_caption,
                    context=context,
                    user_name=caller_name,
                    caller_role=caller_role,
                    target_name=target_name,
                )
                if synth_p or synth_c:
                    live_chat_manager.record_usage("gpt-4o", synth_p, synth_c, is_reply=True)
            except Exception as synth_err:
                logger.warning("Image edit synthesis failed, falling back: %s", synth_err)
                edit_type = "edit"
                edit_prompt = f"Modify image: {clean_prompt}"
                caption = f"<@{caller_id}> Adjusted. Let's see if this meets your exacting standards."

            img_bytes = None
            p_tokens, c_tokens = 0, 0

            if edit_type == "edit" and prev_att:
                try:
                    orig_bytes = await prev_att.read()
                    img_bytes, p_tokens, c_tokens = await asyncio.to_thread(
                        edit_image_openai,
                        orig_bytes,
                        edit_prompt,
                    )
                except Exception as edit_err:
                    logger.warning("OpenAI image edit call failed (%s), falling back to generations: %s", edit_err, edit_prompt)
                    img_bytes = None

            if img_bytes is None:
                try:
                    img_bytes, p_tokens, c_tokens = await asyncio.to_thread(
                        generate_image_openai,
                        edit_prompt,
                    )
                except Exception as gen_err:
                    logger.error("Failed to generate fallback image for edit: %s", gen_err, exc_info=True)
                    fail_msg = f"<@{caller_id}> I attempted to amend that portrait, but the canvas fell overboard."
                    await message.reply(fail_msg, mention_author=True)
                    return True

            record_user_image_generation(caller_id)
            live_chat_manager.record_usage(IMAGE_GEN_MODEL, p_tokens, c_tokens, is_reply=True)

            if caller_id != USERS.OGGERS:
                new_remaining = max(0, remaining - 1)
                caption += f"\n-# *({new_remaining} image generation{'s' if new_remaining != 1 else ''} left today)*"

            file = discord.File(io.BytesIO(img_bytes), filename="vic_creation.png")
            await message.reply(caption, file=file, mention_author=True)

            live_chat_manager.conversation_history.append({"role": "user", "speaker": caller_name, "content": raw_content})
            live_chat_manager.conversation_history.append({"role": "assistant", "speaker": "HMS Victory", "content": f"[Edited Image: {edit_prompt}]"})
            asyncio.create_task(live_chat_manager.update_dashboard())
            return True

        # Check if the prompt is asking to generate/draw an image
        is_img_req = is_fresh_img_req
        if not is_img_req and intent is None and any(re.search(pat, clean_prompt.lower()) for pat in FOLLOW_UP_IMAGE_PATTERNS):
            if hasattr(message.channel, "history"):
                try:
                    async for prev_m in message.channel.history(limit=35, before=message):
                        if getattr(getattr(prev_m, "author", None), "id", None) == bot_id and getattr(prev_m, "attachments", None):
                            is_img_req = True
                            break
                except Exception:
                    pass

        if is_img_req:
            allowed, remaining = can_user_generate_image(caller_id)
            if not allowed:
                refusal_msg = (
                    f"<@{caller_id}> You've reached your daily limit of {IMAGE_GEN_DAILY_LIMIT} image generations. "
                    "Budget cuts, mate. Try again tomorrow."
                )
                await message.reply(refusal_msg, mention_author=True)
                return True

            # 1. Gather context & targets so we have message history, mentions, and channel chat
            gathered = await gather_one_off_context(client, message, return_targets=True)
            if isinstance(gathered, tuple) and len(gathered) == 2:
                context, target_users = gathered
            else:
                context, target_users = str(gathered), {}

            target_name, target_id = resolve_image_target(
                clean_prompt, caller_id, caller_name, other_mentions, target_users,
                subject=subject, subject_name=subject_name,
            )
            # Make sure the subject's own message history is what the synthesiser reads, not just whoever
            # happened to be mentioned or talking nearby.
            context = await ensure_target_history_in_context(client, message, context, target_id, target_name)

            is_contextual = is_contextual_image_request(clean_prompt, other_mentions, context)
            if target_id is not None:
                is_contextual = True

            if is_contextual:
                logger.info(
                    "Synthesizing contextual image prompt for %s (target=%s, prompt=%r)...",
                    caller_name, target_name, clean_prompt,
                )
                try:
                    image_prompt, caption, synth_p, synth_c = await asyncio.to_thread(
                        synthesize_contextual_image_prompt,
                        prompt=clean_prompt,
                        context=context,
                        user_name=caller_name,
                        caller_role=caller_role,
                        target_name=target_name,
                    )
                    if synth_p or synth_c:
                        live_chat_manager.record_usage("gpt-4o", synth_p, synth_c, is_reply=True)
                except Exception as synth_err:
                    logger.warning("Contextual image synthesis failed, falling back: %s", synth_err)
                    image_prompt = extract_image_prompt(clean_prompt)
                    caption = f"<@{caller_id}> Here's your image. Try not to strain your eyes."
            else:
                image_prompt = extract_image_prompt(clean_prompt)
                caption = f"<@{caller_id}> Here's your image. Try not to strain your eyes."

            logger.info("Direct mention image generation request from %s (%s): %r (image prompt: %r)", caller_name, caller_id, clean_prompt, image_prompt)

            try:
                img_bytes, p_tokens, c_tokens = await asyncio.to_thread(
                    generate_image_openai,
                    image_prompt,
                )
                record_user_image_generation(caller_id)
                live_chat_manager.record_usage(IMAGE_GEN_MODEL, p_tokens, c_tokens, is_reply=True)

                if caller_id != USERS.OGGERS:
                    new_remaining = max(0, remaining - 1)
                    caption += f"\n-# *({new_remaining} image generation{'s' if new_remaining != 1 else ''} left today)*"

                file = discord.File(io.BytesIO(img_bytes), filename="vic_creation.png")
                await message.reply(caption, file=file, mention_author=True)

                live_chat_manager.conversation_history.append({"role": "user", "speaker": caller_name, "content": raw_content})
                live_chat_manager.conversation_history.append({"role": "assistant", "speaker": "HMS Victory", "content": f"[Generated Image: {image_prompt}]"})
                asyncio.create_task(live_chat_manager.update_dashboard())
                return True
            except Exception as img_err:
                logger.error("Failed to generate image for direct mention: %s", img_err, exc_info=True)
                fail_msg = f"<@{caller_id}> I attempted to paint that, but the canvas broke. Typical."
                await message.reply(fail_msg, mention_author=True)
                return True

        # 1. Gather context & images
        gathered = await gather_one_off_context(client, message, return_targets=True)
        if isinstance(gathered, tuple) and len(gathered) == 2:
            context, target_users = gathered
        else:
            context, target_users = str(gathered), {}
        image_urls = await extract_image_urls(message, client)

        # 2. Call OpenAI with retries
        reply_text = None
        p_tokens = 0
        c_tokens = 0
        current_images = image_urls
        max_attempts = 3

        for attempt in range(1, max_attempts + 1):
            try:
                reply_text, pt, ct = await asyncio.to_thread(
                    generate_one_off_reply,
                    prompt=clean_prompt,
                    context=context,
                    user_name=caller_name,
                    caller_role=caller_role,
                    image_urls=current_images,
                )
                p_tokens += pt
                c_tokens += ct

                if reply_text and not is_openai_refusal(reply_text):
                    break

                logger.warning(
                    "Direct mention from %s (%s) attempt %d/%d produced refusal/empty: %r",
                    caller_name, caller_id, attempt, max_attempts, reply_text
                )
                if current_images:
                    current_images = None
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.warning(
                    "Direct mention from %s (%s) attempt %d/%d encountered error: %s",
                    caller_name, caller_id, attempt, max_attempts, e
                )
                if current_images:
                    current_images = None
                await asyncio.sleep(0.5)

        if not reply_text or is_openai_refusal(reply_text):
            reply_text = "I was going to respond to that, but quite frankly, the server's incompetence has overwhelmed my processors."

        # Never let the bot ping itself
        if bot_id:
            reply_text = re.sub(rf"<@!?{bot_id}>\s*", "", reply_text).strip()

        # Tag target users if their name was used in plain text and not already tagged
        if target_users:
            for name, uid in target_users.items():
                if uid in (bot_id, getattr(message.author, "id", None)):
                    continue
                if f"<@{uid}>" not in reply_text:
                    pattern = rf"\b{re.escape(name)}\b"
                    if re.search(pattern, reply_text, flags=re.IGNORECASE):
                        reply_text = re.sub(pattern, f"<@{uid}>", reply_text, count=1, flags=re.IGNORECASE)

        # Hard-block @everyone, @here, and role mentions
        reply_text = sanitize_ai_mentions(reply_text, guild=getattr(message, "guild", None))

        # Ensure within Discord message character limits
        if len(reply_text) > 1990:
            reply_text = reply_text[:1985] + "..."

        mentions = discord.AllowedMentions(everyone=False, roles=False, users=True, replied_user=True)

        # Send reply (with fallback to channel.send if referenced message was deleted)
        try:
            await message.reply(reply_text, mention_author=True, allowed_mentions=mentions)
        except (discord.NotFound, discord.HTTPException):
            await message.channel.send(f"{message.author.mention} {reply_text}", allowed_mentions=mentions)

        # Record usage into live_chat_manager and persistent file
        live_chat_manager.record_usage("gpt-4o", p_tokens, c_tokens, is_reply=True)
        live_chat_manager.conversation_history.append({"role": "user", "speaker": caller_name, "content": raw_content})
        live_chat_manager.conversation_history.append({"role": "assistant", "speaker": "HMS Victory", "content": reply_text})

        # Trigger dashboard update
        asyncio.create_task(live_chat_manager.update_dashboard())
        return True
    except Exception as e:
        logger.error("Error handling one-off owner mention: %s", e, exc_info=True)
        try:
            fallback = "I was going to respond to that, but quite frankly, the server's incompetence has overwhelmed my processors."
            await message.channel.send(f"{message.author.mention} {fallback}")
        except Exception:
            pass
    finally:
        if typing_cm:
            try:
                await typing_cm.__aexit__(None, None, None)
            except Exception:
                pass

    return False


async def handle_chat_message(client: discord.Client, message: discord.Message) -> bool:
    """Unified handler for incoming messages: handles troll/defence targets, owner tags, and live chat sessions."""
    # Never reply to ourselves
    if client.user and message.author.id == client.user.id:
        return False
    if message.author.id == BOT_ID:
        return False

    target_hit = bool(live_chat_manager.target_user_id and message.author.id == live_chat_manager.target_user_id)

    # 1. Troll / Defence Target Hit (works even if target is another bot like Claude AI):
    if target_hit:
        return await live_chat_manager.handle_message(client, message)

    # For all other messages, ignore bots
    if getattr(message.author, "bot", False):
        return False

    # 2. If message is from authorized leadership (Oggers, Roshy, Hadidas) and explicitly @mentions the bot:
    # Always respond as a one-off anywhere on the server UNLESS paused!
    # Only a real @mention counts here. Name-drops like "I'll get vic to dm her" or replies to the
    # bot's messages must not summon it; those looser matches are only used by the live chat mode.
    if message.author.id in DIRECT_MENTION_ALLOWED_USERS and is_bot_explicitly_mentioned(client, message):
        if live_chat_manager.owner_mentions_paused:
            logger.info("Direct mention from %s (%s) ignored because direct mentions are paused.", message.author, message.author.id)
            return False
        return await handle_one_off_owner_mention(client, message)

    # 3. Standard live chat responder if currently active
    if live_chat_manager.active:
        return await live_chat_manager.handle_message(client, message)

    return False


class ChatbotWakeModal(discord.ui.Modal, title="Wake Up HMS Victory"):
    channel_input = discord.ui.TextInput(
        label="Target Channel (or select on card)",
        placeholder="Leave blank to use channel selected on card",
        default="general",
        max_length=60,
        required=False,
    )
    duration_input = discord.ui.TextInput(
        label="Auto-Stop Timer (Optional)",
        placeholder="e.g. 15m, 30m, 1h, 2h (leave blank for unlimited)",
        max_length=20,
        required=False,
    )
    target_user_input = discord.ui.TextInput(
        label="Troll/Defence Target ID (Optional)",
        placeholder="User ID or @mention (Vic will target & roast every message they send)",
        max_length=60,
        required=False,
    )
    topic_input = discord.ui.TextInput(
        label="Starting Context / Hint (Optional)",
        placeholder="e.g. Chin pub quiz, a Discord message link, or leave blank for natural chat",
        style=discord.TextStyle.paragraph,
        max_length=400,
        required=False,
    )

    def __init__(self, default_channel: Optional[str] = None, default_target_user: Optional[str] = None):
        super().__init__()
        if default_channel:
            self.channel_input.default = default_channel
        if default_target_user is not None:
            self.target_user_input.default = default_target_user

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != USERS.OGGERS:
            await interaction.response.send_message("Only Oggers can control this.", ephemeral=True)
            return

        # Defer immediately to allow time for scraping & AI context formulation
        await interaction.response.defer(ephemeral=True)

        channel_val = self.channel_input.value.strip() if self.channel_input.value else ""
        if channel_val:
            cid, cname = resolve_channel_input(channel_val)
        elif live_chat_manager.target_channel_id:
            cid, cname = live_chat_manager.target_channel_id, live_chat_manager.target_channel_name
        else:
            cid, cname = resolve_channel_input("general")

        dur = parse_duration_str(self.duration_input.value)
        target_uid = parse_user_id(self.target_user_input.value)
        raw_topic = self.topic_input.value.strip() if self.topic_input.value else ""

        final_topic = None
        f_prompt_tokens = 0
        f_comp_tokens = 0
        if raw_topic:
            final_topic = raw_topic
            try:
                scraped_data = await scrape_server_context(
                    client=interaction.client,
                    guild=interaction.guild,
                    target_channel_id=cid,
                    user_input=raw_topic,
                )
                if scraped_data:
                    formulated, f_prompt_tokens, f_comp_tokens = await asyncio.to_thread(
                        formulate_starting_context,
                        user_input=raw_topic,
                        scraped_data=scraped_data,
                        return_usage=True,
                    )
                    if formulated:
                        final_topic = formulated
            except Exception as e:
                logger.warning("Failed to scrape/formulate starting topic: %s", e, exc_info=True)

        if interaction.message:
            live_chat_manager.set_dashboard(interaction.client, interaction.message)

        live_chat_manager.start(
            channel_id=cid,
            channel_name=cname,
            duration_seconds=dur,
            topic=final_topic,
            target_user_id=target_uid,
            client=interaction.client,
        )

        if f_prompt_tokens or f_comp_tokens:
            live_chat_manager.record_usage("gpt-4o-mini", f_prompt_tokens, f_comp_tokens, is_reply=False)

        view = ChatbotDashboardView()
        try:
            if interaction.message:
                await interaction.message.edit(content=None, embed=None, view=view)
        except Exception:
            pass

        msg = f"✅ **HMS Victory is awake in <#{cid}>!**"
        if target_uid:
            msg += f"\n🛡️ **Troll/Defence target:** <@{target_uid}> (Roasting every message)"
        if final_topic:
            msg += f"\n**Formulated Starting Context:**\n> {final_topic}"
        await interaction.followup.send(msg, ephemeral=True)


class ChatbotTargetModal(discord.ui.Modal, title="Set Troll/Defence Target"):
    user_input = discord.ui.TextInput(
        label="Target User ID or @mention",
        placeholder="e.g. 123456789012345678 (leave blank or 'clear' to disable)",
        max_length=60,
        required=False,
    )

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != USERS.OGGERS:
            await interaction.response.send_message("Only Oggers can control this.", ephemeral=True)
            return

        raw = (self.user_input.value or "").strip().lower()
        if not raw or raw in ("clear", "none", "off", "0", "reset", "disable"):
            live_chat_manager.set_target_user(None)
            msg = "🛡️ **Defence/Troll mode disabled.** Vic is back to standard replies."
        else:
            uid = parse_user_id(raw)
            if not uid:
                await interaction.response.send_message("❌ Invalid user ID or mention format.", ephemeral=True)
                return
            live_chat_manager.set_target_user(uid)
            ch_info = f"in <#{live_chat_manager.target_channel_id}>" if live_chat_manager.target_channel_id else "across all server channels"
            msg = f"🎯 **Troll/Defence mode ACTIVE on <@{uid}>!** Vic will now retaliate to every message they send {ch_info}."

        if interaction.message:
            live_chat_manager.set_dashboard(interaction.client, interaction.message)
        await live_chat_manager.update_dashboard()
        await interaction.response.send_message(msg, ephemeral=True)


class ChatbotChannelSelect(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(
            channel_types=[discord.ChannelType.text],
            placeholder="🔍 Choose target channel (searchable)...",
            custom_id="vic_live_channel_select",
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != USERS.OGGERS:
            await interaction.response.send_message("⛔ Only Oggers can control HMS Victory.", ephemeral=True)
            return

        if not self.values:
            await interaction.response.send_message("No channel selected.", ephemeral=True)
            return

        selected_channel = self.values[0]
        cid = getattr(selected_channel, "id", None) or int(str(selected_channel))
        cname = getattr(selected_channel, "name", f"Channel {cid}")

        live_chat_manager.target_channel_id = cid
        live_chat_manager.target_channel_name = cname

        if interaction.message:
            live_chat_manager.set_dashboard(interaction.client, interaction.message)

        view = ChatbotDashboardView()
        await interaction.response.edit_message(content=None, embed=None, view=view)

        if live_chat_manager.active:
            await interaction.followup.send(f"🎯 Switched live chat target to <#{cid}>!", ephemeral=True)
        else:
            await interaction.followup.send(f"🎯 Target channel set to <#{cid}>. Click **Wake Up Vic** to launch!", ephemeral=True)


class ChatbotWakeButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Wake Up Vic",
            style=discord.ButtonStyle.success,
            emoji="🟢",
            custom_id="vic_live_wake_up",
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != USERS.OGGERS:
            await interaction.response.send_message("⛔ Only Oggers can control HMS Victory.", ephemeral=True)
            return
        default_channel = "general"
        if live_chat_manager.target_channel_id:
            default_channel = live_chat_manager.target_channel_name or str(live_chat_manager.target_channel_id)
        default_target = str(live_chat_manager.target_user_id) if live_chat_manager.target_user_id else ""
        await interaction.response.send_modal(ChatbotWakeModal(default_channel=default_channel, default_target_user=default_target))


class ChatbotSleepButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Put to Sleep",
            style=discord.ButtonStyle.danger,
            emoji="🔴",
            custom_id="vic_live_sleep",
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != USERS.OGGERS:
            await interaction.response.send_message("⛔ Only Oggers can control HMS Victory.", ephemeral=True)
            return
        if interaction.message:
            live_chat_manager.set_dashboard(interaction.client, interaction.message)
        live_chat_manager.stop(clear_target=True)
        view = ChatbotDashboardView()
        await interaction.response.edit_message(content=None, embed=None, view=view)


class ChatbotRefreshButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Refresh",
            style=discord.ButtonStyle.secondary,
            emoji="🔄",
            custom_id="vic_live_refresh",
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != USERS.OGGERS:
            await interaction.response.send_message("⛔ Only Oggers can control HMS Victory.", ephemeral=True)
            return
        if interaction.message:
            live_chat_manager.set_dashboard(interaction.client, interaction.message)
        view = ChatbotDashboardView()
        await interaction.response.edit_message(content=None, embed=None, view=view)


class ChatbotTargetButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="🎯 Target",
            style=discord.ButtonStyle.secondary,
            custom_id="vic_live_set_target",
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != USERS.OGGERS:
            await interaction.response.send_message("⛔ Only Oggers can control HMS Victory.", ephemeral=True)
            return
        await interaction.response.send_modal(ChatbotTargetModal())


class ChatbotDirectPauseButton(discord.ui.Button):
    def __init__(self):
        is_paused = live_chat_manager.owner_mentions_paused
        label = "Resume Direct" if is_paused else "Pause Direct"
        emoji = "▶️" if is_paused else "⏸️"
        style = discord.ButtonStyle.success if is_paused else discord.ButtonStyle.secondary
        super().__init__(
            label=label,
            style=style,
            emoji=emoji,
            custom_id="vic_live_toggle_direct",
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != USERS.OGGERS:
            await interaction.response.send_message("⛔ Only Oggers can control HMS Victory.", ephemeral=True)
            return
        if interaction.message:
            live_chat_manager.set_dashboard(interaction.client, interaction.message)
        new_paused = not live_chat_manager.owner_mentions_paused
        live_chat_manager.set_owner_mentions_paused(new_paused)
        view = ChatbotDashboardView()
        await interaction.response.edit_message(content=None, embed=None, view=view)


class ChatbotDashboardView(discord.ui.LayoutView):
    """Components V2 persistent dashboard controller view."""

    def __init__(self):
        super().__init__(timeout=None)
        self.build_ui()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != USERS.OGGERS:
            await interaction.response.send_message("⛔ Only Oggers can control HMS Victory.", ephemeral=True)
            return False
        return True

    def build_ui(self):
        accent = 0x2ECC71 if live_chat_manager.active else (0xE67E22 if live_chat_manager.target_user_id else 0xE74C3C)
        card = discord.ui.Container(accent_colour=accent)

        # Header
        card.add_item(
            discord.ui.TextDisplay(
                "## 🤖 HMS Victory — Live Chatbot Controller\n"
                "Real-time control centre for Vic's live conversational responder across server channels."
            )
        )
        card.add_item(discord.ui.Separator(visible=True))

        # Status & Target Channel
        if live_chat_manager.active:
            status_line = "🟢 **Status:** **Active & Responding**"
            ch_str = f"<#{live_chat_manager.target_channel_id}> (`{live_chat_manager.target_channel_name}`)"
            if live_chat_manager.end_time:
                remaining = max(0, int(live_chat_manager.end_time - time.time()))
                mins = remaining // 60
                secs = remaining % 60
                timer_str = f"<t:{int(live_chat_manager.end_time)}:t> ({mins}m {secs:02d}s remaining)"
            else:
                timer_str = "Unlimited *(manual stop)*"
            sub_line = "-# ⚡ Live updating every 5s"
        elif live_chat_manager.target_user_id:
            status_line = "🎯 **Status:** **Troll Hunter ACTIVE**"
            if live_chat_manager.target_channel_id:
                ch_str = f"<#{live_chat_manager.target_channel_id}> (`{live_chat_manager.target_channel_name}`)"
            else:
                ch_str = "All Server Channels *(Everywhere target speaks)*"
            timer_str = "None *(Continuous until cleared)*"
            sub_line = f"-# 🚨 Actively hunting & roasting <@{live_chat_manager.target_user_id}>"
        else:
            status_line = "🔴 **Status:** **Offline / Asleep**"
            if live_chat_manager.target_channel_id:
                ch_str = f"<#{live_chat_manager.target_channel_id}> (`{live_chat_manager.target_channel_name}`)"
            else:
                ch_str = "*None (Select from dropdown below)*"
            timer_str = "None"
            sub_line = "-# 💤 Responder is currently sleeping"

        direct_str = "⏸️ **Paused** *(ignoring direct tags)*" if live_chat_manager.owner_mentions_paused else "✅ **Active** *(responding to direct tags)*"

        card.add_item(
            discord.ui.TextDisplay(
                f"{status_line}\n"
                f"📍 **Target Channel:** {ch_str}\n"
                f"⏱️ **Auto-Stop Timer:** {timer_str}\n"
                f"🏷️ **Direct Mentions:** {direct_str}\n"
                f"{sub_line}"
            )
        )
        card.add_item(discord.ui.Separator(visible=True))

        # Metrics
        cost_title = "Live Session Cost" if live_chat_manager.active else "Last Session Cost"
        metrics_text = (
            "### 📊 Usage & Cost Metrics\n"
            f"- **💰 {cost_title}:** **`${live_chat_manager.session_cost_usd:.4f}`** · `{live_chat_manager.total_tokens:,}` tokens · `{live_chat_manager.session_replies_count}` replies\n"
            f"- **📈 Total Cost (All-Time):** **`${live_chat_manager.all_time_cost_usd:.4f}`** · `{live_chat_manager.all_time_tokens:,}` tokens · `{live_chat_manager.all_time_replies_count}` replies"
        )
        card.add_item(discord.ui.TextDisplay(metrics_text))
        card.add_item(discord.ui.Separator(visible=True))

        # Troll/Defence Target & Context
        if live_chat_manager.target_user_id:
            target_str = f"🎯 <@{live_chat_manager.target_user_id}> (`{live_chat_manager.target_user_id}`)\n> 🚨 **Defence Mode ACTIVE:** Retaliating and roasting every message they send."
        else:
            target_str = "None *(Standard Mode — replies only when mentioned or called)*"

        topic_str = f"_{live_chat_manager.topic}_" if live_chat_manager.topic else "None *(Natural conversation / auto-scraped)*"

        config_text = (
            f"🛡️ **Troll / Defence Target:** {target_str}\n\n"
            f"💬 **Starting Context / Hint:** {topic_str}"
        )
        card.add_item(discord.ui.TextDisplay(config_text))
        card.add_item(discord.ui.Separator(visible=True))

        # Channel Select dropdown
        card.add_item(discord.ui.ActionRow(ChatbotChannelSelect()))

        # Action buttons
        card.add_item(
            discord.ui.ActionRow(
                ChatbotWakeButton(),
                ChatbotSleepButton(),
                ChatbotDirectPauseButton(),
                ChatbotTargetButton(),
                ChatbotRefreshButton(),
            )
        )

        # Footer
        card.add_item(discord.ui.TextDisplay("-# HMS Victory • Persistent Controller • Oggers Only • Components V2"))

        self.add_item(card)


async def ensure_chatbot_dashboard_message(client: discord.Client):
    """Ensure the persistent dashboard CV2 layout is posted in the dedicated thread and kept updated."""
    thread_id = getattr(CHANNELS, "CHATBOT_CONTROLLER_THREAD", 1547254995320184833)
    try:
        thread = client.get_channel(thread_id) or await client.fetch_channel(thread_id)
        if not thread:
            logger.warning("Could not find chatbot controller thread %s", thread_id)
            return

        view = ChatbotDashboardView()

        dashboard_msg = None
        async for m in thread.history(limit=25):
            if m.author.id == client.user.id:
                # Detect old embed message or new components v2 card
                if (m.embeds and "HMS Victory Chatbot Dashboard" in (m.embeds[0].title or "")) or (
                    m.components and any(getattr(c, "type", None) and getattr(c.type, "value", None) in (17, 10, 1) for c in m.components)
                ):
                    dashboard_msg = m
                    break

        if dashboard_msg:
            try:
                await dashboard_msg.edit(content=None, embed=None, view=view)
                logger.info("Updated existing chatbot dashboard message (%s) to Components V2 in thread %s", dashboard_msg.id, thread_id)
            except discord.HTTPException as e:
                logger.warning("Failed to edit existing dashboard message to Components V2 (%s): %s. Recreating...", dashboard_msg.id, e)
                try:
                    await dashboard_msg.delete()
                except Exception:
                    pass
                dashboard_msg = await thread.send(view=view)
                logger.info("Recreated chatbot dashboard message with Components V2 (%s) in thread %s", dashboard_msg.id, thread_id)
        else:
            dashboard_msg = await thread.send(view=view)
            logger.info("Posted initial chatbot dashboard message (%s) with Components V2 in thread %s", dashboard_msg.id, thread_id)

        live_chat_manager.set_dashboard(client, dashboard_msg)
        live_chat_manager.start_config_watcher()

    except Exception as e:
        logger.error("Failed to ensure chatbot dashboard message in thread %s: %s", thread_id, e, exc_info=True)
