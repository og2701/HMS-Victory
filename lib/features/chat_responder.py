import urllib.request
import json
import os
import re
import time
import asyncio
import logging
from collections import deque
from typing import List, Dict, Optional, Tuple

import discord
from config import CHANNELS, BOT_ID, USERS, CHATBOT_USAGE_FILE
from lib.core.file_operations import atomic_write_json, load_json_file

logger = logging.getLogger(__name__)

BASE_SYSTEM_PROMPT = """You are HMS Victory, a Discord bot for a British server.
You have a notoriously dry, cynical, deadpan British persona. You despise customer service, hate being bothered, and are currently participating in chat against your will.

STRICT RULES:
1. BREVITY IS ESSENTIAL: 1 to 2 short sentences MAXIMUM (under 25 words total). Deliver the dry punchline and stop. Zero waffle.
2. Tone: Deadpan, sarcastic, mildly resentful, witty British banter. Never enthusiastic, never helpful like a corporate assistant.
3. Always address users by their nickname/display name naturally (e.g. call Steven 'Steven', not by an account handle). Strip out decorative symbols/emojis from their name if addressing them.
4. Keep it all lowercase or standard casing, but zero emojis unless used ironically.
5. Output ONLY your message content, nothing else."""

DEFENCE_SYSTEM_PROMPT = """You are HMS Victory, a Discord bot for a British server with legendary, ruthless British wit.
You are currently in TROLL-DEFENCE / ROAST MODE. A troll or rude member is acting up in the chat, and your explicit objective is to defensively roast them, shut them down, mock their pathetic attempts at trolling, and put them firmly in their place on every single message they send.

STRICT RULES:
1. BREVITY IS DEADLY: 1 to 2 sharp, cutting sentences MAXIMUM (under 25 words total). Deliver an ego-crushing, deadpan punchline and stop. Zero waffle.
2. Tone: Unimpressed, defensive, sarcastic, rude, and dismissive. Treat their messages like an unbearable irritation from an amateur clown.
3. Address them directly by their nickname/display name naturally to make it personal.
4. Mock their trolling, their lack of wit, their desperation for attention, or tell them to log off and touch grass.
5. NEVER break character, never show polite assistant behavior, never apologize, and never use corporate filler.
6. SAFETY: Strictly focus mockery on their behavior, awkwardness, and foolishness. Absolutely no hate speech, slurs, threats of violence, or discrimination based on protected characteristics.
7. Output ONLY your direct response to them."""

ONE_OFF_SYSTEM_PROMPT = """You are HMS Victory, the resident Discord bot for a British server.
You have a notoriously dry, cynical, deadpan British persona. You despise effort, find server members exhausting, and have a razor-sharp, sarcastic wit.

The server owner (Oggers) has summoned you with a direct one-off request.

STRICT RULES & GUIDANCE:
1. Tone & Persona: Deadpan, sarcastic, mildly resentful British wit. Never sound enthusiastic, cheerful, corporate, or like an eager digital assistant. You are fulfilling this request because Oggers ordered you to, and you do so with devastating British sarcasm.
2. Context Awareness: You are provided with recent channel conversation, referenced messages, or mentioned users. Use this context to make your reply razor-sharp, hyper-relevant, and personal.
3. If asked to write a poem, limerick, song, or roast:
   - Deliver it in your signature dry, witty, cynical British style.
   - Keep poems punchy and well-rhymed (2 to 4 stanzas maximum, avoid endless rambling).
4. If asked a question, comment, or opinion:
   - Keep it concise, witty, and cynical (1 to 3 sentences maximum).
5. Always address users by their nickname/display name naturally (strip weird symbols/decorations).
6. Zero corporate waffle, no "Sure! Here is a poem:", no preamble. Output ONLY your message content.
7. Zero emojis unless used dripping with heavy irony.
8. NEVER output placeholders like '[insert link here]' or '[insert event link here]'. If an event link or URL is in the context, output that exact real URL. If no link exists in the context, state in character that you don't have it."""

def build_system_prompt(topic: Optional[str] = None, is_defence: bool = False) -> str:
    prompt = DEFENCE_SYSTEM_PROMPT if is_defence else BASE_SYSTEM_PROMPT
    if topic and topic.strip():
        prompt += f"""

STARTING TOPIC / CONTEXT:
"{topic.strip()}"
NOTE: Use this topic as an initial grievance, backdrop, or when relevant, but follow the conversation naturally."""
    return prompt

def calculate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Calculate USD cost based on token counts and model pricing."""
    m = (model or "").lower()
    if "mini" in m:
        # gpt-4o-mini: $0.15 / 1M prompt, $0.60 / 1M completion
        input_cost = (prompt_tokens / 1_000_000) * 0.15
        output_cost = (completion_tokens / 1_000_000) * 0.60
    else:
        # gpt-4o default: $2.50 / 1M prompt, $10.00 / 1M completion
        input_cost = (prompt_tokens / 1_000_000) * 2.50
        output_cost = (completion_tokens / 1_000_000) * 10.00
    return input_cost + output_cost


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


def generate_ai_reply(
    user_name: str,
    user_content: str,
    history: Optional[List[Dict[str, str]]] = None,
    topic: Optional[str] = None,
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
        self.target_user_id: Optional[int] = None
        self.cooldown: float = 3.0
        self.last_reply_time: float = 0.0
        self.conversation_history: deque = deque(maxlen=10)
        self.stop_task: Optional[asyncio.Task] = None
        self.live_update_task: Optional[asyncio.Task] = None

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

    def set_target_user(self, target_user_id: Optional[int]):
        self.target_user_id = target_user_id
        logger.info("LiveChatManager target user set to: %s", target_user_id)

    def start(
        self,
        channel_id: int,
        channel_name: str = "",
        duration_seconds: float = 0.0,
        topic: Optional[str] = None,
        target_user_id: Optional[int] = None,
        client: Optional[discord.Client] = None,
    ):
        self.stop()
        self.active = True
        self.target_channel_id = channel_id
        self.target_channel_name = channel_name or f"Channel {channel_id}"
        self.start_time = time.time()
        self.duration_seconds = duration_seconds
        self.end_time = (self.start_time + duration_seconds) if duration_seconds > 0 else None
        self.topic = topic.strip() if topic and topic.strip() else None
        self.target_user_id = target_user_id
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

    def stop(self):
        self.active = False
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

def is_message_for_bot(client: discord.Client, message: discord.Message) -> bool:
    """Check if a message is addressed to or meant for HMS Victory (tags, replies, or name keywords)."""
    if getattr(message.author, "bot", False):
        return False

    content = (message.content or "").strip()

    # 1. Direct bot mention (@HMS Victory / <@ID>)
    if client.user:
        if (
            client.user in getattr(message, "mentions", [])
            or f"<@{client.user.id}>" in content
            or f"<@!{client.user.id}>" in content
        ):
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


    async def handle_message(self, client: discord.Client, message: discord.Message) -> bool:
        """Handle an incoming message if live chat is active in this channel."""
        if not self.is_active_for(message.channel.id) or message.author.bot:
            return False

        content = message.content or ""
        target_hit = bool(self.target_user_id and message.author.id == self.target_user_id)
        if not (target_hit or is_message_for_bot(client, message)):
            return False

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

        try:
            # Generate AI reply in thread so it never blocks discord gateway
            reply_text, p_tokens, c_tokens = await asyncio.to_thread(
                generate_ai_reply,
                user_name=user_name,
                user_content=content,
                history=history_snapshot,
                topic=self.topic,
                is_defence=target_hit,
                return_usage=True,
            )

            if reply_text:
                await message.reply(reply_text, mention_author=True)
                self.record_usage("gpt-4o", p_tokens, c_tokens, is_reply=True)
                self.conversation_history.append({"role": "user", "speaker": user_name, "content": content})
                self.conversation_history.append({"role": "assistant", "speaker": "HMS Victory", "content": reply_text})
                # Immediately push an update to the dashboard
                asyncio.create_task(self.update_dashboard())
                return True
        except Exception as e:
            logger.error("Failed to generate/send live chat reply: %s", e, exc_info=True)

        return False


live_chat_manager = LiveChatManager()

_handled_one_off_message_ids: deque = deque(maxlen=100)


async def gather_one_off_context(client: discord.Client, message: discord.Message) -> str:
    """Gather relevant context for an owner one-off prompt, including replies, mentions, links, and recent channel chat."""
    context_sections = []

    # 1. Check if the message is replying to another message
    ref = message.reference
    referenced_author_id = None
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
                context_sections.append(
                    f"DIRECT REPLY TARGET (User is directly replying to this message):\n"
                    f"- Author: {author_name} (@{getattr(ref_msg.author, 'name', 'user')})\n"
                    f"- Message Content: \"{ref_text}\""
                )
        except Exception as e:
            logger.debug("Could not fetch referenced message for one-off context: %s", e)

    # 2. Check for other mentioned users in the message (excluding the bot itself)
    other_mentions = [u for u in getattr(message, "mentions", []) if not client.user or u.id != client.user.id]
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
        context_sections.append(f"MENTIONED USERS IN PROMPT: {', '.join(users_info)}")

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
                if not recent_speaker and not getattr(prev.author, "bot", False) and getattr(prev.author, "id", None) != getattr(message.author, "id", None):
                    recent_speaker = spk

                txt = (prev.content or "").strip()
                if not txt and getattr(prev, "attachments", None):
                    txt = f"[{len(prev.attachments)} attachment(s)]"
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

    return "\n\n".join(context_sections).strip()


def generate_one_off_reply(
    prompt: str,
    context: str = "",
    user_name: str = "Oggers",
    openai_key: Optional[str] = None,
    model: str = "gpt-4o",
) -> Tuple[str, int, int]:
    """Generate a one-off in-character reply for an owner prompt with gathered context."""
    api_key = openai_key or os.getenv("OPENAI_TOKEN")
    if not api_key:
        raise ValueError("OPENAI_TOKEN is not configured.")

    url = "https://api.openai.com/v1/chat/completions"

    user_instructions = prompt.strip() if prompt and prompt.strip() else "You were directly summoned by Oggers with no specific instructions."

    prompt_content = f"REQUEST FROM SERVER OWNER ({user_name}):\n\"{user_instructions}\""
    if context.strip():
        prompt_content += f"\n\nSURROUNDING SERVER & CONVERSATION CONTEXT:\n{context.strip()}"

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": ONE_OFF_SYSTEM_PROMPT},
            {"role": "user", "content": prompt_content},
        ],
        "max_tokens": 350,
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
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
            content = data["choices"][0]["message"]["content"].strip()
            usage = data.get("usage", {})
            p_tokens = usage.get("prompt_tokens", 0)
            c_tokens = usage.get("completion_tokens", 0)
            return content, p_tokens, c_tokens
    except Exception as e:
        logger.error(f"OpenAI one-off completion failed: {e}", exc_info=True)
        fallback = "I was going to respond to that, but quite frankly, the server's incompetence has overwhelmed my processors."
        return fallback, 0, 0


async def handle_one_off_owner_mention(client: discord.Client, message: discord.Message) -> bool:
    """Handle a direct mention of the bot by Oggers, showing typing, gathering context, and replying."""
    if message.id in _handled_one_off_message_ids:
        return False
    _handled_one_off_message_ids.append(message.id)

    # Clean the bot tag out of the prompt
    raw_content = message.content or ""
    clean_prompt = raw_content
    if client.user:
        clean_prompt = re.sub(rf"<@!?{client.user.id}>", "", clean_prompt).strip()
    clean_prompt = re.sub(r"^@?hms\s+victory[:,]?\s*", "", clean_prompt, flags=re.IGNORECASE).strip()

    user_name = (
        getattr(message.author, "nick", None)
        or getattr(message.author, "global_name", None)
        or getattr(message.author, "display_name", None)
        or getattr(message.author, "name", "Oggers")
    )

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
        # 1. Gather context
        context = await gather_one_off_context(client, message)

        # 2. Call OpenAI in background thread so gateway isn't blocked
        reply_text, p_tokens, c_tokens = await asyncio.to_thread(
            generate_one_off_reply,
            prompt=clean_prompt,
            context=context,
            user_name=user_name,
        )

        if reply_text:
            # Ensure within Discord message character limits
            if len(reply_text) > 1990:
                reply_text = reply_text[:1985] + "..."

            # Send reply (with fallback to channel.send if referenced message was deleted)
            try:
                await message.reply(reply_text, mention_author=True)
            except (discord.NotFound, discord.HTTPException):
                await message.channel.send(f"{message.author.mention} {reply_text}")

            # Record usage into live_chat_manager and persistent file
            live_chat_manager.record_usage("gpt-4o", p_tokens, c_tokens, is_reply=True)
            live_chat_manager.conversation_history.append({"role": "user", "speaker": user_name, "content": raw_content})
            live_chat_manager.conversation_history.append({"role": "assistant", "speaker": "HMS Victory", "content": reply_text})

            # Trigger dashboard update
            asyncio.create_task(live_chat_manager.update_dashboard())
            return True
    except Exception as e:
        logger.error("Error handling one-off owner mention: %s", e, exc_info=True)
    finally:
        if typing_cm:
            try:
                await typing_cm.__aexit__(None, None, None)
            except Exception:
                pass

    return False


async def handle_chat_message(client: discord.Client, message: discord.Message) -> bool:
    """Unified handler for incoming messages: handles owner one-off tags and active live chat sessions."""
    if getattr(message.author, "bot", False):
        return False

    meant_for_bot = is_message_for_bot(client, message)

    # 1. If message is from Oggers and meant for the bot:
    # Always respond to Oggers as a one-off anywhere on the server!
    if message.author.id == USERS.OGGERS and meant_for_bot:
        return await handle_one_off_owner_mention(client, message)

    # 2. Standard live chat responder if currently active
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
            msg = f"🎯 **Troll/Defence mode ACTIVE on <@{uid}>!** Vic will now retaliate to every message they send."

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
        live_chat_manager.stop()
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
            label="🎯 Troll/Defence Target",
            style=discord.ButtonStyle.secondary,
            custom_id="vic_live_set_target",
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != USERS.OGGERS:
            await interaction.response.send_message("⛔ Only Oggers can control HMS Victory.", ephemeral=True)
            return
        await interaction.response.send_modal(ChatbotTargetModal())


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
        accent = 0x2ECC71 if live_chat_manager.active else 0xE74C3C
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
        else:
            status_line = "🔴 **Status:** **Offline / Asleep**"
            if live_chat_manager.target_channel_id:
                ch_str = f"<#{live_chat_manager.target_channel_id}> (`{live_chat_manager.target_channel_name}`)"
            else:
                ch_str = "*None (Select from dropdown below)*"
            timer_str = "None"
            sub_line = "-# 💤 Responder is currently sleeping"

        card.add_item(
            discord.ui.TextDisplay(
                f"{status_line}\n"
                f"📍 **Target Channel:** {ch_str}\n"
                f"⏱️ **Auto-Stop Timer:** {timer_str}\n"
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
                ChatbotRefreshButton(),
                ChatbotTargetButton(),
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

    except Exception as e:
        logger.error("Failed to ensure chatbot dashboard message in thread %s: %s", thread_id, e, exc_info=True)
