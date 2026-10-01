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
        f"Write at most {sentence_cap} sentence(s). Plain text only - no markdown headers, "
        "no bullet points, no emojis. Light British dry humour is welcome but never forced.\n\n"
        "You will be given two stats blocks: 'Current period' and 'Previous period'. "
        "Use the previous period to identify genuine trends - repeat winners (e.g. 'oggers tops "
        "the activity board for the second week running'), category swings not already captured "
        "in the (+N)/(-N) deltas (e.g. joins, leaves, bans, reactions, deletions), and noteworthy "
        "channel reshuffles. Treat small wobbles as noise.\n\n"
        "What to include:\n"
        "- Lead with the single most interesting thing in the data - a notable spike or drop, "
        "a streak across both periods, or a member/channel that clearly drove activity.\n"
        "- When referring to a channel, copy its Discord mention token verbatim - the "
        "`<#1234567890>` form shown in the stats block. Do NOT write `#channel-name` in "
        "plain text and do NOT invent or guess channel IDs; only use mention tokens that "
        "appear in the stats. Real member names should be written exactly as given.\n"
        "- Use (+N) / (-N) deltas where present, and otherwise compare current vs previous "
        "directly (e.g. 'joins doubled', 'half as many bans as last week').\n"
        "- If the period was unremarkable, say so briefly - don't manufacture drama.\n"
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
            f"Previous period: not available - skip comparisons."
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


def _delta_text(current, previous):
    change = current - previous
    return f" (+{change})" if change > 0 else f" ({change})"


def _period_bounds(frequency, date_obj):
    """(start, end, prev_start, prev_end) as dates for the period the post covers."""
    if frequency == "daily":
        prev = date_obj - timedelta(days=1)
        return date_obj, date_obj, prev, prev
    if frequency == "weekly":
        end = date_obj - timedelta(days=1)
        start = end - timedelta(days=6)
        prev_end = start - timedelta(days=1)
        return start, end, prev_end - timedelta(days=6), prev_end
    end = date_obj.replace(day=1) - timedelta(days=1)
    start = end.replace(day=1)
    prev_end = start - timedelta(days=1)
    return start, end, prev_end.replace(day=1), prev_end


def _date_span(start, end):
    days = []
    d = start
    while d <= end:
        days.append(d)
        d += timedelta(days=1)
    return days


def _subtitle(frequency, start, end):
    if frequency == "daily":
        return end.strftime("%A %-d %B %Y")
    if frequency == "monthly":
        return end.strftime("%B %Y")
    if start.month == end.month:
        return f"{start.day} - {end.day} {end.strftime('%B %Y')}"
    return f"{start.strftime('%-d %b')} - {end.strftime('%-d %b %Y')}"


async def _people(client, guild, ranked):
    """Ranked (user_id, count) pairs -> card entries with a display name and avatar."""
    from lib.core.image_processing import get_avatar_data_uri
    from lib.features.summary_html import AVATAR_COLOURS

    people = []
    for user_id, count in ranked:
        member = guild.get_member(int(user_id)) if guild else None
        entry = {
            "name": member.display_name if member else "Unknown Member",
            "count": int(count),
            "avatar": None,
            "colour": AVATAR_COLOURS[int(user_id) % len(AVATAR_COLOURS)] if member else "#4E5058",
        }
        if member:
            try:
                url = member.display_avatar.with_size(128).with_static_format("png").url
                entry["avatar"] = await get_avatar_data_uri(client, url)
            except Exception:
                log.debug("avatar fetch failed for %s", user_id, exc_info=True)
        people.append(entry)
    return people


async def _build_card(client, guild, frequency, data, previous_data, total_members,
                      member_change, start, end):
    from lib.features import summary_stats as stats

    days = _date_span(start, end)
    # Members need one extra day before the window for a starting point.
    lookback = start - timedelta(days=7 if frequency == "daily" else 1)
    history = stats.daily_series(lookback, end)
    if frequency == "daily":
        series_days = _date_span(end - timedelta(days=6), end)
    else:
        series_days = days

    message_series = [history.get(d, {}).get("messages") for d in series_days]
    if frequency == "daily":
        message_series[-1] = data.get("total_messages", 0)
    # A day's member total is only written after its summary posts, so use the live count.
    member_series = [history.get(d, {}).get("members") or None for d in series_days]
    member_series[-1] = total_members

    if frequency == "daily":
        baseline = history.get(end - timedelta(days=7), {}).get("members")
        member_caption = f"{_signed(total_members - baseline)} this week" if baseline else "Last 7 days"
        message_caption = "Last 7 days"
    else:
        per_day = (member_change or 0) / len(days)
        member_caption = (f"About {per_day:.0f} new a day" if per_day >= 0.5
                          else f"About {abs(per_day):.0f} fewer a day" if per_day <= -0.5
                          else "Holding steady")
        message_caption = (f"Daily totals, {start.strftime('%a')} to {end.strftime('%a')}"
                           if frequency == "weekly"
                           else f"Daily totals, 1 to {end.day} {end.strftime('%b')}")

    if frequency == "monthly":
        activity = {"kind": "month",
                    "days": [(d, history.get(d, {}).get("messages", 0)) for d in days]}
    else:
        by_day = stats.message_activity(start, end)
        activity = ({"kind": "hours", "hours": by_day[end]} if frequency == "daily"
                    else {"kind": "week", "days": [(d, by_day[d]) for d in days]})

    top_n = 5 if frequency == "daily" else 10
    ranked = lambda key: sorted(data.get(key, {}).items(), key=lambda x: x[1], reverse=True)[:top_n]
    channels = []
    # Busy threads count too, and get_channel alone doesn't see them.
    lookup = getattr(guild, "get_channel_or_thread", None) or guild.get_channel
    for channel_id, count in sorted(data.get("messages", {}).items(), key=lambda x: x[1], reverse=True)[:4]:
        channel = lookup(int(channel_id))
        channels.append((f"#{channel.name}" if channel else "#deleted-channel", int(count)))

    return {
        "frequency": frequency,
        "subtitle": _subtitle(frequency, start, end),
        "messages": int(data.get("total_messages", 0)),
        "messages_prev": int(previous_data.get("total_messages", 0)) if previous_data else None,
        "message_series": message_series,
        "message_caption": message_caption,
        "members": int(total_members),
        "members_change": member_change,
        "member_series": member_series,
        "member_caption": member_caption,
        "activity": activity,
        "joined": int(data.get("members_joined", 0)),
        "left": int(data.get("members_left", 0)),
        "banned": int(data.get("members_banned", 0)),
        "reactions": int(data.get("reactions_added", 0)),
        "reactions_removed": int(data.get("reactions_removed", 0)),
        "boosts_gained": int(data.get("boosters_gained", 0)),
        "boosts_lost": int(data.get("boosters_lost", 0)),
        "chatting": len(data.get("active_members", {})),
        "new_voices": stats.new_voices(start, end),
        "media": stats.media_count(start, end),
        "deleted": int(data.get("deleted_messages", 0)),
        "channels": channels,
        "chatters": await _people(client, guild, ranked("active_members")),
        "reactors": await _people(client, guild, ranked("reacting_members")),
    }


def _signed(n):
    return f"+{n:,}" if n > 0 else (f"−{abs(n):,}" if n < 0 else "0")


async def post_summary(
    client, log_channel_id, frequency, channel_override=None, date=None
):
    log_channel = (
        client.get_channel(log_channel_id)
        if channel_override is None
        else channel_override
    )
    if log_channel is None:
        return
    guild = log_channel.guild
    total_members = guild.member_count
    if date is None:
        uk_timezone = pytz.timezone("Europe/London")
        date = datetime.now(uk_timezone).strftime("%Y-%m-%d")

    date_obj = datetime.strptime(date, "%Y-%m-%d").date()
    start, end, prev_start, prev_end = _period_bounds(frequency, date_obj)

    if frequency == "daily":
        title = f"Daily Server Summary - {date_obj.strftime('%d-%m-%Y')}"
        data = load_summary_data(date)
        if not data:
            await log_channel.send(f"⚠️ Could not load summary data for {date_obj.strftime('%d-%m-%Y')}.")
            return
        previous_data = load_summary_data(prev_end.strftime("%Y-%m-%d"))
    else:
        if frequency == "weekly":
            title = f"Weekly Server Summary - {start.strftime('%d-%m-%Y')} to {end.strftime('%d-%m-%Y')}"
        else:
            title = f"Monthly Server Summary - {end.strftime('%B %Y')}"
        data = aggregate_summaries(start, end)
        previous_data = aggregate_summaries(prev_start, prev_end)

    member_change = None
    member_change_str = ""
    total_message_change_str = ""
    message_change_str = {}
    if previous_data and previous_data.get("total_members"):
        member_change = total_members - previous_data["total_members"]
        member_change_str = _delta_text(total_members, previous_data["total_members"])
    if previous_data:
        total_message_change_str = _delta_text(data["total_messages"], previous_data.get("total_messages", 0))
        for channel_id, count in data["messages"].items():
            message_change_str[channel_id] = _delta_text(
                count, previous_data.get("messages", {}).get(str(channel_id), 0))

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

    def _name(user_id):
        member = guild.get_member(int(user_id))
        return member.display_name if member else "Unknown Member"

    def _channel_name(channel_id):
        channel = guild.get_channel(int(channel_id))
        return channel.name if channel else "Deleted Channel"

    # Plain-text stats for the Gemini caption.
    summary_data = {
        "total_members": f"{total_members}{member_change_str}",
        "members_joined": data["members_joined"],
        "members_left": data["members_left"],
        "members_banned": data["members_banned"],
        "total_messages": f"{data['total_messages']}{total_message_change_str}",
        "reactions_added": data["reactions_added"],
        "reactions_removed": data["reactions_removed"],
        "deleted_messages": data["deleted_messages"],
        "boosters_gained": data["boosters_gained"],
        "boosters_lost": data["boosters_lost"],
        "top_channels": [
            (_channel_name(channel_id), f"{count}{message_change_str.get(channel_id, '')}")
            for channel_id, count in top_channels
        ],
        "active_members": [(_name(user_id), count) for user_id, count in active_members],
        "reacting_members": [(_name(user_id), count) for user_id, count in reacting_members],
    }
    top_channel_ids = [channel_id for channel_id, _ in top_channels]

    card = await _build_card(
        client, guild, frequency, data, previous_data, total_members, member_change, start, end)
    image_buffer = await create_summary_image(card)
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
