import discord
from discord import Embed, File
import json
import os
from datetime import datetime, timedelta
import pytz
from html2image import Html2Image
import uuid
from PIL import Image
from lib.utils import trim
from lib.settings import (
    get_channel_summary_settings,
    get_user_summary_settings,
    save_user_summary_settings,
    save_channel_summary_settings,
)
from config import CHROME_PATH

hti = Html2Image(output_path=".", browser_executable=CHROME_PATH)
hti.browser.flags += [
    "--force-device-scale-factor=2",
    "--disable-gpu",
    "--disable-software-rasterizer",
    "--no-sandbox",
]


def get_summary_period(summary_type, date_str):
    uk_timezone = pytz.timezone("Europe/London")
    date_obj = datetime.strptime(date_str, "%Y-%m-%d").astimezone(uk_timezone)

    if summary_type == "daily":
        start_date = date_obj
        end_date = start_date + timedelta(days=1)
        period_str = start_date.strftime("%A, %B %d, %Y")
    elif summary_type == "weekly":
        start_of_week = date_obj - timedelta(days=date_obj.weekday())
        end_of_week = start_of_week + timedelta(days=7)
        start_date = start_of_week
        end_date = end_of_week
        period_str = f"{start_of_week.strftime('%B %d')} to {end_of_week.strftime('%B %d, %Y')}"
    elif summary_type == "monthly":
        start_of_month = date_obj.replace(day=1)
        next_month = (start_of_month.replace(day=28) + timedelta(days=4)).replace(day=1)
        end_of_month = next_month
        start_date = start_of_month
        end_date = end_of_month
        period_str = start_date.strftime("%B %Y")
    else:
        return None, None, "Invalid summary type"

    return start_date, end_date, period_str


def create_summary_from_template(period_str, server_name, summary_type, summary_data):
    with open("templates/summary.html", "r", encoding="utf-8") as file:
        html_template = file.read()

    html_content = (
        html_template.replace("{period}", period_str)
        .replace("{server_name}", server_name)
        .replace("{summary_type}", summary_type.title())
    )

    for section, content in summary_data.items():
        placeholder = f"{{{section}}}"
        html_content = html_content.replace(placeholder, content)

    for section in [
        "top_channels",
        "top_users",
        "top_reaccs",
        "top_reaccs_received",
        "hot_topics",
        "new_members",
        "vc_time",
        "top_stickers",
    ]:
        html_content = html_content.replace(f"{{{section}}}", "")

    return html_content


async def post_summary(client, channel_id, summary_type, interaction_channel, date_str=None):
    if date_str is None:
        uk_timezone = pytz.timezone("Europe/London")
        date_str = datetime.now(uk_timezone).strftime("%Y-%m-%d")

    summary_file_path = f"daily_summaries/daily_summary_{date_str}.json"
    if not os.path.exists(summary_file_path):
        print(f"Summary file not found for date: {date_str}")
        return

    with open(summary_file_path, "r") as f:
        data = json.load(f)

    _, _, period_str = get_summary_period(summary_type, date_str)
    summary_data = {
        "top_channels": "<li>"
        + "</li><li>".join([f"#{channel} ({count} messages)" for channel, count in data["top_channels"]])
        + "</li>",
        "top_users": "<li>"
        + "</li><li>".join([f"{user} ({count} messages)" for user, count in data["top_users"]])
        + "</li>",
        "top_reaccs": "<li>"
        + "</li><li>".join(
            [f"{user} ({count} reactions given)" for user, count in data["top_reaccs_given"]]
        )
        + "</li>",
        "top_reaccs_received": "<li>"
        + "</li><li>".join(
            [f"{user} ({count} reactions received)" for user, count in data["top_reaccs_received"]]
        )
        + "</li>",
        "hot_topics": "<p>" + data["summary_text"] + "</p>",
        "new_members": "<li>" + "</li><li>".join(data["new_members"]) + "</li>",
        "vc_time": "<li>"
        + "</li><li>".join([f"{user} ({time})" for user, time in data["vc_time"]])
        + "</li>",
        "top_stickers": "".join(
            [
                f'<div class="sticker"><img src="{url}" class="sticker-image"><span class="sticker-name">{name}</span><span class="sticker-count">{count}</span></div>'
                for name, url, count in data["top_stickers"]
            ]
        ),
    }

    html_content = create_summary_from_template(
        period_str, interaction_channel.guild.name, summary_type, summary_data
    )

    output_filename = f"{uuid.uuid4()}.png"
    hti.screenshot(html_str=html_content, save_as=output_filename, size=(1600, 1600))
    image = trim(Image.open(output_filename))
    image.save(output_filename)

    channel = client.get_channel(channel_id)
    await channel.send(file=File(output_filename, "summary.png"))
    os.remove(output_filename)


class SummarySettingsModal(discord.ui.Modal):
    def __init__(self, settings_type, item_id, current_settings):
        super().__init__(title=f"Editing settings for {settings_type}")
        self.settings_type = settings_type
        self.item_id = item_id

        self.ignore_in_summaries = discord.ui.TextInput(
            label="Ignore in summaries (True/False)",
            default=str(current_settings.get("ignore_in_summaries", False)),
            max_length=5,
        )
        self.add_item(self.ignore_in_summaries)

        if self.settings_type == "user":
            self.appear_in_top_users = discord.ui.TextInput(
                label="Appear in Top Users (True/False)",
                default=str(current_settings.get("appear_in_top_users", True)),
                max_length=5,
            )
            self.add_item(self.appear_in_top_users)

    async def on_submit(self, interaction: discord.Interaction):
        new_settings = {
            "ignore_in_summaries": self.ignore_in_summaries.value.lower() == "true",
        }
        if self.settings_type == "user":
            new_settings["appear_in_top_users"] = self.appear_in_top_users.value.lower() == "true"
            save_user_summary_settings(self.item_id, new_settings)
        else:
            save_channel_summary_settings(self.item_id, new_settings)

        await interaction.response.send_message(
            f"Settings for {self.item_id} have been updated.", ephemeral=True
        )