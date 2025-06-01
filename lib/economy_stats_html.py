import discord
import json
import os
import io
import uuid
from datetime import datetime, timedelta
import pytz
from PIL import Image, ImageChops
from html2image import Html2Image

from lib.ukpence import load_ukpence_data
from config import CHROME_PATH

try:
    from lib.settings import ECONOMY_METRICS_FILE, BALANCE_SNAPSHOT_DIR
except ImportError:
    ECONOMY_METRICS_FILE = "economy_metrics.json"
    BALANCE_SNAPSHOT_DIR = "balance_snapshots"


hti = Html2Image(output_path=".", browser_executable=CHROME_PATH)

def trim(image: Image.Image) -> Image.Image:
    if image.mode == "RGBA":
        bbox = image.getbbox()
    elif image.mode == "RGB":
        bg_color = image.getpixel((0, 0))
        bg_image = Image.new(image.mode, image.size, bg_color)
        diff_image = ImageChops.difference(image, bg_image)
        gray_diff = diff_image.convert('L')
        thresholded_diff = gray_diff.point(lambda x: 255 if x > 10 else 0)
        bbox = thresholded_diff.getbbox()
    else:
        temp_image = image.convert("RGBA")
        bbox = temp_image.getbbox()

    if bbox:
        return image.crop(bbox)
    else:
        return image


def get_daily_metrics(date_str: str):
    if not os.path.exists(ECONOMY_METRICS_FILE):
        return {}
    try:
        with open(ECONOMY_METRICS_FILE, "r") as f:
            all_metrics = json.load(f)
        return all_metrics.get(date_str, {})
    except (json.JSONDecodeError, FileNotFoundError):
        return {}

def load_balance_snapshot(date_str: str):
    snapshot_filename = f"ukpence_balances_{date_str}.json"
    snapshot_path = os.path.join(BALANCE_SNAPSHOT_DIR, snapshot_filename)
    if not os.path.exists(snapshot_path):
        return None
    try:
        with open(snapshot_path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return None

async def create_economy_stats_image(guild: discord.Guild) -> str | None:
    ukpence_data_current = load_ukpence_data()
    
    total_ukpence = sum(ukpence_data_current.values())
    num_holders = len(ukpence_data_current)
    average_ukpence = total_ukpence / num_holders if num_holders > 0 else 0
    
    actual_holders_with_balance = sum(1 for balance in ukpence_data_current.values() if balance > 0)
    average_ukpence_active = total_ukpence / actual_holders_with_balance if actual_holders_with_balance > 0 else 0

    sorted_balances = sorted(ukpence_data_current.items(), key=lambda item: item[1], reverse=True)
    top_5_richest = sorted_balances[:5]

    dist_brackets = {
        "1-1,000 UKP": 0, "1,001-10,000 UKP": 0,
        "10,001-100,000 UKP": 0, "100,001+ UKP": 0,
        "Zero Balance (in file)": 0 
    }
    for balance in ukpence_data_current.values():
        if balance == 0: dist_brackets["Zero Balance (in file)"] +=1
        elif 1 <= balance <= 1000: dist_brackets["1-1,000 UKP"] += 1
        elif 1001 <= balance <= 10000: dist_brackets["1,001-10,000 UKP"] += 1
        elif 10001 <= balance <= 100000: dist_brackets["10,001-100,000 UKP"] += 1
        else: dist_brackets["100,001+ UKP"] += 1

    uk_timezone = pytz.timezone("Europe/London")
    now_dt = datetime.now(uk_timezone)
    today_str_key = now_dt.strftime("%Y-%m-%d")
    yesterday_dt = now_dt - timedelta(days=1)
    yesterday_str_key = yesterday_dt.strftime("%Y-%m-%d")
    
    yesterday_metrics = get_daily_metrics(yesterday_str_key)
    chat_rewards_yesterday = yesterday_metrics.get("chat_rewards_total", 0)
    booster_rewards_yesterday = yesterday_metrics.get("booster_rewards_total", 0)
    stage_rewards_yesterday = yesterday_metrics.get("stage_rewards_total", 0)
    total_injected_yesterday = chat_rewards_yesterday + booster_rewards_yesterday + stage_rewards_yesterday

    today_metrics_start_of_day = get_daily_metrics(today_str_key)
    total_circulation_start_of_today = today_metrics_start_of_day.get("total_circulation_start_of_day")

    economy_growth_percentage_str = "N/A"
    growth_class = "growth-neutral"
    if total_circulation_start_of_today is not None and isinstance(total_circulation_start_of_today, (int, float)):
        if total_circulation_start_of_today > 0:
            growth = ((total_ukpence - total_circulation_start_of_today) / total_circulation_start_of_today) * 100
            economy_growth_percentage_str = f"{growth:+.2f}%"
            if growth > 0.005: growth_class = "growth-positive"
            elif growth < -0.005: growth_class = "growth-negative"
        elif total_ukpence > 0:
             economy_growth_percentage_str = "+∞%"
             growth_class = "growth-positive"
        else:
             economy_growth_percentage_str = "0.00%"

    biggest_earner_name_display = "N/A"
    biggest_earner_amount_display = "N/A"
    biggest_earner_class_display = "change-neutral"
    
    biggest_loser_name_display = "N/A"
    biggest_loser_amount_display = "N/A"
    biggest_loser_class_display = "change-neutral"

    earner_id = yesterday_metrics.get("top_event_earner_id")
    earner_amount = yesterday_metrics.get("top_event_earner_amount")
    if earner_id and earner_amount and earner_amount > 0:
        member = guild.get_member(int(earner_id))
        biggest_earner_name_display = member.display_name if member else f"User ID {earner_id}"
        biggest_earner_amount_display = f"+{earner_amount:,}"
        biggest_earner_class_display = "change-positive"
    
    loser_id = yesterday_metrics.get("top_event_loser_id")
    loser_amount = yesterday_metrics.get("top_event_loser_amount")
    if loser_id and loser_amount and loser_amount < 0:
        member = guild.get_member(int(loser_id))
        biggest_loser_name_display = member.display_name if member else f"User ID {loser_id}"
        biggest_loser_amount_display = f"{loser_amount:,}"
        biggest_loser_class_display = "change-negative"

    top_richest_html_parts = []
    for i, (user_id_str, balance) in enumerate(top_5_richest):
        member_display_name = f"User ID {user_id_str}"
        if guild:
            member = guild.get_member(int(user_id_str))
            if member: member_display_name = discord.utils.escape_markdown(member.display_name)
        top_richest_html_parts.append(f"<li><span class='rank'>#{i+1}</span> <span class='name'>{member_display_name}</span> <span class='balance'>{balance:,} UKP</span></li>")
    top_richest_users_html = "\n".join(top_richest_html_parts) if top_richest_html_parts else "<li>No UKPence data.</li>"

    distribution_html_parts = [f"<li><span class='name'>{bracket}</span> <span class='balance'>{count} users</span></li>" for bracket, count in dist_brackets.items() if count > 0]
    distribution_html = "\n".join(distribution_html_parts) if distribution_html_parts else "<li>No distribution data.</li>"

    html_template_path = os.path.join("templates", "economy_stats.html")
    if not os.path.exists(html_template_path):
        logger.error(f"HTML template not found at {html_template_path}")
        return None
        
    with open(html_template_path, "r", encoding="utf-8") as f:
        html_template = f.read()
    
    formatted_html = html_template.format(
        total_ukpence=f"{total_ukpence:,}",
        num_holders=str(num_holders),
        average_ukpence=f"{average_ukpence:,.2f}",
        average_ukpence_active=f"{average_ukpence_active:,.2f}",
        economy_growth_percentage=economy_growth_percentage_str,
        growth_class=growth_class,
        yesterday_date=yesterday_dt.strftime("%d %B %Y"),
        total_injected_yesterday=f"{total_injected_yesterday:,}",
        biggest_earner_name=discord.utils.escape_markdown(str(biggest_earner_name_display)),
        biggest_earner_amount=str(biggest_earner_amount_display),
        biggest_earner_change_class=biggest_earner_class_display,
        biggest_loser_name=discord.utils.escape_markdown(str(biggest_loser_name_display)),
        biggest_loser_amount=str(biggest_loser_amount_display),
        biggest_loser_change_class=biggest_loser_class_display,
        top_richest_users_html=top_richest_users_html,
        distribution_html=distribution_html,
        current_datetime_uk=now_dt.strftime("%d %B %Y, %H:%M:%S %Z")
    )

    image_filename = f"{uuid.uuid4()}.png"
    hti.screenshot(html_str=formatted_html, save_as=image_filename, size=(750, 1))

    try:
        img = Image.open(image_filename)
        img = trim(img) 
        img.save(image_filename)
    except Exception as e:
        logger.error(f"Error trimming image {image_filename}: {e}")
        
    return image_filename