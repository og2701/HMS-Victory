#!/usr/bin/env python3
"""
HMS Victory Live Chat Responder
-------------------------------
CLI runner to activate real-time, in-character conversational replies in Discord.
Can be run interactively or via scripts/responder.sh.

Usage:
    python3 scripts/chat_responder.py [--channel general|vip|<id>] [--duration 30m] [--topic "text"] [--model gpt-4o]
"""

import sys
import os
import time
import argparse
import signal
import urllib.request
import json
from collections import deque
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config import CHANNELS, BOT_ID
from lib.features.chat_responder import generate_ai_reply

# Load .env file manually if not already in os.environ
def load_env_file(env_path: Path):
    if not env_path.exists():
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k not in os.environ:
                    os.environ[k] = v

load_env_file(PROJECT_ROOT / ".env")

BOT_TOKEN = os.getenv("DISCORD_TOKEN")
OPENAI_KEY = os.getenv("OPENAI_TOKEN")

CHANNEL_PRESETS = {
    "1": ("general", "General Chat", CHANNELS.GENERAL),
    "2": ("vip", "VIP Lounge", CHANNELS.VIP_LOUNGE),
    "3": ("commons", "House of Commons", CHANNELS.COMMONS),
    "4": ("politics", "Politics", CHANNELS.POLITICS),
}

def parse_duration(val: str) -> float:
    """Parse a human duration string (e.g. '30m', '1h', '45s', '1800') into seconds."""
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
        print(f"[WARN] Invalid duration format '{val}'. No time limit will be applied.", file=sys.stderr)
        return 0.0

def format_duration(seconds: float) -> str:
    """Format seconds into human-readable string."""
    if seconds <= 0:
        return "Unlimited (manual stop)"
    minutes = seconds / 60
    hours = minutes / 60
    if hours >= 1:
        return f"{hours:.1f} hour(s)" if hours % 1 != 0 else f"{int(hours)} hour(s)"
    if minutes >= 1:
        return f"{minutes:.1f} minute(s)" if minutes % 1 != 0 else f"{int(minutes)} minute(s)"
    return f"{int(seconds)} second(s)"

def parse_args():
    parser = argparse.ArgumentParser(description="HMS Victory Live Chat Responder")
    parser.add_argument(
        "--channel",
        default=None,
        help="Target channel: 'general', 'vip', 'commons', 'politics', or a Discord channel ID",
    )
    parser.add_argument(
        "--duration",
        "--time-limit",
        "--timeout",
        dest="duration",
        default=None,
        help="Auto-stop time limit (e.g. 15m, 30m, 1h, 2h). Default: none",
    )
    parser.add_argument(
        "--topic",
        default=None,
        help="Optional starting topic or context to kick off the session",
    )
    parser.add_argument(
        "--model",
        default="gpt-4o",
        help="OpenAI model name (default: gpt-4o)",
    )
    parser.add_argument(
        "--cooldown",
        type=float,
        default=3.0,
        help="Minimum seconds between replies to avoid spam bursts (default: 3.0)",
    )
    return parser.parse_args()


def resolve_channel_id(channel_arg: str) -> tuple[int, str]:
    if not channel_arg:
        return CHANNELS.GENERAL, "General Chat"

    ch = channel_arg.lower().strip()
    if ch in ("general", "gen"):
        return CHANNELS.GENERAL, "General Chat"
    if ch in ("vip", "viplounge", "vip-lounge"):
        return CHANNELS.VIP_LOUNGE, "VIP Lounge"
    if ch in ("commons", "house-of-commons"):
        return CHANNELS.COMMONS, "House of Commons"
    if ch in ("politics", "pol"):
        return CHANNELS.POLITICS, "Politics"
    if ch in ("bot-spam", "botspam"):
        return CHANNELS.BOT_SPAM, "Bot Spam"
    try:
        cid = int(ch)
        return cid, f"Channel ID {cid}"
    except ValueError:
        sys.exit(f"Unknown channel argument: '{channel_arg}'. Use 'general', 'vip', 'commons', 'politics', or a numeric channel ID.")


def prompt_options_if_interactive(channel_arg, duration_arg, topic_arg):
    is_interactive = sys.stdin.isatty()

    selected_channel = channel_arg
    selected_duration = duration_arg
    selected_topic = topic_arg

    if selected_channel is None and is_interactive:
        print("\nChoose target Discord channel:")
        for key, (_, name, cid) in CHANNEL_PRESETS.items():
            print(f"  {key}) #{name} ({cid})")
        print("  5) Custom Channel ID")
        try:
            choice = input("Select channel [1 for #general]: ").strip()
            if choice in CHANNEL_PRESETS:
                selected_channel = CHANNEL_PRESETS[choice][0]
            elif choice == "5":
                selected_channel = input("Enter Discord Channel ID: ").strip()
            elif not choice:
                selected_channel = "general"
            else:
                selected_channel = choice
        except (KeyboardInterrupt, EOFError):
            print("\nAborted.")
            sys.exit(0)

    if selected_duration is None and is_interactive:
        try:
            dur_input = input("Auto-stop time limit (e.g. 15m, 30m, 1h, or press Enter for no limit): ").strip()
            if dur_input:
                selected_duration = dur_input
        except (KeyboardInterrupt, EOFError):
            print("\nAborted.")
            sys.exit(0)

    if selected_topic is None and is_interactive:
        try:
            top_input = input("Starting topic/context (optional, press Enter to skip): ").strip()
            if top_input:
                selected_topic = top_input
        except (KeyboardInterrupt, EOFError):
            print("\nAborted.")
            sys.exit(0)

    return selected_channel or "general", selected_duration, selected_topic


def get_recent_messages(channel_id: int, limit: int = 15):
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages?limit={limit}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bot {BOT_TOKEN}",
            "User-Agent": "DiscordBot (HMS, 1.0)",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"[WARN] Failed to fetch messages: {e}", file=sys.stderr, flush=True)
        return []


def send_reply(channel_id: int, reply_to_id: str, content: str):
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    payload = {
        "content": content,
        "message_reference": {"message_id": reply_to_id},
        "allowed_mentions": {"replied_user": True},
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bot {BOT_TOKEN}",
            "Content-Type": "application/json",
            "User-Agent": "DiscordBot (HMS, 1.0)",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            res = json.loads(resp.read().decode())
            return res.get("id")
    except Exception as e:
        print(f"[ERROR] Failed to send reply: {e}", file=sys.stderr, flush=True)
        return None


def main():
    args = parse_args()

    if not BOT_TOKEN:
        sys.exit("Error: DISCORD_TOKEN is missing from environment / .env")
    if not OPENAI_KEY:
        sys.exit("Error: OPENAI_TOKEN is missing from environment / .env")

    ch_input, dur_input, active_topic = prompt_options_if_interactive(args.channel, args.duration, args.topic)
    channel_id, channel_name = resolve_channel_id(ch_input)
    duration_seconds = parse_duration(dur_input)

    start_time = time.time()
    end_time = start_time + duration_seconds if duration_seconds > 0 else None

    # Rolling multi-turn conversation memory (last 10 turns)
    conversation_history = deque(maxlen=10)

    running = True

    def handle_signal(sig, frame):
        nonlocal running
        print("\nShutting down HMS Victory live responder...", flush=True)
        running = False

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    print(f"==================================================", flush=True)
    print(f" HMS Victory Live Chat Responder Active", flush=True)
    print(f" Target Channel:    #{channel_name} ({channel_id})", flush=True)
    print(f" Starting Topic:    {active_topic or 'None (Natural conversation)'}", flush=True)
    print(f" Model:             {args.model}", flush=True)
    print(f" Cooldown:          {args.cooldown}s", flush=True)
    print(f" Time Limit:        {format_duration(duration_seconds)}", flush=True)
    if end_time:
        print(f" Auto-Stop At:      {time.strftime('%X', time.localtime(end_time))}", flush=True)
    print(f" Press Ctrl+C to stop anytime.", flush=True)
    print(f"==================================================", flush=True)

    initial_msgs = get_recent_messages(channel_id, limit=30)
    seen_ids = {m["id"] for m in initial_msgs}
    our_bot_msg_ids = {m["id"] for m in initial_msgs if m.get("author", {}).get("id") == str(BOT_ID)}

    print(f"Initialized with {len(seen_ids)} messages. Tracking {len(our_bot_msg_ids)} bot messages.", flush=True)

    last_reply_time = 0.0

    while running:
        if end_time and time.time() >= end_time:
            print(f"\n[TIME LIMIT REACHED] Auto-stopping responder after {format_duration(duration_seconds)}.", flush=True)
            break

        try:
            msgs = get_recent_messages(channel_id, limit=10)
            for m in sorted(msgs, key=lambda x: int(x["id"])):
                mid = m["id"]
                if mid in seen_ids:
                    continue
                seen_ids.add(mid)

                author = m.get("author", {})
                if author.get("bot"):
                    if author.get("id") == str(BOT_ID):
                        our_bot_msg_ids.add(mid)
                    continue

                content = m.get("content", "")
                ref_id = m.get("message_reference", {}).get("message_id")

                is_reply_to_us = ref_id in our_bot_msg_ids
                is_mentioned = f"<@{BOT_ID}>" in content or f"<@!{BOT_ID}>" in content
                name_called = (
                    content.lower().strip().startswith("vic ")
                    or content.lower().strip() == "vic"
                    or "hms victory" in content.lower()
                )

                if is_reply_to_us or is_mentioned or name_called:
                    now = time.time()
                    elapsed = now - last_reply_time
                    if elapsed < args.cooldown:
                        time.sleep(args.cooldown - elapsed)

                    member = m.get("member") or {}
                    user_name = member.get("nick") or author.get("global_name") or author.get("username") or "user"
                    user_handle = author.get("username", "")
                    print(f"[{time.strftime('%X')}] Triggered by {user_name} (@{user_handle}): \"{content}\" (ref={ref_id})", flush=True)

                    reply_text = generate_ai_reply(
                        user_name=user_name,
                        user_content=content,
                        history=list(conversation_history),
                        topic=active_topic,
                        openai_key=OPENAI_KEY,
                        model=args.model,
                    )

                    sent_id = send_reply(channel_id, mid, reply_text)
                    if sent_id:
                        last_reply_time = time.time()
                        our_bot_msg_ids.add(sent_id)
                        seen_ids.add(sent_id)

                        # Update rolling multi-turn memory
                        conversation_history.append({"role": "user", "speaker": user_name, "content": content})
                        conversation_history.append({"role": "assistant", "speaker": "HMS Victory", "content": reply_text})

                        print(f"[{time.strftime('%X')}] Replied: \"{reply_text}\"", flush=True)

        except Exception as e:
            print(f"[ERROR] Polling loop error: {e}", file=sys.stderr, flush=True)

        time.sleep(2.5)

    print("Live responder terminated cleanly.", flush=True)


if __name__ == "__main__":
    main()
