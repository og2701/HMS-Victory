import os
import json
import shutil
from datetime import datetime, timedelta
import discord
import pytz
from lib.features.summary_html import create_summary_image
from config import *

SUMMARY_DATA_FILE = "daily_summaries/daily_summary_{date}.json"
SUMMARY_BACKUP_DATA_FILE = "daily_summaries/daily_summary_{date}_{time}.bak.json"


def get_file_path():
    uk_timezone = pytz.timezone("Europe/London")
    date = datetime.now(uk_timezone).strftime("%Y-%m-%d")
    file_path = SUMMARY_DATA_FILE.format(date=date)

    return file_path


def load_summary_data():
    uk_timezone = pytz.timezone("Europe/London")
    file_path = get_file_path()

    for i in range(1, 3):
        if not os.path.isfile(file_path):
            print("Daily summary file was not created")
            initialize_summary_data(True)
            continue
        with open(file_path, "r") as file:
            try:
                return json.load(file)
            except:
                date = datetime.now(uk_timezone).strftime("%Y-%m-%d")
                time = datetime.now(uk_timezone).strftime("%H-%M-%S")
                backup_file_path = SUMMARY_BACKUP_DATA_FILE.format(date=date, time=time)

                shutil.copyfile(file_path, backup_file_path)

                print(
                    "Failed to load summary data, saved current contents of {0} to {1} and created a new file.".format(
                        file_path, backup_file_path
                    )
                )
                initialize_summary_data(True)

    return {}


def initialize_summary_data(force_init=False):
    file_path = get_file_path()

    if not os.path.exists(file_path) or force_init:
        with open(file_path, "w") as file:
            json.dump(
                {
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
                },
                file,
            )
    else:
        data = load_summary_data()

        if "total_messages" not in data:
            data["total_messages"] = 0
        with open(file_path, "w") as file:
            json.dump(data, file)


def update_summary_data(key, channel_id=None, user_id=None, remove=False):
    file_path = get_file_path()

    data = load_summary_data()

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
    with open(file_path, "w") as file:
        json.dump(data, file)


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
    current_date = start_date
    while current_date <= end_date:
        file_path = SUMMARY_DATA_FILE.format(date=current_date.strftime("%Y-%m-%d"))
        if os.path.exists(file_path):
            try:
                with open(file_path, "r") as file:
                    daily_data = json.load(file)
            except (json.JSONDecodeError, ValueError) as e:
                print(f"Warning: Corrupted or empty JSON file: {file_path}. Skipping. Error: {e}")
                current_date += timedelta(days=1)
                continue
            for key in aggregated_data.keys():
                if key in ["messages", "active_members", "reacting_members"]:
                    for sub_key, count in daily_data.get(key, {}).items():
                        if sub_key not in aggregated_data[key]:
                            aggregated_data[key][sub_key] = 0
                        aggregated_data[key][sub_key] += count
                elif key == "total_members":
                    aggregated_data["total_members"] = daily_data.get(
                        "total_members", 0
                    )
                else:
                    aggregated_data[key] += daily_data.get(key, 0)
        current_date += timedelta(days=1)
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
            file_path = SUMMARY_DATA_FILE.format(date=date)
            try:
                with open(file_path, "r") as file:
                    data = json.load(file)
            except (json.JSONDecodeError, ValueError):
                if log_channel:
                    await log_channel.send(f"⚠️ Could not load summary data for {date_dd_mm_yyyy} (file corrupted or empty).")
                return
            previous_date_obj = date_obj - timedelta(days=1)
            previous_date = previous_date_obj.strftime("%Y-%m-%d")
            previous_file_path = SUMMARY_DATA_FILE.format(date=previous_date)
            title = f"Daily Server Summary - {date_dd_mm_yyyy}"
            title_color = "#7289da"
            if os.path.exists(previous_file_path):
                try:
                    with open(previous_file_path, "r") as previous_file:
                        previous_data = json.load(previous_file)
                except (json.JSONDecodeError, ValueError):
                    previous_data = None
            else:
                previous_data = None

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
        image_buffer = await create_summary_image(summary_data, title, title_color)
        await log_channel.send(
            file=discord.File(image_buffer, filename=f"{frequency}_summary.png")
        )
        if frequency == "daily":
            data["total_members"] = total_members
            with open(file_path, "w") as file:
                json.dump(data, file)
