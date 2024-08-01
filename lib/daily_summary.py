import os
import json
from datetime import datetime
import discord
from collections import defaultdict

SUMMARY_DATA_FILE = "daily_summary.json"

def initialize_summary_data():
    if not os.path.exists(SUMMARY_DATA_FILE):
        with open(SUMMARY_DATA_FILE, "w") as file:
            json.dump({
                "members_joined": 0,
                "members_left": 0,
                "members_banned": 0,
                "messages": {},
                "reactions_added": 0,
                "reactions_removed": 0,
                "deleted_messages": 0,
                "boosters_gained": 0,
                "boosters_lost": 0,
                "active_members": defaultdict(int),
                "reacted_messages": {},
                "reacting_members": defaultdict(int)
            }, file)

def update_summary_data(key, channel_id=None, user_id=None, message_id=None, message_content=None, author_nickname=None, remove=False):
    with open(SUMMARY_DATA_FILE, "r") as file:
        data = json.load(file)

    if key == "messages" and channel_id:
        if str(channel_id) not in data["messages"]:
            data["messages"][str(channel_id)] = 0
        data["messages"][str(channel_id)] += 1
    elif key == "active_members" and user_id:
        if str(user_id) not in data["active_members"]:
            data["active_members"][str(user_id)] = 0
        data["active_members"][str(user_id)] += 1
    elif key == "reacted_messages" and message_id:
        if str(message_id) not in data["reacted_messages"]:
            data["reacted_messages"][str(message_id)] = {"count": 0, "content": message_content, "author": author_nickname}
        data["reacted_messages"][str(message_id)]["count"] += 1 if not remove else -1
        if data["reacted_messages"][str(message_id)]["count"] <= 0:
            del data["reacted_messages"][str(message_id)]
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
            "reactions_added": 0,
            "reactions_removed": 0,
            "deleted_messages": 0,
            "boosters_gained": 0,
            "boosters_lost": 0,
            "active_members": defaultdict(int),
            "reacted_messages": {},
            "reacting_members": defaultdict(int)
        }, file)

async def post_daily_summary(client, log_channel_id):
    log_channel = client.get_channel(log_channel_id)
    if log_channel is not None:
        with open(SUMMARY_DATA_FILE, "r") as file:
            data = json.load(file)

        guild = log_channel.guild
        total_members = guild.member_count
        active_members = sorted(data.get("active_members", {}).items(), key=lambda x: x[1], reverse=True)[:5]
        reacted_messages = sorted(data.get("reacted_messages", {}).items(), key=lambda x: x[1]["count"], reverse=True)[:5]
        reacting_members = sorted(data.get("reacting_members", {}).items(), key=lambda x: x[1], reverse=True)[:5]

        embed = discord.Embed(
            title="Daily Server Summary",
            description=f"Here is the summary for {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}",
            color=discord.Color.blue()
        )
        embed.add_field(name="Total Members", value=total_members, inline=False)
        embed.add_field(name="Members Joined", value=data["members_joined"], inline=False)
        embed.add_field(name="Members Left", value=f"{data['members_left']} ({data['members_banned']} banned)", inline=False)
        embed.add_field(name="Reactions Added/Removed", value=f"{data['reactions_added']} / {data['reactions_removed']}", inline=False)
        embed.add_field(name="Deleted Messages", value=data["deleted_messages"], inline=False)
        embed.add_field(name="Boosters (New/Lost)", value=f"{data['boosters_gained']} / {data['boosters_lost']}", inline=False)
        
        top_channels = sorted(data.get("messages", {}).items(), key=lambda x: x[1], reverse=True)[:5]
        if top_channels:
            top_channels_str = "\n".join([f"<#{channel_id}>: {count} messages" for channel_id, count in top_channels])
            embed.add_field(name="Top 5 Active Channels", value=top_channels_str, inline=False)

        if active_members:
            top_members_str = "\n".join([f"<@{user_id}>: {count} messages" for user_id, count in active_members])
            embed.add_field(name="Top 5 Active Members", value=top_members_str, inline=False)
        
        if reacted_messages:
            top_reacted_messages = []
            for message_id, msg_info in reacted_messages:
                if msg_info:
                    content = msg_info.get("content", "")
                    author = msg_info.get("author", "Unknown")
                    count = msg_info.get("count", 0)
                    top_reacted_messages.append(f"{content[:50]} by {author}: {count} reactions")
            top_reacted_messages_str = "\n".join(top_reacted_messages)
            embed.add_field(name="Top 5 Most Reacted Messages", value=top_reacted_messages_str, inline=False)
        
        if reacting_members:
            top_reacting_members_str = "\n".join([f"<@{user_id}>: {count} reactions" for user_id, count in reacting_members])
            embed.add_field(name="Top 5 Reacting Members", value=top_reacting_members_str, inline=False)

        await log_channel.send(embed=embed)
