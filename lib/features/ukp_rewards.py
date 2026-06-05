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
import time
from datetime import datetime, timedelta

import pytz
import discord

import config
from config import ROLES
from database import DatabaseManager
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
            f"\U0001f3c6 Your message just made it into the **Hall of Fame** - "
            f"here's **{amount:,} UKPence** to go with the glory. Well earned!"
        )
    except Exception:
        log.debug("HoF reward DM failed", exc_info=True)


# ---------------------------------------------------------------------------
# Tree watering
# ---------------------------------------------------------------------------
_WATER_RE = re.compile(r"Thanks <@!?(\d+)> for watering the tree", re.IGNORECASE)
_HEIGHT_RE = re.compile(r"tree is ([\d,]+(?:\.\d+)?)\s*ft tall", re.IGNORECASE)


def _tree_reward(water_index: int) -> int:
    """Reward for a user's Nth water of the day (1-based): full rate for the first few
    waters, then -1 per water down to a floor of 1. Resets daily."""
    start = getattr(config, "TREE_WATER_REWARD", 20)
    full = getattr(config, "TREE_WATER_FULL_COUNT", 3)
    if water_index <= full:
        return start
    return max(1, start - (water_index - full))


async def handle_tree_watering(client, message):
    """Pay the waterer when the Grow-a-Tree bot's 'thanks for watering' embed appears -
    whether it's a NEW message or the bot EDITING the existing one in place.

    Dedup is on the tree height (strictly increasing per water): we only ever pay when the
    height is greater than the last one we paid for, so re-processing the same edit, or the
    new-message and edit events for one water, can't double-pay. Daily-capped per user too.
    """
    if message.author.id != getattr(config, "GROW_A_TREE_BOT_ID", 0):
        return
    waterer_id = None
    height = None
    for e in message.embeds:
        blob = f"{e.description or ''} {e.title or ''}"
        wm = _WATER_RE.search(blob)
        if wm:
            waterer_id = int(wm.group(1))
        hm = _HEIGHT_RE.search(blob)
        if hm:
            height = float(hm.group(1).replace(",", ""))
        if waterer_id:
            break
    if not waterer_id:
        return

    store = load_json_file(config.TREE_WATER_FILE) or {}

    # Dedup: only pay once per genuine water (height must have grown since last payout).
    if height is not None:
        last_h = store.get("_last_height", 0)
        if height <= last_h:
            return
        store["_last_height"] = height

    today = _today()
    rec = store.get(str(waterer_id))
    same_day = isinstance(rec, dict) and rec.get("date") == today
    count = rec.get("count", 0) if same_day else 0
    earned = rec.get("earned", 0) if same_day else 0
    pay_amt = _tree_reward(count + 1)  # decays after the first few waters; floors at 1
    if not _pay(waterer_id, pay_amt, "Tree watering reward"):
        return
    store[str(waterer_id)] = {"date": today, "count": count + 1, "earned": earned + pay_amt}
    save_json_file(config.TREE_WATER_FILE, store)
    try:
        await message.channel.send(
            f"\U0001f333 <@{waterer_id}> earned **{pay_amt:,} UKPence** for watering the tree!",
            allowed_mentions=discord.AllowedMentions(users=True),
            delete_after=600,  # self-destruct after 10 minutes to keep the channel tidy
        )
    except Exception:
        log.debug("tree watering message failed", exc_info=True)


# ---------------------------------------------------------------------------
# /benefits
# ---------------------------------------------------------------------------
_BENEFITS_SUCCESS = [
    "🧾 **Benefits approved!** <@{uid}> receives **{amount:,} UKPence** from the state. Spend it wisely (or at the casino).",
    "🧾 The DWP has assessed your claim. **<@{uid}>**, here's **{amount:,} UKPence** to tide you over. Don't blow it all on scratchcards.",
    "🧾 Universal Credit incoming: **+{amount:,} UKPence** for <@{uid}>. Mind how you go.",
    "🧾 Your giro's arrived. **{amount:,} UKPence** for <@{uid}>. Try the lottery, eh?",
    "🧾 **Cha-ching.** <@{uid}> topped up with **{amount:,} UKPence** of taxpayer money. You're welcome.",
    "🧾 Claim successful. The state grants <@{uid}> **{amount:,} UKPence**. The job centre wishes you well.",
    "🧾 Sorted. <@{uid}> pockets **{amount:,} UKPence** from the public purse. Keep your chin up.",
]
_BENEFITS_RICH = [
    "💼 You've got **{bal:,} UKPence** - benefits are for those under {threshold:,}. Get back to work.",
    "💼 Claim denied: **{bal:,} UKPence** is too rich for the state's blood (cutoff is {threshold:,}).",
    "💼 The DWP reviewed your **{bal:,} UKPence** and decided you'll be fine. Off you pop.",
    "💼 Nice try, but **{bal:,} UKPence** is well over the {threshold:,} threshold. No handouts for the wealthy.",
    "💼 You're hardly destitute with **{bal:,} UKPence**. Come back when you're properly skint (under {threshold:,}).",
]
_BENEFITS_ALREADY = [
    "🧾 You've already had your benefits today. The office reopens at midnight UK <t:{ts}:R>.",
    "🧾 One claim a day, that's the rule. Back at midnight UK <t:{ts}:R>.",
    "🧾 The giro's already gone out today. Next one <t:{ts}:R>.",
    "🧾 Patience. Your next assessment is <t:{ts}:R>.",
    "🧾 You've drained today's allowance. Reopens <t:{ts}:R>.",
]
_BENEFITS_FRAUD_WARN = [
    "🕵️ Hang on. You've shifted **{out:,} UKPence** to other users lately, and we count that as yours - so you're not actually eligible. Do it again and you'll be cut off.",
    "🕵️ The fraud office clocked **{out:,} UKPence** leaving your account recently. Parking money on mates doesn't make you poor. Denied - and consider this your one warning.",
    "🕵️ Benefits are means-tested on what you've **had**, not just what's in your wallet. You've moved **{out:,} UKPence** out recently. No claim today - don't push your luck.",
    "🕵️ Nice try. **{out:,} UKPence** of recent transfers says you're not skint. Refused. Repeat it and you'll lose benefits access entirely.",
]
_BENEFITS_FRAUD_BAN = [
    "🚫 **Benefits fraud detected.** Caught hiding UKPence to keep claiming - you're barred from benefits for **{days} days**.",
    "🚫 That's enough. The DWP fraud squad has sanctioned you for **{days} days**. Keep it up and it only gets longer.",
    "🚫 Caught red-handed shuffling UKPence to look 'poor'. Benefits suspended for **{days} days**.",
    "🚫 **Sanctioned.** Repeated benefits fraud has earned you a **{days}-day** ban. Try earning it honestly.",
]
_BENEFITS_BANNED = [
    "🚫 You're serving a benefits-fraud ban. Access returns <t:{ts}:R>.",
    "🚫 No benefits for you - your fraud ban lifts <t:{ts}:R>.",
    "🚫 The DWP hasn't forgotten. Your benefits ban ends <t:{ts}:R>.",
]


def _benefits_rec(store, uid):
    """Normalise a stored record (older versions stored just the last-claim date string)."""
    v = store.get(str(uid))
    rec = {"last": None, "offenses": 0, "banned_until": 0, "warned": False}
    if isinstance(v, str):
        rec["last"] = v
    elif isinstance(v, dict):
        for k in rec:
            if k in v:
                rec[k] = v[k]
    return rec


def _recent_pay_out(uid, days) -> int:
    """Total UKP this user has sent via /pay in the last ``days`` (their 'hidden' wealth)."""
    cutoff = int(time.time()) - days * 86400
    try:
        row = DatabaseManager.fetch_one(
            "SELECT COALESCE(SUM(amount),0) FROM pay_transfers WHERE payer_id = ? AND timestamp > ?",
            (str(uid), cutoff))
        return int(row[0]) if row else 0
    except Exception:
        log.error("benefits pay-out lookup failed", exc_info=True)
        return 0


async def handle_benefits_command(interaction):
    uid = interaction.user.id
    suid = str(uid)
    bal = get_bb(uid)
    threshold = getattr(config, "BENEFITS_THRESHOLD", 250)
    store = load_json_file(config.BENEFITS_FILE) or {}
    rec = _benefits_rec(store, suid)
    now = int(time.time())

    async def _reply(msg):
        await interaction.response.send_message(msg)

    def _save():
        store[suid] = rec
        save_json_file(config.BENEFITS_FILE, store)

    # Serving a fraud ban?
    if rec["banned_until"] > now:
        await _reply(random.choice(_BENEFITS_BANNED).format(ts=rec["banned_until"]))
        return

    # Genuinely well-off (hid nothing) - plain denial, no penalty.
    if bal >= threshold:
        await _reply(random.choice(_BENEFITS_RICH).format(bal=bal, threshold=threshold))
        return

    # Effective wealth = balance + recent /pay outflows. Parking UKP on an alt to drop
    # under the threshold doesn't make you poor.
    recent_out = _recent_pay_out(suid, getattr(config, "BENEFITS_LOOKBACK_DAYS", 3))
    if bal + recent_out >= threshold:
        ramp = getattr(config, "BENEFITS_BAN_RAMP", [3, 7, 14, 30])
        if rec["offenses"] == 0 and not rec["warned"]:
            rec["warned"] = True  # one warning before any ban (protects honest givers)
            _save()
            await _reply(random.choice(_BENEFITS_FRAUD_WARN).format(out=recent_out))
            return
        days = ramp[min(rec["offenses"], len(ramp) - 1)]
        rec["offenses"] += 1
        rec["banned_until"] = now + days * 86400
        _save()
        await _reply(random.choice(_BENEFITS_FRAUD_BAN).format(days=days))
        return

    # Money locked in a bond still counts as wealth - but that's a legit feature, so it's a
    # plain denial, not a fraud flag.
    locked = 0
    try:
        from lib.economy.bonds import active_bond_principal
        locked = active_bond_principal(suid)
    except Exception:
        locked = 0
    if bal + recent_out + locked >= threshold:
        await _reply(
            f"🏦 You've got **{locked:,} UKPence** locked in a bond, which still counts as wealth - "
            f"so you're over the {threshold:,} threshold. Wait for it to mature or break it early."
        )
        return

    # Already claimed this UK day?
    today = _today()
    if rec["last"] == today:
        await _reply(random.choice(_BENEFITS_ALREADY).format(ts=_next_uk_midnight_ts()))
        return

    # Eligible: pay out (and clear any standing warning - they came good).
    rec["last"] = today
    rec["warned"] = False
    _save()
    amount = random.randint(getattr(config, "BENEFITS_MIN", 30), getattr(config, "BENEFITS_MAX", 75))
    if not _pay(uid, amount, "Benefits payment"):
        await _reply("🧾 The benefits office is shut right now - try later.")
        return
    await _reply(random.choice(_BENEFITS_SUCCESS).format(uid=uid, amount=amount))


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
