import discord
import json
import os
from datetime import datetime, timedelta
import pytz
from lib.image_processing import screenshot_html

from lib.economy_manager import get_all_balances as load_ukpence_data

try:
    from config import ECONOMY_METRICS_FILE, BALANCE_SNAPSHOT_DIR
except ImportError:
    ECONOMY_METRICS_FILE = "economy_metrics.json"
    BALANCE_SNAPSHOT_DIR = "balance_snapshots"


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

async def create_economy_stats_image(guild: discord.Guild) -> str:
    ukpence_data_current = load_ukpence_data()
    
    total_ukpence = sum(ukpence_data_current.values())
    num_holders = len(ukpence_data_current)
    average_ukpence = total_ukpence / num_holders if num_holders > 0 else 0
    
    actual_holders_with_balance = sum(1 for balance in ukpence_data_current.values() if balance > 0)
    average_ukpence_active = total_ukpence / actual_holders_with_balance if actual_holders_with_balance > 0 else 0

    balances_for_median_calc = [b for b in ukpence_data_current.values() if b > 500]
    median_ukpence_balance = 0 
    
    if balances_for_median_calc:
        balances_for_median_calc.sort()
        n = len(balances_for_median_calc)
        mid = n // 2
        if n % 2 == 1:
            median_ukpence_balance = balances_for_median_calc[mid]
        else:
            median_ukpence_balance = (balances_for_median_calc[mid - 1] + balances_for_median_calc[mid]) / 2.0

    sorted_balances_for_top = sorted(ukpence_data_current.items(), key=lambda item: item[1], reverse=True)
    top_5_richest = sorted_balances_for_top[:5]

    num_top_users_for_concentration = 5 
    top_n_balances_values = [balance for _, balance in sorted_balances_for_top[:num_top_users_for_concentration]]
    wealth_of_top_n = sum(top_n_balances_values)
    percentage_held_by_top_n = (wealth_of_top_n / total_ukpence * 100) if total_ukpence > 0 else 0
    
    high_roller_threshold = 100000 
    high_rollers_count = sum(1 for balance in ukpence_data_current.values() if balance >= high_roller_threshold)

    uk_timezone = pytz.timezone("Europe/London")
    now_dt = datetime.now(uk_timezone)
    today_str_key = now_dt.strftime("%Y-%m-%d")
    yesterday_dt = now_dt - timedelta(days=1)
    yesterday_str_key = yesterday_dt.strftime("%Y-%m-%d")

    net_ukpence_change_absolute_str = "N/A"
    net_ukpence_change_class = "change-neutral"
    yesterday_balances_snapshot_data = load_balance_snapshot(yesterday_str_key)
    if yesterday_balances_snapshot_data:
        total_ukpence_yesterday_snapshot = sum(yesterday_balances_snapshot_data.values())
        net_change_value = total_ukpence - total_ukpence_yesterday_snapshot
        net_ukpence_change_absolute_str = f"{net_change_value:+,} UKP"
        if net_change_value > 0:
            net_ukpence_change_class = "change-positive"
        elif net_change_value < 0:
            net_ukpence_change_class = "change-negative"
    
    yesterday_metrics = get_daily_metrics(yesterday_str_key)
    chat_rewards_yesterday = yesterday_metrics.get("chat_rewards_total", 0)
    booster_rewards_yesterday = yesterday_metrics.get("booster_rewards_total", 0)
    stage_rewards_yesterday = yesterday_metrics.get("stage_rewards_total", 0)

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

    biggest_earner_name, biggest_earner_amount, biggest_earner_change_class = "N/A", "N/A", "change-neutral"
    biggest_loser_name, biggest_loser_amount, biggest_loser_change_class = "N/A", "N/A", "change-neutral"
    
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
        
    top_richest_html_parts = []
    for i, (user_id_str, balance) in enumerate(top_5_richest):
        member_display_name = f"User ID {user_id_str}"
        avatar_url = "https://cdn.discordapp.com/embed/avatars/0.png"

        if guild:
            member = guild.get_member(int(user_id_str))
            if member:
                member_display_name = discord.utils.escape_markdown(member.display_name)
                if member.display_avatar:
                    avatar_url = str(member.display_avatar.url)
                elif member.avatar:
                    avatar_url = str(member.avatar.url)

        top_richest_html_parts.append(
            f"<li>"
            f"<div class='user-details-left'>"
            f"<span class='rank'>#{i+1}</span>"
            f"<img src='{avatar_url}' class='list-avatar' alt='{member_display_name} avatar' />"
            f"<span class='name'>{member_display_name}</span>"
            f"</div>"
            f"<span class='balance'>{balance:,} UKP</span>"
            f"</li>"
        )
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
        
        chat_rewards_yesterday=f"{chat_rewards_yesterday:,}",
        booster_rewards_yesterday=f"{booster_rewards_yesterday:,}",
        stage_rewards_yesterday=f"{stage_rewards_yesterday:,}",

        biggest_earner_name=discord.utils.escape_markdown(str(biggest_earner_name)),
        biggest_earner_amount=str(biggest_earner_amount),
        biggest_earner_change_class=biggest_earner_change_class,
        biggest_loser_name=discord.utils.escape_markdown(str(biggest_loser_name)),
        biggest_loser_amount=str(biggest_loser_amount),
        biggest_loser_change_class=biggest_loser_change_class,
        
        top_richest_users_html=top_richest_users_html,
        distribution_html=distribution_html,
        current_datetime_uk=now_dt.strftime("%d %B %Y, %H:%M:%S %Z"),

        wealth_concentration_top_5_percentage=f"{percentage_held_by_top_n:.2f}%",
        wealth_concentration_top_5_amount=f"{wealth_of_top_n:,}",
        num_top_users_concentration=num_top_users_for_concentration,
        high_rollers_count=str(high_rollers_count),
        high_roller_threshold=f"{high_roller_threshold:,}",
        
        median_ukpence_balance=f"{median_ukpence_balance:,.2f}",
        net_ukpence_change_absolute_str=net_ukpence_change_absolute_str,
        net_ukpence_change_class=net_ukpence_change_class
    )

    return screenshot_html(formatted_html, size=(750, 2200))
