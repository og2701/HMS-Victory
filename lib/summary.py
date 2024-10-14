import os
import json
from datetime import datetime, timedelta
import discord
import pytz

from lib.summary_html import create_summary_image
from lib.settings import *


SUMMARY_DATA_FILE = "daily_summaries/daily_summary_{date}.json"

def initialize_summary_data():
    uk_timezone = pytz.timezone("Europe/London")
    date = datetime.now(uk_timezone).strftime("%Y-%m-%d")
    file_path = SUMMARY_DATA_FILE.format(date=date)
    
    if not os.path.exists(file_path):
        with open(file_path, "w") as file:
            json.dump({
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
                "reacting_members": {}
            }, file)
    else:
        with open(file_path, "r") as file:
            data = json.load(file)
        if "total_messages" not in data:
            data["total_messages"] = 0
        with open(file_path, "w") as file:
            json.dump(data, file)


def update_summary_data(key, channel_id=None, user_id=None, remove=False):
    uk_timezone = pytz.timezone("Europe/London")
    date = datetime.now(uk_timezone).strftime("%Y-%m-%d")
    file_path = SUMMARY_DATA_FILE.format(date=date)
    with open(file_path, "r") as file:
        data = json.load(file)

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
        "reacting_members": {}
    }

    current_date = start_date
    while current_date <= end_date:
        file_path = SUMMARY_DATA_FILE.format(date=current_date.strftime("%Y-%m-%d"))
        if os.path.exists(file_path):
            with open(file_path, "r") as file:
                daily_data = json.load(file)

            for key in aggregated_data.keys():
                if key in ["messages", "active_members", "reacting_members"]:
                    for sub_key, count in daily_data.get(key, {}).items():
                        if sub_key not in aggregated_data[key]:
                            aggregated_data[key][sub_key] = 0
                        aggregated_data[key][sub_key] += count
                else:
                    aggregated_data[key] += daily_data.get(key, 0)

        current_date += timedelta(days=1)

    return aggregated_data

async def post_summary(client, log_channel_id, frequency, channel_override=None, date=None):
    log_channel = client.get_channel(log_channel_id) if channel_override is None else channel_override
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
            file_path = SUMMARY_DATA_FILE.format(date=date)
            with open(file_path, "r") as file:
                data = json.load(file)
            title_color = "#7289da"  # Blue

            previous_date = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
            previous_file_path = SUMMARY_DATA_FILE.format(date=previous_date)
            if os.path.exists(previous_file_path):
                with open(previous_file_path, "r") as previous_file:
                    previous_data = json.load(previous_file)
                member_change = total_members - previous_data["total_members"]
                member_change_str = f" (+{member_change})" if member_change > 0 else f" ({member_change})"
                member_change_color = "green" if member_change > 0 else "red"
                
                for channel_id, count in data["messages"].items():
                    prev_count = previous_data["messages"].get(channel_id, 0)
                    message_change = count - prev_count
                    message_change_str[channel_id] = f" (+{message_change})" if message_change > 0 else f" ({message_change})"
                    message_change_color[channel_id] = "green" if message_change > 0 else "red"
                
                total_message_change = data["total_messages"] - previous_data["total_messages"]
                total_message_change_str = f" (+{total_message_change})" if total_message_change > 0 else f" ({total_message_change})"
                total_message_change_color = "green" if total_message_change > 0 else "red"
            else:
                member_change_str = ""
                member_change_color = "white"
                total_message_change_str = ""
                total_message_change_color = "white"
        elif frequency == "weekly":
            end_date = datetime.strptime(date, "%Y-%m-%d")
            start_date = end_date - timedelta(days=6)
            data = aggregate_summaries(start_date, end_date)
            title_color = "#7CFC00"  # Light Green
        elif frequency == "monthly":
            end_date = datetime.strptime(date, "%Y-%m-%d").replace(day=1) - timedelta(days=1)
            start_date = end_date.replace(day=1)
            data = aggregate_summaries(start_date, end_date)
            title_color = "#FFD700"  # Yellow

        top_n = 5 if frequency == "daily" else 10
        
        active_members = sorted(data.get("active_members", {}).items(), key=lambda x: x[1], reverse=True)[:top_n]
        reacting_members = sorted(data.get("reacting_members", {}).items(), key=lambda x: x[1], reverse=True)[:top_n]
        top_channels = sorted(data.get("messages", {}).items(), key=lambda x: x[1], reverse=True)[:top_n]

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
            "top_channels": [(log_channel.guild.get_channel(int(channel_id)).name if log_channel.guild.get_channel(int(channel_id)) else "Unknown Channel", f"{count} <span style='color: {message_change_color.get(channel_id, 'white')};'>{message_change_str.get(channel_id, '')}</span>") for channel_id, count in top_channels],
            "active_members": [(guild.get_member(int(user_id)).display_name if guild.get_member(int(user_id)) else "Unknown Member", count) for user_id, count in active_members],
            "reacting_members": [(guild.get_member(int(user_id)).display_name if guild.get_member(int(user_id)) else "Unknown Member", count) for user_id, count in reacting_members]
        }

        title = f"{frequency.capitalize()} Server Summary"
        image_path = await create_summary_image(summary_data, title, title_color)

        try:
            with open(image_path, "rb") as f:
                await log_channel.send(file=discord.File(f, f"{frequency}_summary.png"))
        finally:
            os.remove(image_path)

        if frequency == "daily":
            data["total_members"] = total_members
            with open(file_path, "w") as file:
                json.dump(data, file)