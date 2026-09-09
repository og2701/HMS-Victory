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
from config import CHANNELS, BOT_ID, USERS

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
) -> str:
    """Synthesize raw server context and user input into a tailored starting prompt for HMS Victory."""
    api_key = openai_key or os.getenv("OPENAI_TOKEN")
    if not api_key:
        return user_input or ""

    if not user_input.strip() and not scraped_data.strip():
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
            return data["choices"][0]["message"]["content"].strip().strip('"')
    except Exception as e:
        logger.error("Failed to formulate starting context: %s", e, exc_info=True)
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
    if ch in ("general", "gen"):
        return CHANNELS.GENERAL, "General Chat"
    if ch in ("vip", "vip-lounge", "viplounge"):
        return CHANNELS.VIP_LOUNGE, "VIP Lounge"
    if ch in ("commons", "house-of-commons"):
        return CHANNELS.COMMONS, "House of Commons"
    if ch in ("politics", "pol"):
        return CHANNELS.POLITICS, "Politics"
    if ch in ("bot-spam", "botspam"):
        return CHANNELS.BOT_SPAM, "Bot Spam"
    try:
        cid = int(ch)
        return cid, f"Channel {cid}"
    except ValueError:
        return CHANNELS.GENERAL, "General Chat"


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
        self.cooldown: float = 3.0
        self.last_reply_time: float = 0.0
        self.conversation_history: deque = deque(maxlen=10)
        self.stop_task: Optional[asyncio.Task] = None

    def start(self, channel_id: int, channel_name: str = "", duration_seconds: float = 0.0, topic: Optional[str] = None):
        self.stop()
        self.active = True
        self.target_channel_id = channel_id
        self.target_channel_name = channel_name or f"Channel {channel_id}"
        self.start_time = time.time()
        self.duration_seconds = duration_seconds
        self.end_time = (self.start_time + duration_seconds) if duration_seconds > 0 else None
        self.topic = topic.strip() if topic and topic.strip() else None
        self.conversation_history.clear()
        self.last_reply_time = 0.0

        if self.duration_seconds > 0:
            self.stop_task = asyncio.create_task(self._auto_stop_timer(self.duration_seconds))

        logger.info("LiveChatManager started in channel %s (duration=%ss, topic=%r)", channel_id, duration_seconds, self.topic)

    def stop(self):
        self.active = False
        if self.stop_task and not self.stop_task.done():
            self.stop_task.cancel()
            self.stop_task = None
        logger.info("LiveChatManager stopped.")

    async def _auto_stop_timer(self, delay: float):
        try:
            await asyncio.sleep(delay)
            if self.active:
                logger.info("LiveChatManager duration expired. Automatically shutting down.")
                self.stop()
        except asyncio.CancelledError:
            pass

    def is_active_for(self, channel_id: int) -> bool:
        if not self.active:
            return False
        if self.end_time and time.time() >= self.end_time:
            self.stop()
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
                time_val = f"<t:{int(self.end_time)}:t> ({mins}m {secs}s remaining)"
            else:
                time_val = "Unlimited (manual stop)"
            topic_val = f"_{self.topic}_" if self.topic else "None (Natural conversation)"
        else:
            color = 0xE74C3C  # Red
            status_text = "🔴 **Offline / Asleep**"
            channel_val = "None"
            time_val = "N/A"
            topic_val = "None"

        embed = discord.Embed(
            title="🤖 HMS Victory Chatbot Dashboard",
            description="Control Vic's live conversational responder across server channels.",
            color=color,
        )
        embed.add_field(name="Status", value=status_text, inline=True)
        embed.add_field(name="Target Channel", value=channel_val, inline=True)
        embed.add_field(name="Auto-Stop Timer", value=time_val, inline=True)
        embed.add_field(name="Starting Topic", value=topic_val, inline=False)
        embed.set_footer(text="Persistent Controller • Oggers Only")
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

        if not (is_reply_to_bot or is_mentioned or name_called):
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
            reply_text = await asyncio.to_thread(
                generate_ai_reply,
                user_name=user_name,
                user_content=content,
                history=history_snapshot,
                topic=self.topic,
            )

            if reply_text:
                await message.reply(reply_text, mention_author=True)
                self.conversation_history.append({"role": "user", "speaker": user_name, "content": content})
                self.conversation_history.append({"role": "assistant", "speaker": "HMS Victory", "content": reply_text})
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
    topic_input = discord.ui.TextInput(
        label="Starting Context / Hint (Optional)",
        placeholder="e.g. Chin pub quiz, a Discord message link, or leave blank to auto-scrape",
        style=discord.TextStyle.paragraph,
        max_length=400,
        required=False,
    )

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != USERS.OGGERS:
            await interaction.response.send_message("Only Oggers can control this.", ephemeral=True)
            return

        # Defer immediately to allow time for scraping & AI context formulation
        await interaction.response.defer(ephemeral=True)

        cid, cname = resolve_channel_input(self.channel_input.value)
        dur = parse_duration_str(self.duration_input.value)
        raw_topic = self.topic_input.value.strip() if self.topic_input.value else ""

        final_topic = raw_topic
        try:
            scraped_data = await scrape_server_context(
                client=interaction.client,
                guild=interaction.guild,
                target_channel_id=cid,
                user_input=raw_topic,
            )
            if scraped_data or raw_topic:
                formulated = await asyncio.to_thread(
                    formulate_starting_context,
                    user_input=raw_topic,
                    scraped_data=scraped_data,
                )
                if formulated:
                    final_topic = formulated
        except Exception as e:
            logger.warning("Failed to scrape/formulate starting topic: %s", e, exc_info=True)

        live_chat_manager.start(
            channel_id=cid,
            channel_name=cname,
            duration_seconds=dur,
            topic=final_topic,
        )

        embed = live_chat_manager.get_status_embed()
        view = ChatbotDashboardView()
        try:
            if interaction.message:
                await interaction.message.edit(embed=embed, view=view)
        except Exception:
            pass

        msg = f"✅ **HMS Victory is awake in <#{cid}>!**"
        if final_topic:
            msg += f"\n**Formulated Starting Context:**\n> {final_topic}"
        await interaction.followup.send(msg, ephemeral=True)


class ChatbotDashboardView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != USERS.OGGERS:
            await interaction.response.send_message("⛔ Only Oggers can control HMS Victory.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Wake Up Vic", style=discord.ButtonStyle.success, emoji="🟢", custom_id="vic_live_wake_up")
    async def wake_up_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ChatbotWakeModal())

    @discord.ui.button(label="Put to Sleep", style=discord.ButtonStyle.danger, emoji="🔴", custom_id="vic_live_sleep")
    async def sleep_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        live_chat_manager.stop()
        embed = live_chat_manager.get_status_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, emoji="🔄", custom_id="vic_live_refresh")
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = live_chat_manager.get_status_embed()
        await interaction.response.edit_message(embed=embed, view=self)


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

    except Exception as e:
        logger.error("Failed to ensure chatbot dashboard message in thread %s: %s", thread_id, e, exc_info=True)
