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
    """Pay a player from the bank. Returns True on success.

    Every reward in this module (HoF, tree, bump, welcome, benefits, tickets) is
    discretionary - the server chooses to give it - so it scales down when bank reserves
    are low. See lib/economy/reserve_policy.py.
    """
    try:
        return add_bb(int(user_id), int(amount), reason=reason, discretionary=True)
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
# Welcoming pays for a reply, not for the word "welcome".
#
# The original rule paid WELCOME_REWARD the moment someone posted a welcome-worded message
# inside a 15-minute window. Three months of #general says that failed on its own terms:
# 87.9% of welcomes were never followed by another word to that newcomer, and the people
# doing it most had the worst follow-up rates - one member greeted 155 newcomers and spoke
# again to 3% of them. Meanwhile the member who actually engaged the most newcomers said
# "welcome" to 3% of them and so earned almost nothing. The rule paid the vocabulary and
# ignored the behaviour.
#
# So a greeting now books a *claim* rather than a payout, and money moves on evidence that
# a conversation happened:
#
#   1. Greet a newcomer in the join window  -> a pending claim, worth nothing yet.
#   2. That newcomer replies to you within WELCOME_REPLY_WINDOW_MINUTES
#                                           -> the claim pays WELCOME_REWARD.
#   3. Go back to them at least WELCOME_FOLLOWUP_MIN_HOURS later (and inside
#      WELCOME_FOLLOWUP_WINDOW_HOURS of their join)
#                                           -> WELCOME_FOLLOWUP_REWARD, once per pair.
#
# A bare "welcome" that lands on nobody now earns nothing, and the greeting no longer has
# to be welcome-worded at all - any first contact that gets an answer counts, because the
# newcomer answering is the only signal here that tracks whether they stay.
#
# State (config.WELCOME_TRACKING_FILE), keyed by newcomer id:
#   {"joined_at": epoch, "system_msg_id": int|None, "channel_id": int|None,
#    "pending": {welcomer_id: greeted_at}, "paid": [ids], "followed": [ids]}
# Records from the old scheme carry "welcomers": [ids], read as already-paid.
#
# _tracked is an in-memory mirror of which newcomer ids are live, so the on_message hook can
# decide in microseconds whether a message could possibly matter before touching disk.
_WELCOME_RE = re.compile(
    r"\bwelcome\b|\bwelcom\b|\bwelc\b|\bwlcm\b|\bwlc\b|\bwilkommen\b|"
    r"glad (?:you|u)(?:'?re| are)?(?: here| with us| to)|good to have (?:you|u)|"
    r"enjoy your stay|welcome aboard|welcome to the (?:server|sub|community)",
    re.IGNORECASE,
)

_tracked: set = set()          # newcomer ids with a live record
_tracked_loaded = False


def _welcome_window_secs() -> int:
    return int(getattr(config, "WELCOME_WINDOW_MINUTES", 15)) * 60


def _reply_window_secs() -> int:
    return int(getattr(config, "WELCOME_REPLY_WINDOW_MINUTES", 60)) * 60


def _followup_window_secs() -> int:
    return int(getattr(config, "WELCOME_FOLLOWUP_WINDOW_HOURS", 48)) * 3600


def _record_life_secs() -> int:
    """How long a newcomer's record is kept: the longest phase that can still pay."""
    return max(_welcome_window_secs(), _reply_window_secs(), _followup_window_secs())


def _refresh_tracked(store: dict) -> None:
    global _tracked, _tracked_loaded
    _tracked = set(store.keys())
    _tracked_loaded = True


def _load_store() -> dict:
    """Load, prune, and persist if pruning dropped anything.

    Persisting matters: pruning is what banks each welcomer's outcome, so a dropped record
    left on disk would be judged again on the very next message and count its dry welcomes
    over and over - tightening someone after one dead greeting instead of five.
    """
    raw = load_json_file(config.WELCOME_TRACKING_FILE) or {}
    store = _prune_welcome_store(raw)
    if len(store) != len(raw):
        save_json_file(config.WELCOME_TRACKING_FILE, store)
    _refresh_tracked(store)
    return store


def welcome_activity_possible(message) -> bool:
    """Cheap gate: could this message pay anyone? Runs on every message, so no disk.

    True when the author is a tracked newcomer (they might be replying to a greeter) or
    when the message addresses one (a greeting, or a follow-up). Everything else returns
    immediately without loading the store.
    """
    global _tracked_loaded
    try:
        if not _tracked_loaded:
            _load_store()
        if not _tracked:
            return False
        if str(getattr(message.author, "id", "")) in _tracked:
            return True
        for u in getattr(message, "mentions", None) or ():
            if str(u.id) in _tracked:
                return True
        ref = getattr(message, "reference", None)
        if ref is not None:
            resolved = getattr(ref, "resolved", None)
            if isinstance(resolved, discord.Message) and resolved.author \
                    and str(resolved.author.id) in _tracked:
                return True
            if getattr(ref, "message_id", None) is not None:
                return True     # might be the join system message; worth a disk check
        return bool(message.content and _WELCOME_RE.search(message.content))
    except Exception:
        return False


# Kept for callers that still ask the old question.
def welcome_window_open() -> bool:
    if not _tracked_loaded:
        _load_store()
    return bool(_tracked)


# --- per-member welcome reputation ------------------------------------------------
# One rolling list of outcomes per member: "engaged" if that newcomer ever answered them
# or they ever went back, "dry" if the greeting was the whole relationship. Outcomes are
# only decided when a newcomer's record is pruned, because until then a welcome can still
# turn into a conversation.
def _load_rep() -> dict:
    return load_json_file(config.WELCOME_REPUTATION_FILE) or {}


def _save_rep(rep: dict) -> None:
    save_json_file(config.WELCOME_REPUTATION_FILE, rep)


def welcome_needs_earning(user_id, rep: dict | None = None) -> bool:
    """True when this member's greeting no longer pays up front.

    Trips after WELCOME_DRY_STREAK_LIMIT welcomes in a row that went nowhere, and clears
    once they have had WELCOME_REDEMPTION_ENGAGEMENTS real interactions since.
    """
    rep = _load_rep() if rep is None else rep
    entry = rep.get(str(user_id)) or {}
    return bool(entry.get("earning", False))


def _record_outcomes(pairs: list[tuple[str, bool]]) -> None:
    """Log (welcomer, engaged) results and move members in and out of earning mode."""
    if not pairs:
        return
    limit = int(getattr(config, "WELCOME_DRY_STREAK_LIMIT", 5))
    redeem = int(getattr(config, "WELCOME_REDEMPTION_ENGAGEMENTS", 2))
    keep = int(getattr(config, "WELCOME_HISTORY_KEPT", 12))
    rep = _load_rep()
    for welcomer, engaged in pairs:
        entry = rep.setdefault(str(welcomer), {"recent": [], "earning": False, "since": 0})
        entry["recent"] = (entry.get("recent", []) + ["engaged" if engaged else "dry"])[-keep:]
        if engaged:
            entry["since"] = int(entry.get("since", 0)) + 1
            if entry.get("earning") and entry["since"] >= redeem:
                entry["earning"] = False
                entry["since"] = 0
        else:
            recent = entry["recent"]
            if len(recent) >= limit and all(x == "dry" for x in recent[-limit:]):
                if not entry.get("earning"):
                    entry["earning"] = True
                    entry["since"] = 0
    _save_rep(rep)


def _prune_welcome_store(store: dict) -> dict:
    """Drop finished newcomers, banking each welcomer's outcome on the way out.

    A welcome is only judged here: while the record lives, a quiet greeting can still
    become a conversation, and marking it dry earlier would punish someone for a newcomer
    who simply had not replied yet.
    """
    cutoff = int(time.time()) - _record_life_secs()
    keep, outcomes = {}, []
    for nid, rec in store.items():
        if not isinstance(rec, dict):
            continue
        if rec.get("joined_at", 0) >= cutoff:
            keep[nid] = rec
            continue
        rec = _norm(rec)
        if not rec.get("spoke"):
            # They joined and never posted a word. Nobody could have got a reply out of
            # them, so this tells us nothing about the people who said hello.
            continue
        engaged = {str(x) for x in rec.get("engaged", [])}
        banked = {str(x) for x in rec.get("banked", [])}
        touched = {str(x) for x in rec.get("paid", [])} | {str(x) for x in rec.get("pending", {})}
        for wid in touched - banked:
            outcomes.append((wid, wid in engaged))
    _record_outcomes(outcomes)
    return keep


def _norm(rec: dict) -> dict:
    """Give a record the current shape, including ones written by the old scheme."""
    rec.setdefault("joined_at", int(time.time()))
    rec.setdefault("pending", {})
    rec.setdefault("followed", [])
    rec.setdefault("engaged", [])
    rec.setdefault("banked", [])   # welcomers whose outcome has already been counted
    rec.setdefault("spoke", False)  # did this newcomer ever post at all?
    paid = rec.get("paid")
    if paid is None:
        # Old records tracked a flat "welcomers" list, all of whom had been paid.
        rec["paid"] = list(rec.get("welcomers", []))
    rec.pop("welcomers", None)
    return rec


def register_new_member_join(member) -> None:
    """Open a welcome window for a newly joined member. Called from on_member_join.
    Preserves a record the join system message may already have created (event ordering)."""
    try:
        if getattr(member, "bot", False):
            return
        store = _load_store()
        prev = _norm(store.get(str(member.id)) or {})
        prev["joined_at"] = int(time.time())
        store[str(member.id)] = prev
        save_json_file(config.WELCOME_TRACKING_FILE, store)
        _refresh_tracked(store)
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
        store = _load_store()
        nid = str(message.author.id)
        rec = _norm(store.get(nid) or {})
        rec["system_msg_id"] = message.id
        rec["channel_id"] = message.channel.id
        store[nid] = rec
        save_json_file(config.WELCOME_TRACKING_FILE, store)
        _refresh_tracked(store)
    except Exception:
        log.debug("note_join_system_message failed", exc_info=True)
    return True


def _welcome_targets(message, store: dict) -> set:
    """Which pending newcomer ids (as strings) this message addresses."""
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

    # @mention of a pending newcomer - mentioning a brand-new member is itself a greeting.
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


def _addressed_by_newcomer(message, store: dict) -> set:
    """Ids this newcomer is answering: an explicit reply, or anyone they @mention."""
    out = set()
    ref = getattr(message, "reference", None)
    resolved = getattr(ref, "resolved", None) if ref is not None else None
    if isinstance(resolved, discord.Message) and resolved.author:
        out.add(str(resolved.author.id))
    for u in getattr(message, "mentions", None) or ():
        out.add(str(u.id))
    return out


async def handle_welcome_reward(client, message) -> None:
    """Book claims from greeters, and pay them when the newcomer answers.

    Three separate things can happen on one message; each is a self-contained block so a
    quiet failure in one cannot stop the others.
    """
    try:
        if message.guild is None or message.author is None or getattr(message.author, "bot", False):
            return

        store = _load_store()
        if not store:
            return

        now = int(time.time())
        author_id = str(message.author.id)
        payouts = []          # (welcomer_id, amount, reason)
        dirty = False

        # --- 1. the newcomer answers someone -----------------------------------------
        if author_id in store:
            rec = _norm(store[author_id])
            if not rec.get("spoke"):
                rec["spoke"] = True
                dirty = True
            answered = _addressed_by_newcomer(message, store)
            for wid in list(set(rec["pending"]) | {str(x) for x in rec["paid"]}):
                if wid not in answered:
                    continue
                # Any reply is engagement, whether or not money is owed for it.
                if wid not in [str(x) for x in rec["engaged"]]:
                    rec["engaged"].append(int(wid))
                    # Counted now rather than at prune time: someone earning their payout
                    # back should not have to wait two days for the record to expire.
                    if wid not in [str(x) for x in rec["banked"]]:
                        rec["banked"].append(int(wid))
                        _record_outcomes([(wid, True)])
                    dirty = True
                greeted_at = rec["pending"].get(wid)
                if greeted_at is None:
                    continue            # they were paid up front; this just clears the mark
                rec["pending"].pop(wid, None)
                dirty = True
                if now - int(greeted_at) > _reply_window_secs():
                    continue            # too late to pay the held-back welcome
                if wid in [str(x) for x in rec["paid"]]:
                    continue
                rec["paid"].append(int(wid))
                payouts.append((int(wid), int(getattr(config, "WELCOME_REWARD", 10)),
                                "A new member replied to your welcome"))
            store[author_id] = rec

        # --- 2. someone addresses a newcomer ------------------------------------------
        else:
            targets = _welcome_targets(message, store)
            cap = int(getattr(config, "WELCOME_MAX_WELCOMERS", 5))
            follow_min = int(getattr(config, "WELCOME_FOLLOWUP_MIN_HOURS", 1)) * 3600
            for nid in targets:
                if nid == author_id:
                    continue
                rec = _norm(store.get(nid) or {})
                paid = [str(x) for x in rec["paid"]]
                followed = [str(x) for x in rec["followed"]]
                engaged = [str(x) for x in rec["engaged"]]

                # 2a. a later, separate visit back to someone they already greeted
                if (author_id in paid or author_id in rec["pending"]) \
                        and author_id not in followed:
                    since_join = now - rec.get("joined_at", now)
                    if follow_min <= since_join <= _followup_window_secs():
                        rec["followed"].append(int(author_id))
                        if author_id not in engaged:
                            rec["engaged"].append(int(author_id))
                        if author_id not in [str(x) for x in rec["banked"]]:
                            rec["banked"].append(int(author_id))
                            _record_outcomes([(author_id, True)])
                        payouts.append(
                            (int(author_id),
                             int(getattr(config, "WELCOME_FOLLOWUP_REWARD", 15)),
                             "You went back to a new member later"))
                        dirty = True

                # 2b. the greeting itself
                elif author_id not in paid and author_id not in rec["pending"]:
                    if now - rec.get("joined_at", 0) > _welcome_window_secs():
                        pass          # too late to greet; a follow-up may still count
                    elif len(rec["pending"]) + len(rec["paid"]) >= cap:
                        pass          # this newcomer's pot is spoken for
                    elif welcome_needs_earning(author_id):
                        # Recent welcomes all went nowhere, so this one pays on a reply.
                        rec["pending"][author_id] = now
                        dirty = True
                    else:
                        rec["paid"].append(int(author_id))
                        payouts.append((int(author_id),
                                        int(getattr(config, "WELCOME_REWARD", 10)),
                                        "Welcomed a new member"))
                        dirty = True
                store[nid] = rec

        if not dirty:
            return
        save_json_file(config.WELCOME_TRACKING_FILE, store)
        _refresh_tracked(store)
        if not payouts:
            return

        paid_total = 0
        for wid, amount, reason in payouts:
            if _pay(wid, amount, reason):
                paid_total += amount

        if not paid_total:
            return
        try:
            from lib.features.income_badges import record_income_source, bump_daily_income
            bump_daily_income("welcome_total", paid_total)
            for wid, _amount, _reason in payouts:
                await record_income_source(client, wid, "welcome")
        except Exception:
            log.debug("welcome reward bookkeeping failed", exc_info=True)

        for wid, amount, reason in payouts:
            await _welcome_first_time_dm(client, wid, amount, reason)
    except Exception:
        log.error("handle_welcome_reward failed", exc_info=True)


async def _welcome_first_time_dm(client, welcomer_id, amount: int, reason: str) -> None:
    """One-off explanation the first time someone earns this, so the new rule is learnable.

    The payout is silent from then on, so this DM is the only chance to explain why a
    greeting on its own paid nothing.
    """
    try:
        src_store = load_json_file(config.EARNED_SOURCES_FILE) or {}
        srcs = set(src_store.get(str(welcomer_id), []))
        if "welcome" in srcs:
            return
        srcs.add("welcome")
        src_store[str(welcomer_id)] = sorted(srcs)
        save_json_file(config.EARNED_SOURCES_FILE, src_store)
    except Exception:
        log.debug("welcome first-time check failed", exc_info=True)
        return
    try:
        user = client.get_user(int(welcomer_id)) or await client.fetch_user(int(welcomer_id))
        await user.send(
            f"\U0001F44B {reason} - you've earned **{amount:,} UKPence**!\n\n"
            "Welcoming a new member pays automatically. Go back and talk to them again "
            "later and you'll earn a bit more again.\n\n"
            "One thing worth knowing: if a long run of your welcomes goes nowhere - nobody "
            "ever replies and you never go back - greetings stop paying up front and start "
            "paying when the new member answers you instead. A couple of real conversations "
            "puts it back to normal.\n\n"
            "It's silent from here, so this is the only time you'll hear about it. \U0001FA99"
        )
    except Exception:
        log.debug("welcome first-time DM failed (DMs closed?)", exc_info=True)


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
