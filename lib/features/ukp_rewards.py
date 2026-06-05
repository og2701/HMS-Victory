"""UKP earning rewards funded from the house bank.

Four ways to earn beyond chat activity:
  - Tree watering: the Grow-a-Tree bot's "thanks for watering" posts pay the waterer.
  - /benefits: a once-a-day handout for players under a balance threshold.
  - Hall of Fame: a message reaching the HoF DMs its author a reward.
  - Tickets: staff can grant a support ticket's opener a payout from the close summary.

Every payout goes through add_bb (bank -> player), so the 800k supply is conserved (no
minting); add_bb returns False only if the bank is somehow insolvent.
"""

import logging
import random
import re
from datetime import datetime, timedelta

import pytz
import discord

import config
from config import ROLES
from lib.economy.economy_manager import add_bb, get_bb
from lib.core.file_operations import load_json_file, save_json_file

log = logging.getLogger(__name__)
_UK = pytz.timezone("Europe/London")

_STAFF_ROLES = {ROLES.DEPUTY_PM, ROLES.MINISTER, ROLES.CABINET, ROLES.BORDER_FORCE, ROLES.PCSO}


def _today() -> str:
    return datetime.now(_UK).strftime("%Y-%m-%d")


def _next_uk_midnight_ts() -> int:
    now = datetime.now(_UK)
    nxt = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(nxt.timestamp())


def _pay(user_id: int, amount: int, reason: str) -> bool:
    """Pay a player from the bank. Returns True on success."""
    try:
        return add_bb(int(user_id), int(amount), reason=reason)
    except Exception:
        log.error("UKP reward pay failed (%s)", reason, exc_info=True)
        return False


def _is_staff(member) -> bool:
    return hasattr(member, "roles") and any(r.id in _STAFF_ROLES for r in member.roles)


# ---------------------------------------------------------------------------
# Hall of Fame
# ---------------------------------------------------------------------------
async def award_hof_reward(client, user_id: int):
    if not user_id:
        return
    amount = getattr(config, "HOF_REWARD", 100)
    if not _pay(user_id, amount, "Hall of Fame reward"):
        return
    try:
        user = client.get_user(int(user_id)) or await client.fetch_user(int(user_id))
        await user.send(
            f"\U0001f3c6 Your message just made it into the **Hall of Fame** — "
            f"here's **{amount:,} UKPence** to go with the glory. Well earned!"
        )
    except Exception:
        log.debug("HoF reward DM failed", exc_info=True)


# ---------------------------------------------------------------------------
# Tree watering
# ---------------------------------------------------------------------------
_WATER_RE = re.compile(r"Thanks <@!?(\d+)> for watering the tree", re.IGNORECASE)


async def handle_tree_watering(client, message):
    """Pay the waterer when the Grow-a-Tree bot posts a 'thanks for watering' embed.
    Daily-capped per user to stop someone camping the tree for UKP."""
    if message.author.id != getattr(config, "GROW_A_TREE_BOT_ID", 0):
        return
    waterer_id = None
    for e in message.embeds:
        m = _WATER_RE.search(f"{e.description or ''} {e.title or ''}")
        if m:
            waterer_id = int(m.group(1))
            break
    if not waterer_id:
        return

    reward = getattr(config, "TREE_WATER_REWARD", 20)
    cap = getattr(config, "TREE_WATER_DAILY_CAP", 200)
    store = load_json_file(config.TREE_WATER_FILE) or {}
    today = _today()
    rec = store.get(str(waterer_id))
    earned = rec["earned"] if (rec and rec.get("date") == today) else 0
    if earned >= cap:
        return  # hit the daily cap; pay nothing more today
    pay_amt = min(reward, cap - earned)
    if not _pay(waterer_id, pay_amt, "Tree watering reward"):
        return
    store[str(waterer_id)] = {"date": today, "earned": earned + pay_amt}
    save_json_file(config.TREE_WATER_FILE, store)
    try:
        await message.channel.send(
            f"\U0001f333 <@{waterer_id}> earned **{pay_amt:,} UKPence** for watering the tree!",
            allowed_mentions=discord.AllowedMentions(users=True),
        )
    except Exception:
        log.debug("tree watering message failed", exc_info=True)


# ---------------------------------------------------------------------------
# /benefits
# ---------------------------------------------------------------------------
async def handle_benefits_command(interaction):
    uid = interaction.user.id
    bal = get_bb(uid)
    threshold = getattr(config, "BENEFITS_THRESHOLD", 250)
    if bal >= threshold:
        await interaction.response.send_message(
            f"\U0001f4bc You're not eligible — you've got **{bal:,} UKPence** "
            f"(benefits are for those under {threshold:,}). Back to work."
        )
        return

    store = load_json_file(config.BENEFITS_FILE) or {}
    today = _today()
    if store.get(str(uid)) == today:
        await interaction.response.send_message(
            f"\U0001f9fe You've already had your benefits today. The office reopens at "
            f"midnight UK <t:{_next_uk_midnight_ts()}:R>."
        )
        return

    # One claim per UK calendar day; resets at midnight.
    store[str(uid)] = today
    save_json_file(config.BENEFITS_FILE, store)

    amount = random.randint(getattr(config, "BENEFITS_MIN", 30), getattr(config, "BENEFITS_MAX", 75))
    if not _pay(uid, amount, "Benefits payment"):
        await interaction.response.send_message("\U0001f9fe The benefits office is shut right now — try later.")
        return
    await interaction.response.send_message(
        f"\U0001f9fe **Benefits approved!** <@{uid}> receives **{amount:,} UKPence** from the state. "
        f"Spend it wisely (or at the casino)."
    )


# ---------------------------------------------------------------------------
# Ticket reward (staff-granted from the close summary)
# ---------------------------------------------------------------------------
async def grant_ticket_reward(client, creator_id, creator_name=None) -> bool:
    if not creator_id:
        return False
    amount = getattr(config, "TICKET_REWARD", 100)
    if not _pay(creator_id, amount, "Ticket reward"):
        return False
    try:
        user = client.get_user(int(creator_id)) or await client.fetch_user(int(creator_id))
        await user.send(
            f"\U0001f3ab Thanks for using support! A staff member has awarded you "
            f"**{amount:,} UKPence** for your ticket."
        )
    except Exception:
        log.debug("ticket reward DM failed", exc_info=True)
    return True


class TicketRewardView(discord.ui.View):
    """Award / Skip buttons posted under a closed-ticket summary (staff only)."""

    def __init__(self, creator_id, creator_name=None):
        super().__init__(timeout=None)
        self.creator_id = creator_id
        self.creator_name = creator_name

    @discord.ui.button(label="Award 100 UKP", style=discord.ButtonStyle.success,
                       emoji="\U0001f4b7", custom_id="ticket_reward:award")
    async def award(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _is_staff(interaction.user):
            await interaction.response.send_message("Only staff can decide this.", ephemeral=True)
            return
        ok = await grant_ticket_reward(interaction.client, self.creator_id, self.creator_name)
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        who = self.creator_name or f"<@{self.creator_id}>"
        amount = getattr(config, "TICKET_REWARD", 100)
        msg = (f"✅ **{who}** was awarded **{amount:,} UKPence** by {interaction.user.display_name}."
               if ok else "⚠️ Could not award (no creator found or bank issue).")
        await interaction.followup.send(msg)

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.secondary,
                       custom_id="ticket_reward:skip")
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _is_staff(interaction.user):
            await interaction.response.send_message("Only staff can decide this.", ephemeral=True)
            return
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(f"No reward granted (skipped by {interaction.user.display_name}).")
