#!/usr/bin/env python3
"""
HMS Victory Live Chat Responder
-------------------------------
CLI runner to activate real-time, in-character conversational replies in Discord.
Can be run interactively or via scripts/responder.sh.

Usage:
    python3 scripts/chat_responder.py [--channel general|vip|<id>] [--model gpt-4o]
"""

import sys
import os
import time
import argparse
import signal
import urllib.request
import json
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

def parse_args():
    parser = argparse.ArgumentParser(description="HMS Victory Live Chat Responder")
    parser.add_argument(
        "--channel",
        default="general",
        help="Target channel: 'general', 'vip', or a Discord channel ID (default: general)",
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


BOT_TOKEN = os.getenv("DISCORD_TOKEN")
OPENAI_KEY = os.getenv("OPENAI_TOKEN")


def resolve_channel_id(channel_arg: str) -> int:
    ch = channel_arg.lower().strip()
    if ch in ("general", "gen"):
        return CHANNELS.GENERAL
    if ch in ("vip", "viplounge", "vip-lounge"):
        return CHANNELS.VIP_LOUNGE
    try:
        return int(ch)
    except ValueError:
        sys.exit(f"Unknown channel argument: {channel_arg}. Use 'general', 'vip', or a channel ID.")


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

    channel_id = resolve_channel_id(args.channel)

    running = True

    def handle_signal(sig, frame):
        nonlocal running
        print("\nShutting down HMS Victory live responder...", flush=True)
        running = False

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    print(f"==================================================", flush=True)
    print(f" HMS Victory Live Chat Responder Active", flush=True)
    print(f" Target Channel ID: {channel_id}", flush=True)
    print(f" Model:             {args.model}", flush=True)
    print(f" Cooldown:          {args.cooldown}s", flush=True)
    print(f" Press Ctrl+C to stop.", flush=True)
    print(f"==================================================", flush=True)

    initial_msgs = get_recent_messages(channel_id, limit=30)
    seen_ids = {m["id"] for m in initial_msgs}
    our_bot_msg_ids = {m["id"] for m in initial_msgs if m.get("author", {}).get("id") == str(BOT_ID)}
    our_bot_msg_text = {m["id"]: m.get("content", "") for m in initial_msgs if m.get("author", {}).get("id") == str(BOT_ID)}

    print(f"Initialized with {len(seen_ids)} messages. Tracking {len(our_bot_msg_ids)} bot messages.", flush=True)

    last_reply_time = 0.0

    while running:
        try:
            msgs = get_recent_messages(channel_id, limit=10)
            # Process in chronological order (oldest first)
            for m in sorted(msgs, key=lambda x: int(x["id"])):
                mid = m["id"]
                if mid in seen_ids:
                    continue
                seen_ids.add(mid)

                author = m.get("author", {})
                if author.get("bot"):
                    if author.get("id") == str(BOT_ID):
                        our_bot_msg_ids.add(mid)
                        our_bot_msg_text[mid] = m.get("content", "")
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
                    # Enforce cooldown
                    now = time.time()
                    elapsed = now - last_reply_time
                    if elapsed < args.cooldown:
                        time.sleep(args.cooldown - elapsed)

                    member = m.get("member") or {}
                    # Prefer server nickname, then Discord display name, then username handle
                    user_name = member.get("nick") or author.get("global_name") or author.get("username") or "user"
                    user_handle = author.get("username", "")
                    print(f"[{time.strftime('%X')}] Triggered by {user_name} (@{user_handle}): \"{content}\" (ref={ref_id})", flush=True)

                    prior_text = our_bot_msg_text.get(ref_id, "")
                    reply_text = generate_ai_reply(
                        user_name=user_name,
                        user_content=content,
                        context_snippet=prior_text,
                        openai_key=OPENAI_KEY,
                        model=args.model,
                    )

                    sent_id = send_reply(channel_id, mid, reply_text)
                    if sent_id:
                        last_reply_time = time.time()
                        our_bot_msg_ids.add(sent_id)
                        our_bot_msg_text[sent_id] = reply_text
                        seen_ids.add(sent_id)
                        print(f"[{time.strftime('%X')}] Replied: \"{reply_text}\"", flush=True)

        except Exception as e:
            print(f"[ERROR] Polling loop error: {e}", file=sys.stderr, flush=True)

        time.sleep(2.5)

    print("Live responder terminated cleanly.", flush=True)


if __name__ == "__main__":
    main()
