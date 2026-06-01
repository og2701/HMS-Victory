import os
import re
import json
import shutil
import logging
from datetime import datetime, timedelta
import discord
import pytz
from lib.features.summary_html import create_summary_image
from lib.core.gemini import gemini_generate
from config import *
from database import DatabaseManager
from lib.core.file_operations import atomic_write_json

log = logging.getLogger(__name__)

_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(value):
    if not isinstance(value, str):
        return value
    return _HTML_TAG_RE.sub("", value).strip()


def _format_stats_for_prompt(summary_data, top_channel_ids=None):
    """Render summary_data as a plain-text stats block for the LLM."""
    lines = [
        f"Total members: {_strip_html(summary_data['total_members'])}",
        f"Members joined: {summary_data['members_joined']}",
        f"Members left: {summary_data['members_left']}",
        f"Members banned: {summary_data['members_banned']}",
        f"Total messages: {_strip_html(summary_data['total_messages'])}",
        f"Reactions added: {summary_data['reactions_added']}",
        f"Reactions removed: {summary_data['reactions_removed']}",
        f"Deleted messages: {summary_data['deleted_messages']}",
        f"Boosters gained: {summary_data['boosters_gained']}",
        f"Boosters lost: {summary_data['boosters_lost']}",
    ]

    if summary_data.get("top_channels"):
        lines.append("Top channels (messages):")
        ids = top_channel_ids or [None] * len(summary_data["top_channels"])
        for (name, count), channel_id in zip(summary_data["top_channels"], ids):
            mention = f"<#{channel_id}>" if channel_id else f"#{name}"
            lines.append(f"  - {mention} (name: {name}): {_strip_html(count)}")

    if summary_data.get("active_members"):
        lines.append("Most active members (messages):")
        for name, count in summary_data["active_members"]:
            lines.append(f"  - {name}: {count}")

    if summary_data.get("reacting_members"):
        lines.append("Top reactors:")
        for name, count in summary_data["reacting_members"]:
            lines.append(f"  - {name}: {count}")

    return "\n".join(lines)


def _format_previous_for_prompt(previous_data, guild, top_n):
    """Render the raw previous-period data dict as a comparison block."""
    if not previous_data or not previous_data.get("total_messages") and not previous_data.get("messages"):
        return None

    lines = [
        f"Total members: {previous_data.get('total_members', 0)}",
        f"Members joined: {previous_data.get('members_joined', 0)}",
        f"Members left: {previous_data.get('members_left', 0)}",
        f"Members banned: {previous_data.get('members_banned', 0)}",
        f"Total messages: {previous_data.get('total_messages', 0)}",
        f"Reactions added: {previous_data.get('reactions_added', 0)}",
        f"Reactions removed: {previous_data.get('reactions_removed', 0)}",
        f"Deleted messages: {previous_data.get('deleted_messages', 0)}",
        f"Boosters gained: {previous_data.get('boosters_gained', 0)}",
        f"Boosters lost: {previous_data.get('boosters_lost', 0)}",
    ]

    prev_channels = sorted(
        previous_data.get("messages", {}).items(), key=lambda x: x[1], reverse=True
    )[:top_n]
    if prev_channels:
        lines.append("Top channels (messages):")
        for channel_id, count in prev_channels:
            channel = guild.get_channel(int(channel_id)) if guild else None
            name = channel.name if channel else "deleted-channel"
            mention = f"<#{channel_id}>" if channel else f"#{name}"
            lines.append(f"  - {mention} (name: {name}): {count}")

    prev_active = sorted(
        previous_data.get("active_members", {}).items(), key=lambda x: x[1], reverse=True
    )[:top_n]
    if prev_active:
        lines.append("Most active members (messages):")
        for user_id, count in prev_active:
            member = guild.get_member(int(user_id)) if guild else None
            name = member.display_name if member else "Unknown Member"
            lines.append(f"  - {name}: {count}")

    prev_reactors = sorted(
        previous_data.get("reacting_members", {}).items(), key=lambda x: x[1], reverse=True
    )[:top_n]
    if prev_reactors:
        lines.append("Top reactors:")
        for user_id, count in prev_reactors:
            member = guild.get_member(int(user_id)) if guild else None
            name = member.display_name if member else "Unknown Member"
            lines.append(f"  - {name}: {count}")

    return "\n".join(lines)


async def _generate_summary_narrative(
    client, frequency, title, summary_data, previous_data, guild, top_n,
    top_channel_ids=None,
):
    """Ask Gemini for a short editorial blurb to post alongside the summary image."""
    stats_block = _format_stats_for_prompt(summary_data, top_channel_ids)
    previous_block = _format_previous_for_prompt(previous_data, guild, top_n)

    sentence_cap = {"daily": 2, "weekly": 3, "monthly": 4}.get(frequency, 2)
    previous_label = {
        "daily": "yesterday",
        "weekly": "the previous week",
        "monthly": "the previous month",
    }.get(frequency, "the previous period")

    system_prompt = (
        "You are the HMS Victory bot, posting a short editorial caption to accompany a "
        f"{frequency} server summary image for a UK-themed Discord. "
        f"Write at most {sentence_cap} sentence(s). Plain text only — no markdown headers, "
        "no bullet points, no emojis. Light British dry humour is welcome but never forced.\n\n"
        "You will be given two stats blocks: 'Current period' and 'Previous period'. "
        "Use the previous period to identify genuine trends — repeat winners (e.g. 'oggers tops "
        "the activity board for the second week running'), category swings not already captured "
        "in the (+N)/(-N) deltas (e.g. joins, leaves, bans, reactions, deletions), and noteworthy "
        "channel reshuffles. Treat small wobbles as noise.\n\n"
        "What to include:\n"
        "- Lead with the single most interesting thing in the data — a notable spike or drop, "
        "a streak across both periods, or a member/channel that clearly drove activity.\n"
        "- When referring to a channel, copy its Discord mention token verbatim — the "
        "`<#1234567890>` form shown in the stats block. Do NOT write `#channel-name` in "
        "plain text and do NOT invent or guess channel IDs; only use mention tokens that "
        "appear in the stats. Real member names should be written exactly as given.\n"
        "- Use (+N) / (-N) deltas where present, and otherwise compare current vs previous "
        "directly (e.g. 'joins doubled', 'half as many bans as last week').\n"
        "- If the period was unremarkable, say so briefly — don't manufacture drama.\n"
        "- Never restate the full numbers; the image already shows them. Add colour, not redundancy."
    )

    if previous_block:
        stats_section = (
            f"Current period:\n{stats_block}\n\n"
            f"Previous period ({previous_label}):\n{previous_block}"
        )
    else:
        stats_section = (
            f"Current period:\n{stats_block}\n\n"
            f"Previous period: not available — skip comparisons."
        )

    user_text = (
        f"Title: {title}\n"
        f"Frequency: {frequency}\n\n"
        f"{stats_section}\n\n"
        "Caption:"
    )

    session = getattr(client, "session", None)
    text, err = await gemini_generate(
        session,
        system_prompt,
        [{"text": user_text}],
        temperature=0.6,
        max_output_tokens=300,
    )
    if err:
        log.warning("Summary narrative generation failed: %s", err)
        return None

    if text and len(text) > 1900:
        text = text[:1900].rstrip() + "…"
    return text

SUMMARY_DATA_FILE = "daily_summaries/daily_summary_{date}.json"
SUMMARY_BACKUP_DATA_FILE = "daily_summaries/daily_summary_{date}_{time}.bak.json"


def get_file_path():
    uk_timezone = pytz.timezone("Europe/London")
    date = datetime.now(uk_timezone).strftime("%Y-%m-%d")
    file_path = SUMMARY_DATA_FILE.format(date=date)

    return file_path


def load_summary_data(date=None):
    uk_timezone = pytz.timezone("Europe/London")
    if date is None:
        date = datetime.now(uk_timezone).strftime("%Y-%m-%d")

    # Try database first
    db_data = DatabaseManager.fetch_one("SELECT data FROM daily_summaries WHERE date = ?", (date,))
    if db_data:
        try:
            return json.loads(db_data[0])
        except json.JSONDecodeError:
            print(f"Failed to decode summary data from database for {date}")

    # Fallback/Migration: Try JSON file
    file_path = SUMMARY_DATA_FILE.format(date=date)
    if os.path.isfile(file_path):
        with open(file_path, "r") as file:
            try:
                data = json.load(file)
                # Migrate to database
                DatabaseManager.execute(
                    "INSERT OR REPLACE INTO daily_summaries (date, data) VALUES (?, ?)",
                    (date, json.dumps(data))
                )
                return data
            except Exception as e:
                print(f"Failed to load/migrate summary data from JSON for {date}: {e}")

    # If both fail, initialize new data (only for today)
    current_date = datetime.now(uk_timezone).strftime("%Y-%m-%d")
    if date == current_date:
        initialize_summary_data(True)
        # Attempt to reload once after initialization
        db_data = DatabaseManager.fetch_one("SELECT data FROM daily_summaries WHERE date = ?", (date,))
        if db_data:
            return json.loads(db_data[0])

    return {}


def initialize_summary_data(force_init=False):
    uk_timezone = pytz.timezone("Europe/London")
    date = datetime.now(uk_timezone).strftime("%Y-%m-%d")

    # Check database
    exists = DatabaseManager.fetch_one("SELECT 1 FROM daily_summaries WHERE date = ?", (date,))

    if not exists or force_init:
        initial_data = {
            "total_members": 0,
            "members_joined": 0,
            "members_left": 0,
            "members_banned": 0,
            "messages": {},
            "total_messages": 0,
            "reactions_added": 0,
            "reactions_removed": 0,
            "deleted_messages": 0,
            "boosters_gained": 0,
            "boosters_lost": 0,
            "active_members": {},
            "reacting_members": {},
        }
        DatabaseManager.execute(
            "INSERT OR REPLACE INTO daily_summaries (date, data) VALUES (?, ?)",
            (date, json.dumps(initial_data))
        )
        # Still write to JSON for legacy/backup purposes if folder exists
        if os.path.exists("daily_summaries"):
            file_path = SUMMARY_DATA_FILE.format(date=date)
            atomic_write_json(file_path, initial_data)
    else:
        # Maintenance: ensure total_messages exists (sanity check)
        data = load_summary_data(date)
        if "total_messages" not in data:
            data["total_messages"] = 0
            DatabaseManager.execute(
                "UPDATE daily_summaries SET data = ? WHERE date = ?",
                (json.dumps(data), date)
            )


def update_summary_data(key, channel_id=None, user_id=None, remove=False):
    uk_timezone = pytz.timezone("Europe/London")
    date = datetime.now(uk_timezone).strftime("%Y-%m-%d")
    data = load_summary_data(date)

    if key == "messages" and channel_id:
        if str(channel_id) not in data["messages"]:
            data["messages"][str(channel_id)] = 0
        data["messages"][str(channel_id)] += 1
        data["total_messages"] += 1
    elif key == "active_members" and user_id:
        if str(user_id) not in data["active_members"]:
            data["active_members"][str(user_id)] = 0
        data["active_members"][str(user_id)] += 1
    elif key == "reacting_members" and user_id:
        if str(user_id) not in data["reacting_members"]:
            data["reacting_members"][str(user_id)] = 0
        data["reacting_members"][str(user_id)] += 1 if not remove else -1
        if data["reacting_members"][str(user_id)] <= 0:
            del data["reacting_members"][str(user_id)]
    else:
        data[key] += 1

    # Save to database
    DatabaseManager.execute(
        "UPDATE daily_summaries SET data = ? WHERE date = ?",
        (json.dumps(data), date)
    )

    # Legacy: Still write to JSON for now if folder exists
    if os.path.exists("daily_summaries"):
        file_path = SUMMARY_DATA_FILE.format(date=date)
        atomic_write_json(file_path, data)


def aggregate_summaries(start_date, end_date):
    aggregated_data = {
        "total_members": 0,
        "members_joined": 0,
        "members_left": 0,
        "members_banned": 0,
        "messages": {},
        "total_messages": 0,
        "reactions_added": 0,
        "reactions_removed": 0,
        "deleted_messages": 0,
        "boosters_gained": 0,
        "boosters_lost": 0,
        "active_members": {},
        "reacting_members": {},
    }
    
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")
    
    # Efficiently fetch all days in the range in one go
    rows = DatabaseManager.fetch_all(
        "SELECT data FROM daily_summaries WHERE date BETWEEN ? AND ? ORDER BY date ASC",
        (start_str, end_str)
    )
    
    for row in rows:
        try:
            daily_data = json.loads(row[0])
            for key in aggregated_data.keys():
                if key in ["messages", "active_members", "reacting_members"]:
                    for sub_key, count in daily_data.get(key, {}).items():
                        if sub_key not in aggregated_data[key]:
                            aggregated_data[key][sub_key] = 0
                        aggregated_data[key][sub_key] += count
                elif key == "total_members":
                    # For total members, we take the value from the last day in the range
                    aggregated_data["total_members"] = daily_data.get("total_members", 0)
                else:
                    aggregated_data[key] += daily_data.get(key, 0)
        except json.JSONDecodeError:
            continue
            
    return aggregated_data


async def post_summary(
    client, log_channel_id, frequency, channel_override=None, date=None
):
    log_channel = (
        client.get_channel(log_channel_id)
        if channel_override is None
        else channel_override
    )
    guild = log_channel.guild
    total_members = guild.member_count
    if date is None:
        uk_timezone = pytz.timezone("Europe/London")
        date = datetime.now(uk_timezone).strftime("%Y-%m-%d")
    member_change_str = ""
    member_change_color = "white"
    message_change_str = {}
    message_change_color = {}
    if log_channel is not None:
        if frequency == "daily":
            date_obj = datetime.strptime(date, "%Y-%m-%d")
            date_dd_mm_yyyy = date_obj.strftime("%d-%m-%Y")
            title = f"Daily Server Summary - {date_dd_mm_yyyy}"
            title_color = "#7289da"
            
            data = load_summary_data(date)
            if not data:
                if log_channel:
                    await log_channel.send(f"⚠️ Could not load summary data for {date_dd_mm_yyyy}.")
                return

            previous_date_obj = date_obj - timedelta(days=1)
            previous_date = previous_date_obj.strftime("%Y-%m-%d")
            previous_data = load_summary_data(previous_date)

            if previous_data:
                member_change = total_members - previous_data["total_members"]
                member_change_str = (
                    f" (+{member_change})"
                    if member_change > 0
                    else f" ({member_change})"
                )
                member_change_color = "green" if member_change > 0 else "red"
                for channel_id, count in data["messages"].items():
                    prev_count = previous_data["messages"].get(channel_id, 0)
                    message_change = count - prev_count
                    message_change_str[channel_id] = (
                        f" (+{message_change})"
                        if message_change > 0
                        else f" ({message_change})"
                    )
                    message_change_color[channel_id] = (
                        "green" if message_change > 0 else "red"
                    )
                total_message_change = (
                    data["total_messages"] - previous_data["total_messages"]
                )
                total_message_change_str = (
                    f" (+{total_message_change})"
                    if total_message_change > 0
                    else f" ({total_message_change})"
                )
                total_message_change_color = (
                    "green" if total_message_change > 0 else "red"
                )
            else:
                total_message_change_str = ""
                total_message_change_color = "white"
        elif frequency == "weekly":
            date_obj = datetime.strptime(date, "%Y-%m-%d")
            end_date = date_obj - timedelta(days=1)
            start_date = end_date - timedelta(days=6)
            data = aggregate_summaries(start_date, end_date)
            title_color = "#7CFC00"
            prev_end_date = start_date - timedelta(days=1)
            prev_start_date = prev_end_date - timedelta(days=6)
            previous_data = aggregate_summaries(prev_start_date, prev_end_date)
            start_str = start_date.strftime("%d-%m-%Y")
            end_str = end_date.strftime("%d-%m-%Y")
            title = f"Weekly Server Summary - {start_str} to {end_str}"
            member_change = total_members - previous_data["total_members"]
            member_change_str = (
                f" (+{member_change})" if member_change > 0 else f" ({member_change})"
            )
            member_change_color = "green" if member_change > 0 else "red"
            total_message_change = (
                data["total_messages"] - previous_data["total_messages"]
            )
            total_message_change_str = (
                f" (+{total_message_change})"
                if total_message_change > 0
                else f" ({total_message_change})"
            )
            total_message_change_color = "green" if total_message_change > 0 else "red"
            for channel_id, count in data["messages"].items():
                prev_count = previous_data["messages"].get(str(channel_id), 0)
                message_change = count - prev_count
                message_change_str[channel_id] = (
                    f" (+{message_change})"
                    if message_change > 0
                    else f" ({message_change})"
                )
                message_change_color[channel_id] = (
                    "green" if message_change > 0 else "red"
                )
        elif frequency == "monthly":
            date_obj = datetime.strptime(date, "%Y-%m-%d")
            this_month_start = date_obj.replace(day=1)
            end_date = this_month_start - timedelta(days=1)
            start_date = end_date.replace(day=1)
            data = aggregate_summaries(start_date, end_date)
            title_color = "#FFD700"
            prev_end_date = start_date - timedelta(days=1)
            prev_start_date = prev_end_date.replace(day=1)
            previous_data = aggregate_summaries(prev_start_date, prev_end_date)
            month_str = end_date.strftime("%B %Y")
            title = f"Monthly Server Summary - {month_str}"
            member_change = total_members - previous_data["total_members"]
            member_change_str = (
                f" (+{member_change})" if member_change > 0 else f" ({member_change})"
            )
            member_change_color = "green" if member_change > 0 else "red"
            total_message_change = (
                data["total_messages"] - previous_data["total_messages"]
            )
            total_message_change_str = (
                f" (+{total_message_change})"
                if total_message_change > 0
                else f" ({total_message_change})"
            )
            total_message_change_color = "green" if total_message_change > 0 else "red"
            for channel_id, count in data["messages"].items():
                prev_count = previous_data["messages"].get(str(channel_id), 0)
                message_change = count - prev_count
                message_change_str[channel_id] = (
                    f" (+{message_change})"
                    if message_change > 0
                    else f" ({message_change})"
                )
                message_change_color[channel_id] = (
                    "green" if message_change > 0 else "red"
                )
        top_n = 5 if frequency == "daily" else 10
        active_members = sorted(
            data.get("active_members", {}).items(), key=lambda x: x[1], reverse=True
        )[:top_n]
        reacting_members = sorted(
            data.get("reacting_members", {}).items(), key=lambda x: x[1], reverse=True
        )[:top_n]
        top_channels = sorted(
            data.get("messages", {}).items(), key=lambda x: x[1], reverse=True
        )[:top_n]
        summary_data = {
            "total_members": f"{total_members} <span style='color: {member_change_color};'>{member_change_str}</span>",
            "members_joined": data["members_joined"],
            "members_left": data["members_left"],
            "members_banned": data["members_banned"],
            "total_messages": f"{data['total_messages']} <span style='color: {total_message_change_color};'>{total_message_change_str}</span>",
            "reactions_added": data["reactions_added"],
            "reactions_removed": data["reactions_removed"],
            "deleted_messages": data["deleted_messages"],
            "boosters_gained": data["boosters_gained"],
            "boosters_lost": data["boosters_lost"],
            "top_channels": [
                (
                    (
                        log_channel.guild.get_channel(int(channel_id)).name
                        if log_channel.guild.get_channel(int(channel_id))
                        else "Deleted Channel"
                    ),
                    f"{count} <span style='color: {message_change_color.get(channel_id, 'white')};'>{message_change_str.get(channel_id, '')}</span>",
                )
                for channel_id, count in top_channels
            ],
            "active_members": [
                (
                    (
                        guild.get_member(int(user_id)).display_name
                        if guild.get_member(int(user_id))
                        else "Unknown Member"
                    ),
                    count,
                )
                for user_id, count in active_members
            ],
            "reacting_members": [
                (
                    (
                        guild.get_member(int(user_id)).display_name
                        if guild.get_member(int(user_id))
                        else "Unknown Member"
                    ),
                    count,
                )
                for user_id, count in reacting_members
            ],
        }
        top_channel_ids = [channel_id for channel_id, _ in top_channels]
        image_buffer = await create_summary_image(summary_data, title, title_color)
        narrative = await _generate_summary_narrative(
            client, frequency, title, summary_data, previous_data, guild, top_n,
            top_channel_ids=top_channel_ids,
        )
        await log_channel.send(
            content=narrative or None,
            file=discord.File(image_buffer, filename=f"{frequency}_summary.png"),
        )
        if frequency == "daily":
            data["total_members"] = total_members
            # Save final total_members update to DB
            DatabaseManager.execute(
                "UPDATE daily_summaries SET data = ? WHERE date = ?",
                (json.dumps(data), date)
            )
            # Legacy fallback
            if os.path.exists("daily_summaries"):
                file_path = SUMMARY_DATA_FILE.format(date=date)
                atomic_write_json(file_path, data)
