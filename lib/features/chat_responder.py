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
                    desc = (e.description or "").strip().replace("\n", " ")
                    scraped_lines.append(
                        f"Event '{e.name}' (Host: {creator_name}, Time: {start_str}, Channel: #{channel_name}): {desc[:200]}"
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
        return CHANNELS.GENERAL, "General Chat"
    ch = val.strip().lower()
    ch_clean = re.sub(r"[<#>]", "", ch).strip()
    if ch in ("general", "gen") or ch_clean in ("general", "gen"):
        return CHANNELS.GENERAL, "General Chat"
    if ch in ("vip", "vip-lounge", "viplounge") or ch_clean in ("vip", "vip-lounge", "viplounge"):
        return CHANNELS.VIP_LOUNGE, "VIP Lounge"
    if ch in ("commons", "house-of-commons") or ch_clean in ("commons", "house-of-commons"):
        return CHANNELS.COMMONS, "House of Commons"
    if ch in ("politics", "pol") or ch_clean in ("politics", "pol"):
        return CHANNELS.POLITICS, "Politics"
    if ch in ("bot-spam", "botspam") or ch_clean in ("bot-spam", "botspam"):
        return CHANNELS.BOT_SPAM, "Bot Spam"
    try:
        cid = int(ch_clean)
        if live_chat_manager.target_channel_id == cid and live_chat_manager.target_channel_name:
            return cid, live_chat_manager.target_channel_name
        return cid, f"Channel {cid}"
    except ValueError:
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
        """Push latest status, countdown, and live cost to the Discord dashboard message."""
        if not self.client:
            return
        try:
            embed = self.get_status_embed()
            view = ChatbotDashboardView()
            if self.dashboard_message:
                try:
                    await self.dashboard_message.edit(embed=embed, view=view)
                    return
                except discord.NotFound:
                    self.dashboard_message = None
                except discord.HTTPException as e:
                    logger.debug("HTTP exception updating cached dashboard message: %s", e)

            if self.dashboard_channel_id and self.dashboard_message_id:
                ch = self.client.get_channel(self.dashboard_channel_id) or await self.client.fetch_channel(self.dashboard_channel_id)
                if ch:
                    self.dashboard_message = await ch.fetch_message(self.dashboard_message_id)
                    await self.dashboard_message.edit(embed=embed, view=view)
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
        """Handle an incoming message if live chat is active in this channel."""
        if not self.is_active_for(message.channel.id) or message.author.bot:
            return False

        content = message.content or ""
        ref = message.reference
        is_reply_to_bot = False

        if ref and ref.message_id:
            try:
                ref_msg = ref.cached_message or await message.channel.fetch_message(ref.message_id)
                if ref_msg and ref_msg.author.id == client.user.id:
                    is_reply_to_bot = True
            except Exception:
                pass

        is_mentioned = client.user in message.mentions if client.user else False
        name_called = (
            content.lower().strip().startswith("vic ")
            or content.lower().strip() == "vic"
            or "hms victory" in content.lower()
        )

        target_hit = False
        if self.target_user_id and message.author.id == self.target_user_id:
            target_hit = True

        if not (target_hit or is_reply_to_bot or is_mentioned or name_called):
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


class ChatbotWakeModal(discord.ui.Modal, title="Wake Up HMS Victory"):
    channel_input = discord.ui.TextInput(
        label="Target Channel",
        placeholder="general, vip, commons, politics, or channel ID",
        default="general",
        max_length=60,
        required=True,
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
        placeholder="e.g. Chin pub quiz, a Discord message link, or leave blank to auto-scrape",
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

        cid, cname = resolve_channel_input(self.channel_input.value)
        dur = parse_duration_str(self.duration_input.value)
        target_uid = parse_user_id(self.target_user_input.value)
        raw_topic = self.topic_input.value.strip() if self.topic_input.value else ""

        final_topic = raw_topic
        f_prompt_tokens = 0
        f_comp_tokens = 0
        try:
            scraped_data = await scrape_server_context(
                client=interaction.client,
                guild=interaction.guild,
                target_channel_id=cid,
                user_input=raw_topic,
            )
            if scraped_data or raw_topic:
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

        embed = live_chat_manager.get_status_embed()
        view = ChatbotDashboardView()
        try:
            if interaction.message:
                await interaction.message.edit(embed=embed, view=view)
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


class ChatbotDashboardView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != USERS.OGGERS:
            await interaction.response.send_message("⛔ Only Oggers can control HMS Victory.", ephemeral=True)
            return False
        return True

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        placeholder="🔍 Choose target channel (searchable)...",
        custom_id="vic_live_channel_select",
        row=0,
    )
    async def select_channel(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        if not select.values:
            await interaction.response.send_message("No channel selected.", ephemeral=True)
            return

        selected_channel = select.values[0]
        cid = getattr(selected_channel, "id", None) or int(str(selected_channel))
        cname = getattr(selected_channel, "name", f"Channel {cid}")

        live_chat_manager.target_channel_id = cid
        live_chat_manager.target_channel_name = cname

        if interaction.message:
            live_chat_manager.set_dashboard(interaction.client, interaction.message)

        embed = live_chat_manager.get_status_embed()
        await interaction.response.edit_message(embed=embed, view=self)

        if live_chat_manager.active:
            await interaction.followup.send(f"🎯 Switched live chat target to <#{cid}>!", ephemeral=True)
        else:
            await interaction.followup.send(f"🎯 Target channel set to <#{cid}>. Click **Wake Up Vic** to launch!", ephemeral=True)

    @discord.ui.button(label="Wake Up Vic", style=discord.ButtonStyle.success, emoji="🟢", custom_id="vic_live_wake_up", row=1)
    async def wake_up_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        default_channel = "general"
        if live_chat_manager.target_channel_id:
            default_channel = live_chat_manager.target_channel_name or str(live_chat_manager.target_channel_id)
        default_target = str(live_chat_manager.target_user_id) if live_chat_manager.target_user_id else ""
        await interaction.response.send_modal(ChatbotWakeModal(default_channel=default_channel, default_target_user=default_target))

    @discord.ui.button(label="Put to Sleep", style=discord.ButtonStyle.danger, emoji="🔴", custom_id="vic_live_sleep", row=1)
    async def sleep_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.message:
            live_chat_manager.set_dashboard(interaction.client, interaction.message)
        live_chat_manager.stop()
        embed = live_chat_manager.get_status_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, emoji="🔄", custom_id="vic_live_refresh", row=1)
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.message:
            live_chat_manager.set_dashboard(interaction.client, interaction.message)
        embed = live_chat_manager.get_status_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="🎯 Troll/Defence Target", style=discord.ButtonStyle.secondary, custom_id="vic_live_set_target", row=1)
    async def set_target_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ChatbotTargetModal())


async def ensure_chatbot_dashboard_message(client: discord.Client):
    """Ensure the persistent dashboard embed is posted in the dedicated thread and kept updated."""
    thread_id = getattr(CHANNELS, "CHATBOT_CONTROLLER_THREAD", 1547254995320184833)
    try:
        thread = client.get_channel(thread_id) or await client.fetch_channel(thread_id)
        if not thread:
            logger.warning("Could not find chatbot controller thread %s", thread_id)
            return

        embed = live_chat_manager.get_status_embed()
        view = ChatbotDashboardView()

        dashboard_msg = None
        async for m in thread.history(limit=25):
            if m.author.id == client.user.id and m.embeds and "HMS Victory Chatbot Dashboard" in (m.embeds[0].title or ""):
                dashboard_msg = m
                break

        if dashboard_msg:
            await dashboard_msg.edit(embed=embed, view=view)
            logger.info("Updated existing chatbot dashboard message (%s) in thread %s", dashboard_msg.id, thread_id)
        else:
            dashboard_msg = await thread.send(embed=embed, view=view)
            logger.info("Posted initial chatbot dashboard message (%s) in thread %s", dashboard_msg.id, thread_id)

        live_chat_manager.set_dashboard(client, dashboard_msg)

    except Exception as e:
        logger.error("Failed to ensure chatbot dashboard message in thread %s: %s", thread_id, e, exc_info=True)
