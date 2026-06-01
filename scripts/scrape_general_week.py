import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

from config import CHANNELS, GUILD_ID

DISCORD_API = "https://discord.com/api/v10"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOCAL_TZ = ZoneInfo("Europe/London")


def load_env_file(env_path):
    if not env_path.exists():
        return

    with env_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def load_environment():
    env_path = PROJECT_ROOT / ".env"
    if load_dotenv:
        load_dotenv(dotenv_path=env_path)
    else:
        load_env_file(env_path)

    if os.getenv("DISCORD_TOKEN"):
        return

    service_file = "/etc/systemd/system/hms-victory.service"
    if not os.path.exists(service_file):
        return

    try:
        content = Path(service_file).read_text()
    except OSError:
        return

    match = re.search(r'Environment="DISCORD_TOKEN=([^"]+)"', content)
    if match:
        os.environ["DISCORD_TOKEN"] = match.group(1)


def parse_discord_timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def parse_cli_datetime(value, end_of_day=False):
    if value is None:
        return None

    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        dt = datetime.strptime(value, "%Y-%m-%d")
        if end_of_day:
            dt = dt.replace(hour=23, minute=59, second=59, microsecond=999999)
        return dt.replace(tzinfo=LOCAL_TZ).astimezone(timezone.utc)

    normalized = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=LOCAL_TZ)
    return dt.astimezone(timezone.utc)


def request_json(url, token):
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bot {token}",
            "User-Agent": "HMS-Victory magazine prompt scraper",
        },
    )

    while True:
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8")
            if exc.code == 429:
                try:
                    retry_after = json.loads(body).get("retry_after", 1)
                except json.JSONDecodeError:
                    retry_after = 1
                time.sleep(float(retry_after) + 0.25)
                continue
            raise RuntimeError(f"Discord API HTTP {exc.code}: {body}") from exc


def discord_asset_extension(asset_hash):
    return "gif" if asset_hash and asset_hash.startswith("a_") else "png"


def build_avatar_url(author, member):
    user_id = author.get("id")
    if not user_id:
        return None

    guild_avatar = member.get("avatar") if member else None
    if guild_avatar:
        ext = discord_asset_extension(guild_avatar)
        return (
            f"https://cdn.discordapp.com/guilds/{GUILD_ID}/users/"
            f"{user_id}/avatars/{guild_avatar}.{ext}?size=256"
        )

    avatar = author.get("avatar")
    if avatar:
        ext = discord_asset_extension(avatar)
        return f"https://cdn.discordapp.com/avatars/{user_id}/{avatar}.{ext}?size=256"

    return None


def message_to_record(message):
    author = message.get("author", {})
    member = message.get("member") or {}
    author_name = (
        member.get("nick")
        or author.get("global_name")
        or author.get("username")
        or "Unknown"
    )
    reactions = [
        {
            "emoji": reaction.get("emoji", {}).get("name", ""),
            "count": reaction.get("count", 0),
        }
        for reaction in message.get("reactions", [])
    ]
    attachments = [
        {
            "filename": attachment.get("filename"),
            "content_type": attachment.get("content_type"),
            "url": attachment.get("url"),
        }
        for attachment in message.get("attachments", [])
    ]
    reference = message.get("message_reference") or {}

    return {
        "id": str(message["id"]),
        "created_at": parse_discord_timestamp(message["timestamp"]).isoformat(),
        "author_id": str(author.get("id", "")),
        "author": author_name,
        "avatar_url": build_avatar_url(author, member),
        "content": message.get("content", ""),
        "reactions": reactions,
        "attachments": attachments,
        "reply_to": str(reference.get("message_id")) if reference else None,
        "jump_url": (
            f"https://discord.com/channels/{GUILD_ID}/"
            f"{CHANNELS.GENERAL}/{message['id']}"
        ),
    }


def load_existing_records(jsonl_path, since, until):
    records_by_id = {}
    if not jsonl_path.exists():
        return records_by_id

    skipped = 0
    with jsonl_path.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue

            try:
                created_at = datetime.fromisoformat(record["created_at"])
            except (KeyError, ValueError):
                skipped += 1
                continue

            if created_at >= since and (until is None or created_at <= until) and record.get("id"):
                records_by_id[record["id"]] = record

    print(
        f"Loaded {len(records_by_id)} checkpointed messages"
        f" from {jsonl_path}."
    )
    if skipped:
        print(f"Ignored {skipped} malformed or out-of-shape checkpoint lines.")
    return records_by_id


def append_jsonl_records(jsonl_path, records):
    if not records:
        return

    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    with jsonl_path.open("a", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def rewrite_jsonl_records(jsonl_path, records):
    tmp_path = jsonl_path.with_suffix(jsonl_path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
    tmp_path.replace(jsonl_path)


def write_text_transcript(records, txt_path):
    with txt_path.open("w", encoding="utf-8") as f:
        for record in records:
            reaction_text = ""
            if record["reactions"]:
                reaction_text = " [reactions: " + ", ".join(
                    f"{reaction['emoji']}x{reaction['count']}"
                    for reaction in record["reactions"]
                ) + "]"

            attachment_text = ""
            if record["attachments"]:
                attachment_text = " [attachments: " + ", ".join(
                    attachment["filename"] for attachment in record["attachments"]
                ) + "]"

            f.write(
                f"{record['created_at']} | {record['author']}: "
                f"{record['content']}{reaction_text}{attachment_text}\n"
            )


def print_stats(records, since, until=None):
    authors = Counter(record["author"] for record in records)
    reacted = []
    for record in records:
        reaction_count = sum(reaction["count"] for reaction in record["reactions"])
        if reaction_count:
            reacted.append((reaction_count, record))

    until_text = f" through {until.isoformat()}" if until else ""
    print(f"Scraped {len(records)} messages since {since.isoformat()}{until_text}.")
    print("\nTop posters:")
    for author, count in authors.most_common(10):
        print(f"- {author}: {count}")

    print("\nMost-reacted messages:")
    for reaction_count, record in sorted(
        reacted,
        key=lambda item: item[0],
        reverse=True,
    )[:10]:
        content = " ".join(record["content"].split())
        if len(content) > 140:
            content = content[:137] + "..."
        print(f"- {reaction_count} reactions | {record['author']}: {content}")


def scrape_messages(token, days, start_date, end_date, limit, output_base):
    since = parse_cli_datetime(start_date) if start_date else datetime.now(timezone.utc) - timedelta(days=days)
    until = parse_cli_datetime(end_date, end_of_day=True) if end_date else None
    jsonl_path = output_base.with_suffix(".jsonl")
    txt_path = output_base.with_suffix(".txt")
    records_by_id = load_existing_records(jsonl_path, since, until)
    before = None
    if records_by_id:
        oldest_record = min(records_by_id.values(), key=lambda record: record["created_at"])
        oldest_created_at = datetime.fromisoformat(oldest_record["created_at"])
        if oldest_created_at > since:
            before = oldest_record["id"]
            print(
                "Continuing from oldest checkpointed message "
                f"{oldest_record['created_at']} ({before})."
            )
    pages = 0
    fetched = 0
    saved = 0

    print(
        f"Scraping #general since {since.isoformat()} "
        f"{'through ' + until.isoformat() + ' ' if until else ''}"
        f"with limit {limit}."
    )

    while fetched < limit:
        page_limit = min(100, limit - fetched)
        params = {"limit": str(page_limit)}
        if before:
            params["before"] = before
        query = urllib.parse.urlencode(params)
        url = f"{DISCORD_API}/channels/{CHANNELS.GENERAL}/messages?{query}"
        messages = request_json(url, token)
        if not messages:
            break

        pages += 1
        fetched += len(messages)
        stop = False
        page_new_records = []
        for message in messages:
            created_at = parse_discord_timestamp(message["timestamp"])
            if created_at < since:
                stop = True
                continue
            if until is not None and created_at > until:
                continue

            author = message.get("author", {})
            if author.get("bot"):
                continue

            if message["id"] in records_by_id:
                continue

            record = message_to_record(message)
            records_by_id[record["id"]] = record
            page_new_records.append(record)

            if len(records_by_id) >= limit:
                break

        append_jsonl_records(jsonl_path, page_new_records)
        saved += len(page_new_records)
        oldest = parse_discord_timestamp(messages[-1]["timestamp"]).isoformat()
        print(
            f"Page {pages}: fetched {len(messages)}, "
            f"saved {len(page_new_records)} new, "
            f"checkpoint total {len(records_by_id)}, oldest {oldest}.",
            flush=True,
        )

        before = messages[-1]["id"]
        if stop or len(records_by_id) >= limit:
            break

    records = sorted(records_by_id.values(), key=lambda record: record["created_at"])
    rewrite_jsonl_records(jsonl_path, records)
    write_text_transcript(records, txt_path)
    print(
        f"Finished scrape. Added {saved} new messages this run. "
        f"Final transcript has {len(records)} messages.",
        flush=True,
    )
    return records, since, until, jsonl_path, txt_path


def main():
    parser = argparse.ArgumentParser(
        description="Scrape recent #general messages to temp transcript files."
    )
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument(
        "--start-date",
        help="Start date/time. YYYY-MM-DD is interpreted in Europe/London.",
    )
    parser.add_argument(
        "--end-date",
        help="Optional end date/time. YYYY-MM-DD includes the whole local day.",
    )
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument(
        "--output",
        default="/private/tmp/hms_victory_general_week",
        help="Output base path without extension.",
    )
    args = parser.parse_args()

    load_environment()
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("DISCORD_TOKEN is not configured.", file=sys.stderr)
        raise SystemExit(1)

    records, since, until, jsonl_path, txt_path = scrape_messages(
        token,
        args.days,
        args.start_date,
        args.end_date,
        args.limit,
        Path(args.output),
    )
    print_stats(records, since, until)
    print(f"\nJSONL transcript: {jsonl_path}")
    print(f"Text transcript: {txt_path}")


if __name__ == "__main__":
    main()
