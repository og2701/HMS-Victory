from lib.daily_summary_html import create_summary_image

async def post_summary(client, log_channel_id, frequency):
    log_channel = client.get_channel(log_channel_id)
    if log_channel is not None:
        if frequency == "daily":
            date = datetime.now().strftime("%Y-%m-%d")
            file_path = SUMMARY_DATA_FILE.format(date=date)
            with open(file_path, "r") as file:
                data = json.load(file)
        else:
            if frequency == "weekly":
                end_date = datetime.now()
                start_date = end_date - timedelta(days=end_date.weekday() + 1)
            elif frequency == "monthly":
                end_date = datetime.now()
                start_date = end_date.replace(day=1)
            data = aggregate_summaries(start_date, end_date)

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

        title = f"{frequency.capitalize()} Server Summary"
        image_path = await create_summary_image(summary_data, title)

        role = guild.get_role(DEPUTY_PM_ROLE_ID)
        if role:
            role_mention = role.mention
        else:
            role_mention = ""

        try:
            with open(image_path, "rb") as f:
                await log_channel.send(content=f"{role_mention}", file=discord.File(f, f"{frequency}_summary.png"))
        finally:
            os.remove(image_path)
