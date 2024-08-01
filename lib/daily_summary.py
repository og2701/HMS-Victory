import os
import json
from datetime import datetime
import discord
from lib.daily_summary_html import create_daily_summary_image

SUMMARY_DATA_FILE = "daily_summary.json"

def initialize_summary_data():
    if not os.path.exists(SUMMARY_DATA_FILE):
        with open(SUMMARY_DATA_FILE, "w") as file:
            json.dump({
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
        with open(SUMMARY_DATA_FILE, "r") as file:
            data = json.load(file)
        if "total_messages" not in data:
            data["total_messages"] = 0
        with open(SUMMARY_DATA_FILE, "w") as file:
            json.dump(data, file)

def update_summary_data(key, channel_id=None, user_id=None, remove=False):
    with open(SUMMARY_DATA_FILE, "r") as file:
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

    with open(SUMMARY_DATA_FILE, "w") as file:
        json.dump(data, file)

def reset_summary_data():
    with open(SUMMARY_DATA_FILE, "w") as file:
        json.dump({
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

async def post_daily_summary(client, log_channel_id):
    log_channel = client.get_channel(log_channel_id)
    if log_channel is not None:
        with open(SUMMARY_DATA_FILE, "r") as file:
            data = json.load(file)

        guild = log_channel.guild
        total_members = guild.member_count
        active_members = sorted(data.get("active_members", {}).items(), key=lambda x: x[1], reverse=True)[:5]
        reacting_members = sorted(data.get("reacting_members", {}).items(), key=lambda x: x[1], reverse=True)[:5]
        top_channels = sorted(data.get("messages", {}).items(), key=lambda x: x[1], reverse=True)[:5]

        summary_data = {
            "total_members": total_members,
            "members_joined": data["members_joined"],
            "members_left": data["members_left"],
            "members_banned": data["members_banned"],
            "total_messages": data["total_messages"],
            "reactions_added": data["reactions_added"],
            "reactions_removed": data["reactions_removed"],
            "deleted_messages": data["deleted_messages"],
            "boosters_gained": data["boosters_gained"],
            "boosters_lost": data["boosters_lost"],
            "top_channels": [(log_channel.guild.get_channel(int(channel_id)).name, count) for channel_id, count in top_channels],
            "active_members": [(guild.get_member(int(user_id)).display_name, count) for user_id, count in active_members],
            "reacting_members": [(guild.get_member(int(user_id)).display_name, count) for user_id, count in reacting_members]
        }

        image_path = await create_daily_summary_image(summary_data, "Daily Server Summary")

        try:
            with open(image_path, "rb") as f:
                await log_channel.send(file=discord.File(f, "daily_summary.png"))
        finally:
            os.remove(image_path)
