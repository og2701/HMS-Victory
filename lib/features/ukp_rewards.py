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
from lib.economy.economy_manager import add_bb, get_bb, remove_bb
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
    # Once per UK day per user: a user can land several messages on 6+ reactions, so the HoF
    # cash is farmable. Cap the UKP to one HoF reward a day - the HoF entry and the badge
    # still happen in the caller; this only gates the money. (Check->pay->record runs with no
    # await between, so two near-simultaneous HoF entries can't both pay.)
    store = load_json_file(config.HOF_REWARD_CLAIMS_FILE) or {}
    today = _today()
    if store.get(str(user_id)) == today:
        log.info("[HOF] %s already earned a HoF reward today; skipping the UKP.", user_id)
        return
    amount = getattr(config, "HOF_REWARD", 100)
    if not _pay(user_id, amount, "Hall of Fame reward"):
        return
    store[str(user_id)] = today
    save_json_file(config.HOF_REWARD_CLAIMS_FILE, store)
    try:
        user = client.get_user(int(user_id)) or await client.fetch_user(int(user_id))
        await user.send(
            f"\U0001f3c6 Your message just made it into the **Hall of Fame** - "
            f"here's **{amount:,} UKPence** to go with the glory. Well earned!"
        )
    except Exception:
        log.debug("HoF reward DM failed", exc_info=True)
    from lib.features.income_badges import record_income_source, bump_daily_income
    bump_daily_income("hof_total", amount)
    await record_income_source(client, user_id, "hof")


# ---------------------------------------------------------------------------
# Tree watering
# ---------------------------------------------------------------------------
_WATER_RE = re.compile(r"Thanks <@!?(\d+)> for watering the tree", re.IGNORECASE)
# The growth-window timestamp ("...come back <t:1780655819:R>.") only changes on a real
# water - used to dedup against the bot's periodic message refreshes.
_COMEBACK_RE = re.compile(r"come back <t:(\d+)", re.IGNORECASE)


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

    Dedup is on the "come back <t:...>" growth-window timestamp, which only changes on a
    REAL water. The bot also refreshes the message periodically (which bumps the displayed
    height without anyone watering), so height is NOT a safe key - that timestamp is.
    """
    if message.author.id != getattr(config, "GROW_A_TREE_BOT_ID", 0):
        return
    waterer_id = None
    comeback = None
    for e in message.embeds:
        blob = f"{e.description or ''} {e.title or ''}"
        wm = _WATER_RE.search(blob)
        if wm:
            waterer_id = int(wm.group(1))
        cm = _COMEBACK_RE.search(blob)
        if cm:
            comeback = int(cm.group(1))
        if waterer_id:
            break
    if not waterer_id:
        return

    store = load_json_file(config.TREE_WATER_FILE) or {}

    # Dedup on the growth-window timestamp: it only advances on a genuine water, so a mere
    # message refresh (same window, higher height) is correctly ignored. If it's missing,
    # skip rather than risk paying for a refresh.
    if comeback is None or comeback <= store.get("_last_cb", 0):
        return
    store["_last_cb"] = comeback

    today = _today()
    rec = store.get(str(waterer_id)) if isinstance(store.get(str(waterer_id)), dict) else {}
    same_day = rec.get("date") == today
    count = rec.get("count", 0) if same_day else 0
    earned = rec.get("earned", 0) if same_day else 0
    total = rec.get("total", 0) + 1                    # lifetime water count (never resets)
    pay_amt = _tree_reward(count + 1)  # decays after the first few waters; floors at 1
    if not _pay(waterer_id, pay_amt, "Tree watering reward"):
        return
    store[str(waterer_id)] = {"date": today, "count": count + 1, "earned": earned + pay_amt, "total": total}
    save_json_file(config.TREE_WATER_FILE, store)

    from lib.features.income_badges import award_badge_safe, record_income_source, bump_daily_income
    bump_daily_income("tree_total", pay_amt)
    await award_badge_safe(client, waterer_id, "green_fingers")     # first water (idempotent)
    if pay_amt <= 10:
        await award_badge_safe(client, waterer_id, "drip")          # decayed to 10 UKPence or below today
    if total >= 100:
        await award_badge_safe(client, waterer_id, "sir_branchalot")
    await record_income_source(client, waterer_id, "tree")

    try:
        await message.channel.send(
            f"\U0001f333 <@{waterer_id}> earned **{pay_amt:,} UKPence** for watering the tree!",
            allowed_mentions=discord.AllowedMentions(users=True),
            delete_after=600,  # self-destruct after 10 minutes to keep the channel tidy
        )
    except Exception:
        log.debug("tree watering message failed", exc_info=True)


# ---------------------------------------------------------------------------
# DISBOARD bump
# ---------------------------------------------------------------------------
async def handle_bump_reward(client, message):
    """Reward the member who bumps the server on DISBOARD. DISBOARD's '/bump' success reply
    ('Bump done!') is an interaction response, so its invoker is the bumper. DISBOARD limits a
    server to one bump every ~2h, so this naturally can't be farmed."""
    if message.author.id != getattr(config, "DISBOARD_BOT_ID", 302050872383242240):
        return
    # Success only - DISBOARD says "Bump done!" on success (a "wait N minutes" embed on failure).
    blob = (message.content or "") + " " + " ".join(
        f"{e.title or ''} {e.description or ''}" for e in message.embeds)
    if "bump done" not in blob.lower():
        return
    # The bumper = whoever ran /bump (interaction_metadata on modern discord.py, else .interaction).
    meta = getattr(message, "interaction_metadata", None) or getattr(message, "interaction", None)
    bumper = getattr(meta, "user", None)
    if bumper is None or getattr(bumper, "bot", False):
        log.info("[BUMP] couldn't identify the bumper on a 'Bump done' message; no reward given.")
        return
    # Dedup on the message id (claim before paying) so a re-delivered event can't double-pay.
    store = load_json_file(config.BUMP_REWARD_FILE) or {}
    if store.get("last_msg_id") == message.id:
        return
    store["last_msg_id"] = message.id
    save_json_file(config.BUMP_REWARD_FILE, store)

    amount = getattr(config, "BUMP_REWARD", 50)
    if not _pay(bumper.id, amount, "DISBOARD bump reward"):
        return
    try:
        from lib.features.income_badges import bump_daily_income
        bump_daily_income("bump_total", amount)
    except Exception:
        pass
    try:
        await message.channel.send(
            f"\U0001F4E3 <@{bumper.id}> earned **{amount:,} UKPence** for bumping the server - "
            "thanks for the support!",
            allowed_mentions=discord.AllowedMentions(users=True),
            delete_after=600,
        )
    except Exception:
        log.debug("bump reward message failed", exc_info=True)


# ---------------------------------------------------------------------------
# Welcoming a new member
# ---------------------------------------------------------------------------
# When someone joins we open a short "welcome window". The first WELCOME_MAX_WELCOMERS
# members who greet that newcomer inside the window each earn WELCOME_REWARD. A greeting
# is any of:
#   - replying to Discord's "Glad you're here, X" join system message,
#   - @mentioning the newcomer,
#   - posting a welcome-worded message in the join channel (the loose, fallback signal).
# One payout per (welcomer -> newcomer) pair; self-welcomes and bots don't count.
#
# State (config.WELCOME_TRACKING_FILE), keyed by newcomer id:
#   {"joined_at": epoch, "system_msg_id": int|None, "channel_id": int|None, "welcomers": [ids]}
#
# An in-memory window gate (_welcome_window_until) keeps the per-message path free when no
# one has joined recently; it resets on restart, which only means welcome windows open
# during downtime are dropped (the bot couldn't have paid them out then anyway).
_WELCOME_RE = re.compile(
    r"\bwelcome\b|\bwelcom\b|\bwelc\b|\bwlcm\b|\bwlc\b|\bwilkommen\b|"
    r"glad (?:you|u)(?:'?re| are)?(?: here| with us| to)|good to have (?:you|u)|"
    r"enjoy your stay|welcome aboard|welcome to the (?:server|sub|community)",
    re.IGNORECASE,
)

_welcome_window_until = 0.0  # epoch until which at least one welcome window is open


def _welcome_window_secs() -> int:
    return int(getattr(config, "WELCOME_WINDOW_MINUTES", 15)) * 60


def _extend_welcome_window() -> None:
    global _welcome_window_until
    _welcome_window_until = max(_welcome_window_until, time.time() + _welcome_window_secs())


def welcome_window_open() -> bool:
    """Cheap in-memory gate so on_message only does work shortly after a join."""
    return time.time() < _welcome_window_until


def _prune_welcome_store(store: dict) -> dict:
    cutoff = int(time.time()) - _welcome_window_secs()
    return {k: v for k, v in store.items()
            if isinstance(v, dict) and v.get("joined_at", 0) >= cutoff}


def register_new_member_join(member) -> None:
    """Open a welcome window for a newly joined member. Called from on_member_join.
    Preserves a record the join system message may already have created (event ordering)."""
    try:
        if getattr(member, "bot", False):
            return
        store = _prune_welcome_store(load_json_file(config.WELCOME_TRACKING_FILE) or {})
        prev = store.get(str(member.id)) or {}
        store[str(member.id)] = {
            "joined_at": int(time.time()),
            "system_msg_id": prev.get("system_msg_id"),
            "channel_id": prev.get("channel_id"),
            "welcomers": prev.get("welcomers", []),
        }
        save_json_file(config.WELCOME_TRACKING_FILE, store)
        _extend_welcome_window()
    except Exception:
        log.debug("register_new_member_join failed", exc_info=True)


def note_join_system_message(message) -> bool:
    """Record the id of Discord's auto 'X joined' system message so replies to it can be
    matched to the newcomer. Returns True if this was a join system message (so the caller
    can skip the welcome-reward path for it). Creates the record if the join event hasn't
    landed yet, so it works regardless of which arrives first."""
    try:
        if message.type != discord.MessageType.new_member:
            return False
        store = _prune_welcome_store(load_json_file(config.WELCOME_TRACKING_FILE) or {})
        nid = str(message.author.id)
        rec = store.get(nid) or {"joined_at": int(time.time()), "welcomers": []}
        rec["system_msg_id"] = message.id
        rec["channel_id"] = message.channel.id
        rec.setdefault("joined_at", int(time.time()))
        rec.setdefault("welcomers", [])
        store[nid] = rec
        save_json_file(config.WELCOME_TRACKING_FILE, store)
        _extend_welcome_window()
    except Exception:
        log.debug("note_join_system_message failed", exc_info=True)
    return True


def _welcome_targets(message, store: dict) -> set:
    """Which pending newcomer ids (as strings) this message welcomes."""
    targets = set()

    # Reply to the join system message (or directly to the newcomer's own message).
    ref = message.reference
    if ref is not None:
        ref_id = getattr(ref, "message_id", None)
        if ref_id is not None:
            for nid, rec in store.items():
                if rec.get("system_msg_id") == ref_id:
                    targets.add(nid)
        resolved = getattr(ref, "resolved", None)
        if isinstance(resolved, discord.Message) and resolved.author and str(resolved.author.id) in store:
            targets.add(str(resolved.author.id))

    # @mention of a pending newcomer - mentioning a brand-new member is itself a welcome.
    for u in message.mentions:
        if str(u.id) in store:
            targets.add(str(u.id))

    # Loose fallback: a welcome-worded message in the join channel, with no explicit target.
    # Attributed to the most recently joined pending newcomer.
    if not targets and message.content and _WELCOME_RE.search(message.content):
        general = getattr(getattr(config, "CHANNELS", None), "GENERAL", 0)
        in_join_channel = message.channel.id == general or any(
            rec.get("channel_id") == message.channel.id for rec in store.values()
        )
        if in_join_channel:
            newest = max(store.items(), key=lambda kv: kv[1].get("joined_at", 0))
            targets.add(newest[0])

    return targets


async def handle_welcome_reward(client, message) -> None:
    """Pay the first WELCOME_MAX_WELCOMERS members who welcome a newcomer in the window."""
    try:
        if message.guild is None or message.author is None or getattr(message.author, "bot", False):
            return

        store = _prune_welcome_store(load_json_file(config.WELCOME_TRACKING_FILE) or {})
        if not store:
            return

        targets = _welcome_targets(message, store)
        if not targets:
            return

        welcomer = message.author
        amount = int(getattr(config, "WELCOME_REWARD", 20))
        cap = int(getattr(config, "WELCOME_MAX_WELCOMERS", 5))

        # Critical section: load -> check -> pay -> save with NO await in between, so two
        # near-simultaneous welcome messages can't both slip past the cap or double-pay
        # (asyncio is single-threaded; without an await this runs atomically). _pay/add_bb
        # are synchronous; the channel send + bookkeeping happen afterwards.
        paid_for = 0
        for nid in targets:
            rec = store.get(nid)
            if rec is None or str(welcomer.id) == nid:
                continue  # no record, or you can't welcome yourself
            welcomers = rec.setdefault("welcomers", [])
            if welcomer.id in welcomers or len(welcomers) >= cap:
                continue  # already paid for this newcomer, or its pot is used up
            if not _pay(welcomer.id, amount, "Welcomed a new member"):
                continue
            welcomers.append(welcomer.id)
            paid_for += 1
        if not paid_for:
            return
        save_json_file(config.WELCOME_TRACKING_FILE, store)

        total = amount * paid_for
        try:
            who = "a new member" if paid_for == 1 else f"{paid_for} new members"
            await message.channel.send(
                f"\U0001F44B <@{welcomer.id}> earned **{total:,} UKPence** for welcoming {who}!",
                allowed_mentions=discord.AllowedMentions(users=True),
                delete_after=600,  # self-destruct after 10 minutes to keep chat tidy
            )
        except Exception:
            log.debug("welcome reward message failed", exc_info=True)

        try:
            from lib.features.income_badges import record_income_source, bump_daily_income
            bump_daily_income("welcome_total", total)
            await record_income_source(client, welcomer.id, "welcome")
        except Exception:
            log.debug("welcome reward bookkeeping failed", exc_info=True)
    except Exception:
        log.error("handle_welcome_reward failed", exc_info=True)


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
    "🧾 Form processed, no questions asked. <@{uid}> receives **{amount:,} UKPence**. The state believes in you.",
    "🧾 Payment authorised. **{amount:,} UKPence** lands in <@{uid}>'s account. Try not to fritter it away.",
    "🧾 The hardship fund has spoken: **+{amount:,} UKPence** for <@{uid}>. Bills first, scratchcards second.",
    "🧾 **<@{uid}>**, your support payment of **{amount:,} UKPence** is through. Spend it like it's somebody else's money, because it is.",
    "🧾 Crisis loan? No, free money. <@{uid}> banks **{amount:,} UKPence**. Don't say the state never gave you anything.",
    "🧾 Approved on the first try, a small miracle. **{amount:,} UKPence** for <@{uid}>. Go on, treat yourself.",
    "🧾 The benefits office stamped it: **{amount:,} UKPence** to <@{uid}>. Mind it lasts till midnight.",
    "🧾 Cost-of-living top-up incoming: **+{amount:,} UKPence** for <@{uid}>. Every little helps.",
    "🧾 <@{uid}>, the welfare gods smiled. **{amount:,} UKPence** is yours. Use it wisely or don't, we won't judge.",
    "🧾 Signed, sealed, deposited. <@{uid}> gets **{amount:,} UKPence**. The taxpayer salutes you.",
]
_BENEFITS_RICH = [
    "💼 You've got **{bal:,} UKPence** - benefits are for those under {threshold:,}. Get back to work.",
    "💼 Claim denied: **{bal:,} UKPence** is too rich for the state's blood (cutoff is {threshold:,}).",
    "💼 The DWP reviewed your **{bal:,} UKPence** and decided you'll be fine. Off you pop.",
    "💼 Nice try, but **{bal:,} UKPence** is well over the {threshold:,} threshold. No handouts for the wealthy.",
    "💼 You're hardly destitute with **{bal:,} UKPence**. Come back when you're properly skint (under {threshold:,}).",
    "💼 The means test says no. **{bal:,} UKPence** sails past the {threshold:,} cutoff. Tighten your belt.",
    "💼 With **{bal:,} UKPence** in the bank you don't need a handout, you need an accountant.",
    "💼 Computer says no. **{bal:,} UKPence** is too flush for benefits (limit's {threshold:,}).",
    "💼 We don't subsidise the comfortable. **{bal:,} UKPence** is comfortable. Cutoff is {threshold:,}.",
    "💼 Claim rejected: your **{bal:,} UKPence** would make half the server jealous. Over the {threshold:,} line.",
    "💼 Save it for someone who needs it. **{bal:,} UKPence** is well above {threshold:,}. Denied.",
    "💼 You're not on your uppers with **{bal:,} UKPence**. The {threshold:,} threshold says you'll cope.",
    "💼 The fund is for the broke, not the bourgeois. **{bal:,} UKPence** disqualifies you (under {threshold:,} only).",
    "💼 Sorry, your **{bal:,} UKPence** triggered the 'doing alright, actually' filter. Back over {threshold:,} you go.",
    "💼 No benefits for the well-heeled. Come back under {threshold:,}, you've got **{bal:,} UKPence**.",
]
_BENEFITS_ALREADY = [
    "🧾 You've already had your benefits today. The office reopens at midnight UK <t:{ts}:R>.",
    "🧾 One claim a day, that's the rule. Back at midnight UK <t:{ts}:R>.",
    "🧾 The giro's already gone out today. Next one <t:{ts}:R>.",
    "🧾 Patience. Your next assessment is <t:{ts}:R>.",
    "🧾 You've drained today's allowance. Reopens <t:{ts}:R>.",
    "🧾 Already claimed, already spent, knowing you. Try again <t:{ts}:R>.",
    "🧾 The till's shut for the day. Next handout <t:{ts}:R>.",
    "🧾 No double-dipping. Your next claim unlocks <t:{ts}:R>.",
    "🧾 That's your lot for today. The office reopens <t:{ts}:R>.",
    "🧾 Easy, tiger. One payment per day. Back <t:{ts}:R>.",
    "🧾 The cupboard's bare until midnight. Return <t:{ts}:R>.",
    "🧾 You've had your dole today. Come back <t:{ts}:R>.",
    "🧾 Claim's on cooldown. The shutters lift <t:{ts}:R>.",
    "🧾 Today's giro is spent. Next one's ready <t:{ts}:R>.",
    "🧾 We gave at the office, today's office. Reopens <t:{ts}:R>.",
]
_BENEFITS_FRAUD_WARN = [
    "🕵️ Hang on. You've shifted **{out:,} UKPence** to other users lately, and we count that as yours - so you're not actually eligible. Do it again and you'll be cut off.",
    "🕵️ The fraud office clocked **{out:,} UKPence** leaving your account recently. Parking money on mates doesn't make you poor. Denied - and consider this your one warning.",
    "🕵️ Benefits are means-tested on what you've **had**, not just what's in your wallet. You've moved **{out:,} UKPence** out recently. No claim today - don't push your luck.",
    "🕵️ Nice try. **{out:,} UKPence** of recent transfers says you're not skint. Refused. Repeat it and you'll lose benefits access entirely.",
    "🕵️ The audit flagged **{out:,} UKPence** flowing out of your account. Stashing it elsewhere doesn't fool us. Denied - and that's your warning.",
    "🕵️ Funny how you're 'broke' right after sending **{out:,} UKPence** away. We count it as yours. No claim today, and don't make us escalate.",
    "🕵️ Compliance here. **{out:,} UKPence** left your wallet recently, so on paper you're not eligible. Refused. Try it again and you're cut off.",
    "🕵️ We can read a ledger. **{out:,} UKPence** of outbound transfers means you're not poor, you're hiding. Denied. Consider yourself warned.",
    "🕵️ Convenient timing: **{out:,} UKPence** shipped out, then a benefits claim. We weren't born yesterday. No payment - last chance.",
    "🕵️ The means test includes what you've **given away**. That's **{out:,} UKPence** recently. Claim refused. Do it again and access goes.",
    "🕵️ Spotted: **{out:,} UKPence** quietly moved to other accounts. That counts against you. Denied today, banned if it continues.",
    "🕵️ You can't gift away **{out:,} UKPence** and then cry poverty. Claim blocked. One more stunt and you'll be sanctioned.",
    "🕵️ Our system loves a pattern, and yours is **{out:,} UKPence** out then a claim in. Refused. Push it and you'll be barred.",
    "🕵️ Means-tested means means-tested. **{out:,} UKPence** of recent transfers disqualifies you. No claim - and heed this warning.",
]
_BENEFITS_FRAUD_BAN = [
    "🚫 **Benefits fraud detected.** Caught hiding UKPence to keep claiming - you're barred from benefits for **{days} days**.",
    "🚫 That's enough. The DWP fraud squad has sanctioned you for **{days} days**. Keep it up and it only gets longer.",
    "🚫 Caught red-handed shuffling UKPence to look 'poor'. Benefits suspended for **{days} days**.",
    "🚫 **Sanctioned.** Repeated benefits fraud has earned you a **{days}-day** ban. Try earning it honestly.",
    "🚫 The fraud squad has seen enough. Benefits revoked for **{days} days**. Crime doesn't pay, ironically.",
    "🚫 Funnelling UKPence to dodge the means test? Banned for **{days} days**. The DWP has a long memory.",
    "🚫 **Investigation closed, verdict guilty.** No benefits for **{days} days**. Next time it doubles.",
    "🚫 You gamed the system one time too many. **{days}-day** sanction applied. Sit and think about it.",
    "🚫 Benefits access suspended for **{days} days** for persistent fiddling. Don't make us go to **{days}** times two.",
    "🚫 Caught laundering your 'poverty' again. **{days} days** in the sin bin. Earn it the proper way.",
    "🚫 **Sanction issued.** Repeat offender, **{days} days** without benefits. The honest folk thank you.",
    "🚫 That's a wrap on your claiming career for **{days} days**. The fraud office wishes you a humbling time.",
    "🚫 Three strikes and a shovel: you kept digging, so it's a **{days}-day** ban. Reflect on your choices.",
    "🚫 **Fraud confirmed.** Benefits frozen for **{days} days**. Keep this up and the freeze gets glacial.",
]
_BENEFITS_BANNED = [
    "🚫 You're serving a benefits-fraud ban. Access returns <t:{ts}:R>.",
    "🚫 No benefits for you - your fraud ban lifts <t:{ts}:R>.",
    "🚫 The DWP hasn't forgotten. Your benefits ban ends <t:{ts}:R>.",
    "🚫 Still sanctioned. The system unlocks you <t:{ts}:R>.",
    "🚫 Your fraud ban is very much active. Try again <t:{ts}:R>.",
    "🚫 Nope. You're on the naughty list until <t:{ts}:R>.",
    "🚫 Benefits remain frozen. The thaw comes <t:{ts}:R>.",
    "🚫 Access denied, ban in progress. Lifts <t:{ts}:R>.",
    "🚫 You're still doing your time. Released <t:{ts}:R>.",
    "🚫 The sanction stands. Come back <t:{ts}:R>.",
    "🚫 No dole for the disgraced just yet. Ends <t:{ts}:R>.",
    "🚫 Patience, fraudster. Your ban expires <t:{ts}:R>.",
    "🚫 The fraud office says not yet. Ban lifts <t:{ts}:R>.",
]


def _benefits_rec(store, uid):
    """Normalise a stored record (older versions stored just the last-claim date string)."""
    v = store.get(str(uid))
    rec = {"last": None, "offenses": 0, "banned_until": 0, "warned": False, "streak": 0, "fine": 0, "fine_paid_at": 0}
    if isinstance(v, str):
        rec["last"] = v
    elif isinstance(v, dict):
        for k in rec:
            if k in v:
                rec[k] = v[k]
    return rec


def _recent_pay_out(uid, days, since_ts=0) -> int:
    """Total UKP this user has sent via /pay in the last ``days`` (their 'hidden' wealth)."""
    cutoff = max(int(time.time()) - days * 86400, since_ts)
    try:
        row = DatabaseManager.fetch_one(
            "SELECT COALESCE(SUM(amount),0) FROM pay_transfers WHERE payer_id = ? AND timestamp > ?",
            (str(uid), cutoff))
        return int(row[0]) if row else 0
    except Exception:
        log.error("benefits pay-out lookup failed", exc_info=True)
        return 0


def _benefits_clear_ts(uid, bal, threshold, days, since_ts=0):
    """When (epoch) recent transfers age out of the window enough that balance + the
    still-in-window transfers drop below the threshold - i.e. when they'd be eligible again
    if they stop sending UKP. Each transfer leaves the window ``days`` after it was sent."""
    cutoff = max(int(time.time()) - days * 86400, since_ts)
    try:
        rows = DatabaseManager.fetch_all(
            "SELECT timestamp, amount FROM pay_transfers WHERE payer_id = ? AND timestamp > ? "
            "ORDER BY timestamp ASC", (str(uid), cutoff)) or []
    except Exception:
        return None
    if not rows:
        return None
    target = threshold - bal              # in-window transfers must fall below this
    remaining = sum(a for _, a in rows)
    for ts, a in rows:                    # oldest first; each expires at ts + window
        remaining -= a
        if remaining < target:
            return ts + days * 86400
    return None


async def handle_benefits_command(interaction):
    uid = interaction.user.id
    suid = str(uid)
    bal = get_bb(uid)
    threshold = getattr(config, "BENEFITS_THRESHOLD", 250)
    store = load_json_file(config.BENEFITS_FILE) or {}
    rec = _benefits_rec(store, suid)
    now = int(time.time())

    async def _reply(msg, view=None):
        if view is not None:
            await interaction.response.send_message(msg, view=view)
        else:
            await interaction.response.send_message(msg)

    def _save():
        store[suid] = rec
        save_json_file(config.BENEFITS_FILE, store)

    # Serving a fraud ban?
    if rec["banned_until"] > now:
        fine = rec.get("fine", 0)
        if fine <= 0:
            fine = 400  # Fallback for legacy bans
        view = BenefitsFineView(uid, fine)
        await interaction.response.send_message(
            random.choice(_BENEFITS_BANNED).format(ts=rec["banned_until"]) +
            f"\n\n-# You can pay a fine of **{fine:,} UKPence** to lift the ban and reset your offense history.",
            view=view
        )
        return

    # Genuinely well-off (hid nothing) - plain denial, no penalty.
    if bal >= threshold:
        await _reply(random.choice(_BENEFITS_RICH).format(bal=bal, threshold=threshold))
        return

    # Effective wealth = balance + recent /pay outflows. Parking UKP on an alt to drop
    # under the threshold doesn't make you poor.
    recent_out = _recent_pay_out(suid, getattr(config, "BENEFITS_LOOKBACK_DAYS", 3), rec.get("fine_paid_at", 0))
    if bal + recent_out >= threshold:
        ramp = getattr(config, "BENEFITS_BAN_RAMP", [3, 7, 14, 30])
        if rec["offenses"] == 0 and not rec["warned"]:
            rec["warned"] = True  # one warning before any ban (protects honest givers)
            _save()
            msg = random.choice(_BENEFITS_FRAUD_WARN).format(out=recent_out)
            clear = _benefits_clear_ts(suid, bal, threshold, getattr(config, "BENEFITS_LOOKBACK_DAYS", 1), rec.get("fine_paid_at", 0))
            if clear:
                msg += f"\n-# If you stop sending UKP, you'll be eligible again <t:{clear}:R>."
            await _reply(msg)
            return
        days = ramp[min(rec["offenses"], len(ramp) - 1)]
        rec["offenses"] += 1
        rec["banned_until"] = now + days * 86400
        fine = max(1, min(int(recent_out * 0.25), 350))
        rec["fine"] = fine
        _save()
        from lib.features.income_badges import award_badge_safe
        from lib.economy import secret_config as _sc
        if (_b := _sc.bid("a4")):
            await award_badge_safe(interaction.client, uid, _b)
        view = BenefitsFineView(uid, fine)
        await _reply(
            random.choice(_BENEFITS_FRAUD_BAN).format(days=days) +
            f"\n\n-# You can pay a fine of **{fine:,} UKPence** to lift the ban and reset your offense history.",
            view=view
        )
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
    yesterday = (datetime.now(_UK) - timedelta(days=1)).strftime("%Y-%m-%d")
    rec["streak"] = (rec.get("streak", 0) + 1) if rec["last"] == yesterday else 1
    rec["last"] = today
    rec["warned"] = False
    _save()
    amount = random.randint(getattr(config, "BENEFITS_MIN", 30), getattr(config, "BENEFITS_MAX", 75))
    if not _pay(uid, amount, "Benefits payment"):
        await _reply("🧾 The benefits office is shut right now - try later.")
        return
    await _reply(random.choice(_BENEFITS_SUCCESS).format(uid=uid, amount=amount))

    from lib.features.income_badges import award_badge_safe, record_income_source, bump_daily_income
    bump_daily_income("benefits_total", amount)
    await award_badge_safe(interaction.client, uid, "on_the_dole")     # first claim (idempotent)
    if bal < 5:
        await award_badge_safe(interaction.client, uid, "rock_bottom")
    if rec["streak"] >= 7:
        await award_badge_safe(interaction.client, uid, "career_claimant")
    await record_income_source(interaction.client, uid, "benefits")


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
    from lib.features.income_badges import award_badge_safe, record_income_source, bump_daily_income
    bump_daily_income("ticket_total", amount)
    await award_badge_safe(client, creator_id, "squeaky_wheel")
    await record_income_source(client, creator_id, "ticket")
    return True


class BenefitsFineConfirmView(discord.ui.View):
    """View to confirm fine payment by the payer."""

    def __init__(self, banned_user_id: int, payer_id: int, fine: int):
        super().__init__(timeout=60)
        self.banned_user_id = banned_user_id
        self.payer_id = payer_id
        self.fine = fine

    @discord.ui.button(label="Confirm Payment", style=discord.ButtonStyle.danger, emoji="✅")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.payer_id:
            await interaction.response.send_message("Only the person who initiated this payment can confirm it.", ephemeral=True)
            return

        from lib.economy.economy_manager import remove_bb, get_bb
        from database import DatabaseManager
        import time

        uid = self.banned_user_id
        suid = str(uid)
        payer_id = self.payer_id

        # Use DatabaseManager lock to serialize state changes and avoid race conditions
        with DatabaseManager.locked_connection():
            store = load_json_file(config.BENEFITS_FILE) or {}
            rec = _benefits_rec(store, suid)
            now = int(time.time())

            if rec.get("banned_until", 0) <= now:
                await interaction.response.send_message("❌ This benefits ban has already expired or is not active.", ephemeral=True)
                return

            fine_amount = rec.get("fine", 0)
            if fine_amount <= 0:
                fine_amount = 350  # Fallback

            # Check payer's balance again to avoid race conditions
            bal = get_bb(payer_id)
            if bal < fine_amount:
                await interaction.response.send_message(
                    f"❌ You cannot afford this fine. The fine is **{fine_amount:,} UKPence**, but you only have **{bal:,} UKPence**.",
                    ephemeral=True
                )
                return

            # Deduct the fine from the payer and deposit to bank
            reason = f"Paid benefits fraud fine for {uid}" if payer_id != uid else "Paid benefits fraud fine"
            if not remove_bb(payer_id, fine_amount, reason=reason, to_bank=True):
                await interaction.response.send_message("❌ Fine payment failed due to a bank issue. Please try again.", ephemeral=True)
                return

            # Reset benefits status
            rec["banned_until"] = 0
            rec["fine"] = 0
            rec["offenses"] = 0
            rec["warned"] = False
            rec["fine_paid_at"] = now
            store[suid] = rec
            save_json_file(config.BENEFITS_FILE, store)

            # Clear outbound transactions log for this user in the database
            try:
                DatabaseManager.execute("DELETE FROM pay_transfers WHERE payer_id = ?", (suid,))
            except Exception as e:
                log.error("Failed to clear outbound transfers for user %s: %s", suid, e)

        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)

        if payer_id == uid:
            await interaction.followup.send(
                f"✅ **Fine Paid!** <@{payer_id}> paid their own fine of **{fine_amount:,} UKPence**. Their benefits ban has been lifted and their offense history has been reset!",
                ephemeral=False
            )
        else:
            await interaction.followup.send(
                f"✅ **Fine Paid!** <@{payer_id}> paid the fine of **{fine_amount:,} UKPence** for <@{uid}>. Their benefits ban has been lifted and their offense history has been reset!",
                ephemeral=False
            )
            # Generosity badge: paying off SOMEONE ELSE's benefits fine.
            try:
                from lib.features.income_badges import award_badge_safe
                await award_badge_safe(interaction.client, payer_id, "good_samaritan")
            except Exception:
                log.debug("good_samaritan badge award failed", exc_info=True)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="❌")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.payer_id:
            await interaction.response.send_message("Only the person who initiated this payment can cancel it.", ephemeral=True)
            return

        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(f"❌ Payment cancelled by <@{self.payer_id}>.", ephemeral=False)
        self.stop()


class BenefitsFineView(discord.ui.View):
    """Allows any user to pay the benefits fraud fine for a banned user to lift their ban."""

    def __init__(self, user_id: int, fine: int):
        super().__init__(timeout=180)
        self.user_id = user_id
        self.fine = fine

    @discord.ui.button(label="Pay Fine", style=discord.ButtonStyle.danger, emoji="💸", custom_id="benefits_fine:pay")
    async def pay_fine(self, interaction: discord.Interaction, button: discord.ui.Button):
        from lib.economy.economy_manager import get_bb
        import time

        # Ensure the ban is still active before offering confirmation
        store = load_json_file(config.BENEFITS_FILE) or {}
        rec = _benefits_rec(store, str(self.user_id))
        now = int(time.time())
        if rec.get("banned_until", 0) <= now:
            await interaction.response.send_message("❌ This benefits ban has already expired or is not active.", ephemeral=True)
            return

        # Fetch fine stored in JSON to be absolutely precise
        fine_amount = rec.get("fine", 0)
        if fine_amount <= 0:
            fine_amount = self.fine
        if fine_amount <= 0:
            fine_amount = 350

        payer_id = interaction.user.id
        bal = get_bb(payer_id)
        if bal < fine_amount:
            await interaction.response.send_message(
                f"❌ You cannot afford this fine. The fine is **{fine_amount:,} UKPence**, but you only have **{bal:,} UKPence**.",
                ephemeral=True
            )
            return

        # Send a non-ephemeral confirmation message
        if payer_id == self.user_id:
            msg = f"💸 <@{payer_id}>, are you sure you want to pay your own benefits fraud fine of **{fine_amount:,} UKPence**?"
        else:
            msg = f"💸 <@{payer_id}>, are you sure you want to pay the benefits fraud fine of **{fine_amount:,} UKPence** for <@{self.user_id}>?"

        confirm_view = BenefitsFineConfirmView(self.user_id, payer_id, fine_amount)
        await interaction.response.send_message(msg, view=confirm_view, ephemeral=False)



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
