import discord
import json
import os
import io
import uuid
from datetime import datetime, timedelta
import pytz
from PIL import Image, ImageChops
from html2image import Html2Image

from lib.ukpence import _load as load_ukpence_data
from config import CHROME_PATH

try:
    from lib.settings import ECONOMY_METRICS_FILE
except ImportError:
    ECONOMY_METRICS_FILE = "economy_metrics.json"

hti = Html2Image(output_path=".", browser_executable=CHROME_PATH)
hti.browser.flags += [
    "--force-device-scale-factor=2",
    "--disable-gpu",
    "--disable-software-rasterizer",
    "--no-sandbox"
]


def trim(im: Image.Image) -> Image.Image:
    bg = Image.new(im.mode, im.size, im.getpixel((0, 0)) if im.mode == 'RGBA' else (255,255,255))
    diff = ImageChops.difference(im, bg)
    if im.mode == 'RGBA':
        alpha = im.split()[-1]
        diff = ImageChops.add(diff, diff, 2.0, -100)
        diff = ImageChops.multiply(diff, alpha)
    else:
        diff = ImageChops.add(diff, diff, 2.0, -100)
    bbox = diff.getbbox()
    return im.crop(bbox) if bbox else im


def get_economy_metrics_for_day(date_str: str):
    if not os.path.exists(ECONOMY_METRICS_FILE):
        return {}
    try:
        with open(ECONOMY_METRICS_FILE, "r") as f:
            all_metrics = json.load(f)
        return all_metrics.get(date_str, {})
    except (json.JSONDecodeError, FileNotFoundError):
        return {}

async def create_economy_stats_image(guild: discord.Guild) -> str:
    ukpence_data = load_ukpence_data()
    
    total_ukpence = sum(ukpence_data.values())
    num_holders = len(ukpence_data)
    average_ukpence = total_ukpence / num_holders if num_holders > 0 else 0
    
    actual_holders_with_balance = sum(1 for balance in ukpence_data.values() if balance > 0)
    average_ukpence_active = total_ukpence / actual_holders_with_balance if actual_holders_with_balance > 0 else 0

    sorted_balances = sorted(ukpence_data.items(), key=lambda item: item[1], reverse=True)
    top_5_richest = sorted_balances[:5]

    dist_brackets = {
        "1-1,000 UKP": 0, "1,001-10,000 UKP": 0,
        "10,001-100,000 UKP": 0, "100,001+ UKP": 0,
        "Zero Balance (in file)": 0 
    }
    for balance in ukpence_data.values():
        if balance == 0: dist_brackets["Zero Balance (in file)"] +=1
        elif 1 <= balance <= 1000: dist_brackets["1-1,000 UKP"] += 1
        elif 1001 <= balance <= 10000: dist_brackets["1,001-10,000 UKP"] += 1
        elif 10001 <= balance <= 100000: dist_brackets["10,001-100,000 UKP"] += 1
        else: dist_brackets["100,001+ UKP"] += 1

    uk_timezone = pytz.timezone("Europe/London")
    yesterday_dt = datetime.now(uk_timezone) - timedelta(days=1)
    yesterday_str_key = yesterday_dt.strftime("%Y-%m-%d")
    
    yesterday_metrics = get_economy_metrics_for_day(yesterday_str_key)
    chat_rewards_yesterday = yesterday_metrics.get("chat_rewards_total", "N/A")
    booster_rewards_yesterday = yesterday_metrics.get("booster_rewards_total", "N/A")
    total_circulation_eod_yesterday = yesterday_metrics.get("total_circulation_end_of_day")

    economy_growth_percentage_str = "N/A"
    growth_class = "growth-neutral"
    if total_circulation_eod_yesterday not in [None, "N/A"] and total_ukpence is not None:
        if total_circulation_eod_yesterday > 0:
            growth = ((total_ukpence - total_circulation_eod_yesterday) / total_circulation_eod_yesterday) * 100
            economy_growth_percentage_str = f"{growth:+.2f}%"
            if growth > 0: growth_class = "growth-positive"
            elif growth < 0: growth_class = "growth-negative"
        elif total_ukpence > 0 :
             economy_growth_percentage_str = "+∞%"
             growth_class = "growth-positive"


    top_richest_html_parts = []
    for i, (user_id_str, balance) in enumerate(top_5_richest):
        member_display_name = f"User ID {user_id_str}"
        if guild:
            member = guild.get_member(int(user_id_str))
            if member:
                member_display_name = member.display_name
        top_richest_html_parts.append(f"<li><span class='rank'>#{i+1}</span> <span class='name'>{discord.utils.escape_markdown(member_display_name)}</span> <span class='balance'>{balance:,} UKP</span></li>")
    top_richest_users_html = "\n".join(top_richest_html_parts) if top_richest_html_parts else "<li>No UKPence data.</li>"

    distribution_html_parts = [f"<li><span class='name'>{bracket}</span> <span class='balance'>{count} users</span></li>" for bracket, count in dist_brackets.items() if count > 0]
    distribution_html = "\n".join(distribution_html_parts) if distribution_html_parts else "<li>No distribution data.</li>"

    with open("templates/economy_stats.html", "r", encoding="utf-8") as f:
        html_template = f.read()
    
    formatted_html = html_template.format(
        total_ukpence=f"{total_ukpence:,}",
        num_holders=str(num_holders),
        average_ukpence=f"{average_ukpence:,.2f}",
        average_ukpence_active=f"{average_ukpence_active:,.2f}",
        economy_growth_percentage=economy_growth_percentage_str,
        growth_class=growth_class,
        yesterday_date=yesterday_dt.strftime("%d %B %Y"),
        chat_rewards_yesterday=str(chat_rewards_yesterday),
        booster_rewards_yesterday=str(booster_rewards_yesterday),
        top_richest_users_html=top_richest_users_html,
        distribution_html=distribution_html,
        current_datetime_uk=datetime.now(uk_timezone).strftime("%d %B %Y, %H:%M:%S %Z")
    )

    image_filename = f"{uuid.uuid4()}.png"
    hti.screenshot(html_str=formatted_html, save_as=image_filename, size=(750, 1))
    
    try:
        img = Image.open(image_filename)
        img = trim(img) 
        img.save(image_filename)
    except Exception as e:
        print(f"Error trimming image {image_filename}: {e}")

    return image_filename