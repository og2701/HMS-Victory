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
from string import Formatter
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
_awaiting: set = set()         # greeters a newcomer has answered who owe them a reply
_tracked_loaded = False


def _welcome_window_secs() -> int:
    return int(getattr(config, "WELCOME_WINDOW_MINUTES", 15)) * 60


def _reply_window_secs() -> int:
    return int(getattr(config, "WELCOME_REPLY_WINDOW_MINUTES", 60)) * 60


def _followup_window_secs() -> int:
    return int(getattr(config, "WELCOME_FOLLOWUP_WINDOW_HOURS", 48)) * 3600


def _continue_window_secs() -> int:
    """How long after a newcomer answers a plain message in the same channel counts as a
    response. Zero, the default, means it never does and only a reply or a mention will do.
    Replies and mentions count whenever they arrive, window or no window."""
    return int(getattr(config, "WELCOME_CONTINUE_WINDOW_MINUTES", 0)) * 60


def _record_life_secs() -> int:
    """How long a newcomer's record is kept: the longest phase that can still pay."""
    return max(_welcome_window_secs(), _reply_window_secs(), _followup_window_secs())


def _refresh_tracked(store: dict) -> None:
    """Cache the ids the hot path has to recognise, so on_message stays a set lookup.

    Two sets: the newcomers, and the greeters a newcomer has answered who have not yet said
    anything back. The second one exists because carrying a conversation on usually doesn't
    involve pressing reply or typing someone's name, so without it those messages would
    never reach handle_welcome_reward and everybody would be marked dry.
    """
    global _tracked, _tracked_loaded, _awaiting
    _tracked = set(store.keys())
    _awaiting = set()
    for rec in store.values():
        if not isinstance(rec, dict):
            continue
        done = {str(x) for x in rec.get("engaged", [])}
        _awaiting |= {str(w) for w in rec.get("answered", {})} - done
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
        if not _tracked and not _awaiting:
            return False
        if str(getattr(message.author, "id", "")) in _tracked:
            return True
        if str(getattr(message.author, "id", "")) in _awaiting:
            return True     # a greeter who owes a newcomer a reply
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
        # Only greeters this newcomer actually replied to are judged. Being ignored by a
        # newcomer is not a mark against you - there was nothing there to carry on. The
        # dry mark is for the greeters who were answered and said nothing back.
        answered = {str(x) for x in rec.get("answered", {})}
        for wid in answered - banked:
            outcomes.append((wid, wid in engaged))
    _record_outcomes(outcomes)
    return keep


def _norm(rec: dict) -> dict:
    """Give a record the current shape, including ones written by the old scheme."""
    rec.setdefault("joined_at", int(time.time()))
    rec.setdefault("pending", {})
    rec.setdefault("followed", [])
    rec.setdefault("engaged", [])   # welcomers who stayed and talked
    rec.setdefault("answered", {})  # welcomer -> when this newcomer replied to them
    rec.setdefault("answered_in", None)  # channel they replied in
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
    """Book claims from greeters, and pay them for the conversation, not the greeting.

    A welcome is judged on what the greeter did once the newcomer answered. Saying hello
    and vanishing the moment somebody says hello back is the thing worth discouraging, so
    that is what counts as dry - not a newcomer who never replied, which is no reflection
    on whoever greeted them.

    Four separate things can happen on one message; each is a self-contained block so a
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
                # The newcomer answering does not settle anything by itself. It starts the
                # clock: from here the greeter either keeps the conversation going, which
                # is block 3, or they don't, and it goes down as dry.
                if wid not in rec["answered"]:
                    rec["answered"][wid] = now
                    rec["answered_in"] = message.channel.id
                    dirty = True
            store[author_id] = rec

        # --- 3. the greeter answers them back -----------------------------------------
        # This is the block that pays, and it wants a reply or a mention: an actual response
        # to the newcomer, not merely being present. Counting any message in the same
        # channel was tried and it cancels the penalty, because a regular posts something
        # within minutes of anything. That loose reading survives behind a config window
        # which is off by default (see _continue_window_secs).
        else:
            carried = _addressed_by_newcomer(message, store)   # same test, other direction
            for nid, rec in list(store.items()):
                rec = _norm(rec)
                started = rec["answered"].get(author_id)
                if started is None or author_id in [str(x) for x in rec["engaged"]]:
                    continue
                loose = _continue_window_secs()
                same_room = bool(loose) and (rec.get("answered_in") == message.channel.id
                                             and now - int(started) <= loose)
                if nid not in carried and not same_room:
                    continue
                rec["engaged"].append(int(author_id))
                # Banked now rather than at prune time, so anyone earning their instant
                # payment back is not left waiting two days for the record to expire.
                if author_id not in [str(x) for x in rec["banked"]]:
                    rec["banked"].append(int(author_id))
                    _record_outcomes([(author_id, True)])
                held = rec["pending"].pop(author_id, None)
                if held is not None and author_id not in [str(x) for x in rec["paid"]] \
                        and now - int(started) <= _reply_window_secs():
                    rec["paid"].append(int(author_id))
                    payouts.append((int(author_id), int(getattr(config, "WELCOME_REWARD", 10)),
                                    "You stayed and talked to a new member"))
                store[nid] = rec
                dirty = True

        # --- 2. someone addresses a newcomer ------------------------------------------
        if author_id not in store:
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
    "🧾 **Benefits approved.** <@{uid}> receives **{amount:,} UKPence** from the state. Please pretend you're looking for work.",
    "🧾 The DWP has assessed your claim. **<@{uid}>**, here's **{amount:,} UKPence** to tide you over. Try not to blow it all within three minutes.",
    "🧾 Universal Credit incoming: **+{amount:,} UKPence** for <@{uid}>. A triumph of paperwork over common sense.",
    "🧾 Your giro's arrived: **{amount:,} UKPence** for <@{uid}>. We expect nothing and are certain you will deliver.",
    "🧾 <@{uid}> topped up with **{amount:,} UKPence** of taxpayer money. The taxpayer sighs, but pays anyway.",
    "🧾 Claim successful. The state grants <@{uid}> **{amount:,} UKPence**. The Jobcentre caseworker is looking at the ceiling.",
    "🧾 Sorted. <@{uid}> pockets **{amount:,} UKPence** from the public purse. Truly inspiring financial management.",
    "🧾 Form processed without anyone looking too closely. <@{uid}> receives **{amount:,} UKPence**.",
    "🧾 Payment authorised: **{amount:,} UKPence** lands in <@{uid}>'s account. Make it last at least until the tea brews.",
    "🧾 The hardship fund has spoken: **+{amount:,} UKPence** for <@{uid}>. Bills first, terrible decisions second.",
    "🧾 **<@{uid}>**, your support payment of **{amount:,} UKPence** is through. Spend it like it's somebody else's money — because it is.",
    "🧾 Free money for <@{uid}>: **{amount:,} UKPence**. Don't say the state never enabled you.",
    "🧾 Approved on the first try, which says more about our standards than your situation. **{amount:,} UKPence** for <@{uid}>.",
    "🧾 The benefits office stamped it: **{amount:,} UKPence** to <@{uid}>. We won't ask what happened to yesterday's.",
    "🧾 Cost-of-living top-up incoming: **+{amount:,} UKPence** for <@{uid}>. Every little enables.",
    "🧾 <@{uid}>, the welfare system looked the other way. **{amount:,} UKPence** is yours. Do your worst.",
    "🧾 Signed, sealed, deposited. <@{uid}> gets **{amount:,} UKPence**. Try not to make this a lifestyle.",
]
# Personal lines for the heaviest claimants, mixed in with the house pool so the regulars
# get something written for them rather than the same fifteen jokes forever.
#
# Nothing here is fixed in place. The claimant is addressed with a mention or {name}, which
# follow their nickname on the day, and every figure is a placeholder filled from the ledger
# at claim time by _benefits_stats. The numbers were written in at first and were wrong by
# the same evening - somebody up at the tables goes down, somebody who had never gambled
# does. A line whose figures no longer resolve is dropped rather than printed, so a joke
# stops appearing when it stops being true.
#
# Drawn from the top 50 by claim count. Four of that fifty have left and are not here.
# Someone falling off the list keeps their lines; the ids are stable even when names aren't.
_BENEFITS_PERSONAL_CHANCE = 0.5   # how often a regular gets their own line over the pool
_BENEFITS_PERSONAL = {
 # 67 claims
 "479207279850291221": [
  "🧾 Claim number **{claims}** approved. <@{uid}> takes **{amount:,} UKPence**. The case worker stopped reading your forms long ago and just stamps them.",
  "🧾 **{amount:,} UKPence** for <@{uid}>, who has fed **{mines_lost:,}** into the mines. The state admires the commitment, not the strategy.",
  "🧾 The office knows your name, your face and the sound of your footsteps. **{amount:,} UKPence**, <@{uid}>. That's **{claims}** now.",
 ],
 # 40 claims
 "564147759108718664": [
  "🧾 **{amount:,} UKPence** for <@{uid}>. Curious how the generosity arrives in lumps of **{paid_out:,}** and the poverty arrives daily.",
  "🧾 Approved. <@{uid}> gets **{amount:,} UKPence** - a sliver of the **{paid_out:,}** you've handed to mates, but this one's the taxpayer's, so it counts.",
 ],
 # 38 claims
 "1283837687551361117": [
  "🧾 **{amount:,} UKPence** to <@{uid}>, who has played **{casino_games:,}** casino rounds. That is more rounds than most people have had hot dinners.",
  "🧾 Approved. <@{uid}> pockets **{amount:,} UKPence** and owns **{shop_items}** things from the shop, which is **{shop_spent:,}** of not learning.",
 ],
 # 37 claims
 "596789292991512619": [
  "🧾 **{amount:,} UKPence** for <@{uid}>. You don't gamble, you shop - **{shop_items}** items and **{shop_spent:,}** gone. The more dangerous habit.",
  "🧾 Claim approved. <@{uid}> receives **{amount:,} UKPence**. Straight to the shop with it, as tradition demands.",
 ],
 # 37 claims
 "276119377395449856": [
  "🧾 **{amount:,} UKPence** for <@{uid}>. Between this and **{paid_in_n}** bailouts from your mates, you're practically a registered charity.",
  "🧾 Approved, with a note in the margin. <@{uid}> takes **{amount:,} UKPence**. The fraud team remembers you fondly.",
 ],
 # 32 claims
 "795003706717372462": [
  "🧾 **{amount:,} UKPence** to <@{uid}>. **{paid_out:,}** out, **{paid_in:,}** in - a rate of flow the Treasury would call alarming.",
  "🧾 Approved. <@{uid}> gets **{amount:,} UKPence**. See if you can keep this one in the account for a whole hour.",
 ],
 # 31 claims
 "311526098884362242": [
  "🧾 **{amount:,} UKPence** for <@{uid}>, the server's most cautious degenerate. Higher or lower? Higher, obviously.",
  "🧾 Payment through. <@{uid}> receives **{amount:,} UKPence**, against lifetime losses of **{casino_lost:,}**. Nearly square.",
 ],
 # 25 claims
 "285860055570579457": [
  "🧾 **{amount:,} UKPence** for <@{uid}>. That is **{pct_of_casino}%** of what the casino has taken off you. It will not touch the sides.",
  "🧾 The state grants <@{uid}> **{amount:,} UKPence**, or **{pct_of_roulette}%** of what the roulette wheel alone has had. Baby steps.",
  "🧾 Approved. <@{uid}> is **{casino_lost:,}** down to the casino machine. **{amount:,} UKPence**, and our sympathies.",
 ],
 # 25 claims
 "966101821527588885": [
  "🧾 **{amount:,} UKPence** for <@{uid}>. The dealer is already shuffling. Don't.",
  "🧾 Approved. <@{uid}> receives **{amount:,} UKPence**, with **{blackjack_lost:,}** already left at the table. Twist, stick, or go outside.",
 ],
 # 24 claims
 "235505165321502731": [
  "🧾 **{amount:,} UKPence** to <@{uid}>. The mines are that way, as if you needed telling.",
  "🧾 Claim through. <@{uid}> gets **{amount:,} UKPence**. The last **{mines_lost:,}** went the same way this will.",
 ],
 # 21 claims
 "265927604303953920": [
  "🧾 **{amount:,} UKPence** for <@{uid}>. The slots await their tithe of **{slots_lost:,}** and counting.",
  "🧾 Approved. <@{uid}> receives **{amount:,} UKPence**. Try not to put this straight back into the machine.",
 ],
 # 18 claims
 "1398652914737741956": [
  "🧾 **{amount:,} UKPence** for <@{uid}>, shut up **{shut}** times and still going. The state respects persistence.",
  "🧾 Approved. <@{uid}> gets **{amount:,} UKPence**. Please don't spend it all on being told to be quiet.",
 ],
 # 16 claims
 "1453319508587450514": [
  "🧾 **{amount:,} UKPence** for <@{uid}>. You've given away **{paid_out:,}**, so this is less a benefit than a rounding error.",
  "🧾 The state matches your generosity with **{amount:,} UKPence**, <@{uid}>. Against **{paid_out:,}** that is not a fair match and we both know it.",
 ],
 # 15 claims
 "1457814413913489480": [
  "🧾 **{amount:,} UKPence** for <@{uid}>. You've paid out **{paid_out:,}** and been paid back **{paid_in:,}**. Someone owes you.",
  "🧾 Approved. <@{uid}> takes **{amount:,} UKPence**. Consider keeping this one for yourself, as a treat.",
 ],
 # 14 claims
 "812666688184909834": [
  "🧾 **{amount:,} UKPence** for <@{uid}>. You locked **{bonded:,}** in a bond and then queued at the job centre. Respect.",
  "🧾 Approved. <@{uid}> gets **{amount:,} UKPence**. Straight into savings, if we know you.",
 ],
 # 13 claims
 "1022210566871322754": [
  "🧾 **{amount:,} UKPence** to <@{uid}>. The shop has had **{shop_spent:,}** off you and the casino **{casino_lost:,}**. Genuinely impressive.",
  "🧾 Claim approved. <@{uid}> receives **{amount:,} UKPence**. Something in the shop is already calling.",
 ],
 # 12 claims
 "355962189175324674": [
  "🧾 **{amount:,} UKPence** for <@{uid}>, who is **{casino_up:,}** UP at the casino and turned up at the job centre anyway. Astonishing.",
  "🧾 Approved, reluctantly. <@{uid}> gets **{amount:,} UKPence**. **{casino_games:,}** hands played and still claiming hardship.",
 ],
 # 12 claims
 "1337505182904225934": [
  "🧾 **{amount:,} UKPence** for <@{uid}>. Pillage responsibly.",
  "🧾 Approved. <@{uid}> receives **{amount:,} UKPence** from the public purse. No raiding required.",
 ],
 # 12 claims
 "860098855621623809": [
  "🧾 **{amount:,} UKPence** for <@{uid}>, **{casino_up:,}** up at the tables. This is the single funniest claim we process.",
  "🧾 Approved on a technicality. <@{uid}> gets **{amount:,} UKPence**. You've given away **{paid_out:,}**, so do try to make it last.",
 ],
 # 10 claims
 "1103015741994827828": [
  "🧾 **{amount:,} UKPence** for <@{uid}>. Mind the mines, they've had **{mines_lost:,}** off you already.",
  "🧾 Claim through. <@{uid}> receives **{amount:,} UKPence**. Try the shop, it separates you from it more slowly.",
 ],
 # 9 claims
 "1525639310697562232": [
  "🧾 **{amount:,} UKPence** for <@{uid}>. Sanctioned repeatedly for fraud and the state is still handing you money. Extraordinary.",
  "🧾 Approved, against everyone's advice. <@{uid}> receives **{amount:,} UKPence**. The fraud office is watching this one specifically.",
 ],
 # 8 claims
 "797901947734065162": [
  "🧾 **{amount:,} UKPence** for <@{uid}>, sat on **{balance:,}** and claiming anyway. Frugal or shameless, we can't tell.",
  "🧾 Approved. <@{uid}> gets **{amount:,} UKPence**. Not one ruinous decision on your record. Keep it that way.",
 ],
 # 8 claims
 "335303938301624324": [
  "🧾 **{amount:,} UKPence** for <@{uid}>, up a grand total of **{casino_up:,}** at the casino. A man who knows when to leave.",
  "🧾 Approved. <@{uid}> receives **{amount:,} UKPence**. Your gambling career remains, technically, profitable.",
 ],
 # 7 claims
 "1113593004083654707": [
  "🧾 **{amount:,} UKPence** for <@{uid}>. **{casino_games}** casino games, not one win between them{no_casino_wins}. The state admires a clean sheet.",
  "🧾 Approved. <@{uid}> gets **{amount:,} UKPence**, which comfortably beats your lifetime winnings.",
 ],
 # 7 claims
 "958093967310872617": [
  "🧾 **{amount:,} UKPence** for <@{uid}>. **{balance:,}** in the account and **{paid_out:,}** posted to other people. Curious accounting.",
  "🧾 Approved. <@{uid}> receives **{amount:,} UKPence**. Please keep at least some of it.",
 ],
 # 6 claims
 "352040780543557634": [
  "🧾 **{amount:,} UKPence** for <@{uid}>, who has **{balance:,}** in the bank. The threshold is a suggestion and you found the loophole.",
  "🧾 Approved, teeth gritted. <@{uid}> receives **{amount:,} UKPence**. Your **{bonded:,}** in bonds earns more than this while you read it.",
 ],
 # 6 claims
 "450760126765334539": [
  "🧾 **{amount:,} UKPence** for <@{uid}>. **{casino_games}** casino games, one of them **{worst_loss:,}** down. Quality over quantity.",
  "🧾 Approved. <@{uid}> gets **{amount:,} UKPence**. Perhaps sit this round out.",
 ],
 # 6 claims
 "640108968139554827": [
  "🧾 **{amount:,} UKPence** for <@{uid}>, who has made **{paid_out_n}** separate payments to other people. A one-man welfare state.",
  "🧾 Approved. <@{uid}> receives **{amount:,} UKPence**. The state is sending support, over.",
 ],
 # 5 claims
 "1519692185031676024": [
  "🧾 **{amount:,} UKPence** for <@{uid}>, who has never once been near the casino{never_gambled}. A genuinely clean record. Suspicious.",
  "🧾 Approved with a commendation. <@{uid}> gets **{amount:,} UKPence** and an untouched gambling history{never_gambled}.",
 ],
 # 5 claims
 "1497606176660127956": [
  "🧾 **{amount:,} UKPence** for <@{uid}>, delivered on time, in full, and of the promised quality. No complaints will be accepted.",
  "🧾 Approved. <@{uid}> receives **{amount:,} UKPence**. A small, respectable sum for a small, respectable operator.",
 ],
 # 5 claims
 "719962546995593287": [
  "🧾 **{amount:,} UKPence** for <@{uid}>, who has won **{best_win:,}** in one go and lost **{worst_loss:,}** in another. This will feel very small.",
  "🧾 Approved. <@{uid}> gets **{amount:,} UKPence**, or roughly **{pct_of_casino}%** of what's already gone.",
 ],
 # 5 claims
 "1129755209195859988": [
  "🧾 **{amount:,} UKPence** for <@{uid}>. **{casino_games}** casino games, **{casino_up:,}** up, and you stopped. The state would like to study you.",
  "🧾 Approved. <@{uid}> receives **{amount:,} UKPence**. Whatever you're doing, keep doing it.",
 ],
 # 5 claims
 "1486905808515104858": [
  "🧾 **{amount:,} UKPence** for <@{uid}>. Back to the blockade with you.",
  "🧾 Approved. <@{uid}> gets **{amount:,} UKPence**. Say nothing, spend it quietly.",
 ],
 # 4 claims
 "792139113587277835": [
  "🧾 **{amount:,} UKPence** for <@{uid}>. **{bonded:,}** tied up in bonds and here you are with a begging bowl. Bold.",
  "🧾 Approved. <@{uid}> receives **{amount:,} UKPence**. Shut up **{shut}** times and still claiming, which is its own kind of resilience.",
 ],
 # 4 claims
 "811136986018480128": [
  "🧾 **{amount:,} UKPence** for <@{uid}>, whose entire net worth was **{balance:,}** a moment ago. Upward trajectory.",
  "🧾 Approved. <@{uid}> gets **{amount:,} UKPence**. The blackjack table is not your friend, whatever it says.",
 ],
 # 4 claims
 "1457809767794872350": [
  "🧾 **{amount:,} UKPence** for <@{uid}>. The mines have had **{mines_lost:,}** off you. This is not a rescue, it's a gesture.",
  "🧾 Approved. <@{uid}> receives **{amount:,} UKPence**. Good afternoon to you too.",
 ],
 # 4 claims
 "811987329707147264": [
  "🧾 **{amount:,} UKPence** for <@{uid}>, quietly **{casino_up:,}** up at the casino and claiming benefits regardless. This is Sparta, apparently.",
  "🧾 Approved. <@{uid}> gets **{amount:,} UKPence**. **{casino_games}** games and still ahead. Don't ruin it.",
 ],
 # 3 claims
 "281162022320734218": [
  "🧾 **{amount:,} UKPence** for <@{uid}>. Sideways into the mines as usual.",
  "🧾 Approved. <@{uid}> receives **{amount:,} UKPence**. Small claim, small stakes, no complaints.",
 ],
 # 3 claims
 "1486077107695255714": [
  "🧾 **{amount:,} UKPence** for <@{uid}>. The chests have had **{chest_lost:,}** off you and you keep opening them.",
  "🧾 Approved. <@{uid}> gets **{amount:,} UKPence**. There is nothing good in the next one either.",
 ],
 # 3 claims
 "1429772674913009725": [
  "🧾 **{amount:,} UKPence** for <@{uid}>. First of your name, last in the queue, **{balance:,}** in the account.",
  "🧾 Approved. <@{uid}> receives **{amount:,} UKPence**. **{casino_games}** casino games was quite enough, wasn't it.",
 ],
 # 3 claims
 "1086932531498201170": [
  "🧾 **{amount:,} UKPence** for <@{uid}>. **{casino_games}** games, nothing banked, still smiling.",
  "🧾 Approved. <@{uid}> gets **{amount:,} UKPence**. Straight back into the blockade, no doubt.",
 ],
 # 3 claims
 "927502890065604650": [
  "🧾 **{amount:,} UKPence** for <@{uid}>. You prefer the darts and the mines still found **{mines_lost:,}** to take off you.",
  "🧾 Approved. <@{uid}> receives **{amount:,} UKPence**. Meal-deal money, at best.",
 ],
 # 2 claims
 "822525776095608914": [
  "🧾 **{amount:,} UKPence** for <@{uid}>. Landed gentry with **{bonded:,}** in bonds, queueing for a handout. Marvellous.",
  "🧾 Approved. <@{uid}> gets **{amount:,} UKPence**. The estate thanks the taxpayer.",
 ],
 # 2 claims
 "544186272864796672": [
  "🧾 **{amount:,} UKPence** for <@{uid}>. **{casino_lost:,}** lost at the tables and only **{claims}** claims made. Restraint, of a sort.",
  "🧾 Approved. <@{uid}> receives **{amount:,} UKPence**. Sausage roll money.",
 ],
 # 2 claims
 "1356976795047690261": [
  "🧾 **{amount:,} UKPence** for <@{uid}>, who once won **{best_win:,}** and has been coasting on it ever since.",
  "🧾 Approved. <@{uid}> gets **{amount:,} UKPence**. Barely a gambler, barely a claimant, ideal citizen.",
 ],
 # 2 claims
 "1416363376589672450": [
  "🧾 **{amount:,} UKPence** for <@{uid}>. **{casino_games}** casino game in your entire life, **{casino_lost:,}** down, never went back. Wise.",
  "🧾 Approved. <@{uid}> receives **{amount:,} UKPence**, comfortably more than your lifetime gambling losses of **{casino_lost:,}**.",
 ],
}


# Lines for anybody at all, built from the same live figures as the personal ones. The
# personal pool only covers the fifty heaviest claimants; everyone else was getting the
# same seventeen house jokes forever. These fill that in without anyone having to be
# written for: whichever figures resolve for the claimant, those lines are eligible, and
# the rest sit out. Somebody who has never gambled and somebody thirty thousand down get
# completely different sets out of the same list.
_BENEFITS_DATA_CHANCE = 0.6       # how often to prefer a figure over a plain house line
_BENEFITS_DATA = [
 # what the casino has had
 "🧾 **{amount:,} UKPence** for <@{uid}>. The casino has taken **{casino_lost:,}** off you, so consider this a microscopic refund from the wrong institution.",
 "🧾 Approved: **{amount:,} UKPence** to <@{uid}>. That is **{pct_of_casino}%** of what you've lost at the tables. Try not to donate it right back.",
 "🧾 <@{uid}> receives **{amount:,} UKPence**. You are **{casino_lost:,}** down overall, so the state is evidently running an informal bailout scheme.",
 "🧾 **{amount:,} UKPence** for <@{uid}>, across **{casino_games:,}** casino games and still qualifying for hardship. Truly breathtaking consistency.",
 "🧾 Payment through. <@{uid}> gets **{amount:,} UKPence**. Your worst single loss was **{worst_loss:,}**, so do try to show some restraint.",
 "🧾 **{amount:,} UKPence** for <@{uid}>, who once won **{best_win:,}** in a single game and managed to end up back here anyway. Artistry.",
 "🧾 Approved. <@{uid}> takes **{amount:,} UKPence**. The mines alone have swallowed **{mines_lost:,}** of your money.",
 "🧾 **{amount:,} UKPence** for <@{uid}>. Blackjack is **{blackjack_lost:,}** ahead of you. The dealer sends thoughts and prayers.",
 "🧾 <@{uid}> gets **{amount:,} UKPence**. The wheel has had **{roulette_lost:,}**, and the wheel does not do welfare.",
 "🧾 **{amount:,} UKPence** for <@{uid}>. The slots took **{slots_lost:,}**. They are waiting patiently for this too.",
 "🧾 Approved. <@{uid}> receives **{amount:,} UKPence**, roughly **{pct_of_mines}%** of what you detonated in the mines.",

 # the ones who are somehow ahead
 "🧾 **{amount:,} UKPence** for <@{uid}>, who is **{casino_up:,}** UP at the casino. Claiming the dole while in profit is shameless, yet technically legal.",
 "🧾 Approved with deep administrative suspicion. <@{uid}> gets **{amount:,} UKPence** despite being **{casino_up:,}** ahead at the tables.",
 "🧾 <@{uid}> receives **{amount:,} UKPence**. **{casino_up:,}** in casino profit and still in the benefits queue. Inspiring audacity.",

 # the abstainers
 "🧾 **{amount:,} UKPence** for <@{uid}>{never_gambled}, who has never played a single casino game. Broke through sheer normal living. Refreshing.",
 "🧾 Approved. <@{uid}> gets **{amount:,} UKPence** with a spotless gambling record{never_gambled}. The state is genuinely puzzled as to where your money went.",
 "🧾 **{amount:,} UKPence** for <@{uid}>, who has played **{casino_games}** rounds and won none of them{no_casino_wins}. A masterclass in persistence over probability.",

 # money moving between people
 "🧾 **{amount:,} UKPence** for <@{uid}>. You have given **{paid_out:,}** away to others, which the state finds either saintly or completely unhinged.",
 "🧾 Approved. <@{uid}> receives **{amount:,} UKPence** on top of the **{paid_in:,}** your mates already sent you. Quite the syndication.",
 "🧾 <@{uid}> gets **{amount:,} UKPence**. That's **{paid_in_n}** separate friends you've tapped for cash before coming to the taxpayer.",
 "🧾 **{amount:,} UKPence** for <@{uid}>, who has made **{paid_out_n}** outgoing transfers and now needs a government rescue package.",

 # what else they have got
 "🧾 **{amount:,} UKPence** for <@{uid}>. You have **{bonded:,}** locked in bonds, so this is just government-subsidised pocket money.",
 "🧾 Approved. <@{uid}> receives **{amount:,} UKPence**. You've spent **{shop_spent:,}** at the shop; please try not to buy another novelty item.",
 "🧾 <@{uid}> gets **{amount:,} UKPence**. **{shop_items}** luxury items in your inventory and not a scrap of food.",
 "🧾 **{amount:,} UKPence** for <@{uid}>, lifting you from a staggering **{balance:,} UKPence**. Try not to go wild.",

 # the rest of the record
 "🧾 Claim number **{claims}** approved. **{amount:,} UKPence** for <@{uid}>. The case worker has given up reading your forms.",
 "🧾 **{amount:,} UKPence** for <@{uid}>, shut up **{shut}** times and still drawing a public pension. Pure democracy.",
 "🧾 Approved. <@{uid}> receives **{amount:,} UKPence**. Put it towards something resembling common sense.",
 "🧾 **{amount:,} UKPence** for <@{uid}>. Another drip into the bottomless bucket.",
]


_BENEFITS_PERSONAL_RICH = {
    "812666688184909834": [
        "💼 <@{uid}>, you're holding **{bal:,} UKPence** with **{bonded:,}** in bonds. The state is not topping up your private pension.",
        "💼 Claim denied, <@{uid}>. **{bal:,} UKPence** in the wallet while sitting on **{bonded:,}** in bonds. Pure capitalist behavior.",
    ],
    "822525776095608914": [
        "💼 Denied, Lord <@{uid}>. **{bal:,} UKPence** plus **{bonded:,}** in the family trust fund is well over the {threshold:,} line.",
        "💼 <@{uid}>, landed gentry with **{bal:,} UKPence** in hand. The estate can support itself today.",
    ],
    "352040780543557634": [
        "💼 <@{uid}>, **{bal:,} UKPence** in hand and **{bonded:,}** earning interest. Nice try, but the loop is closed today.",
        "💼 Denied: with **{bal:,} UKPence** in the bank, the {threshold:,} threshold says you're doing just fine.",
    ],
    "285860055570579457": [
        "💼 <@{uid}>, you've got **{bal:,} UKPence** in your wallet. The roulette wheel will have to wait for honest earnings.",
        "💼 **{bal:,} UKPence** in hand. The DWP says you are far too flush for welfare today, <@{uid}>.",
    ],
    "564147759108718664": [
        "💼 <@{uid}>, you have **{bal:,} UKPence**. Stop handing out **{paid_out:,}** to everyone else if you want the dole.",
        "💼 The state reviewed your **{bal:,} UKPence**, <@{uid}>. Keep some for yourself and you won't need benefits.",
    ],
    "596789292991512619": [
        "💼 <@{uid}> has **{bal:,} UKPence**. That's enough for **{shop_items}** more things from the shop. No welfare for you.",
        "💼 Denied. You've spent **{shop_spent:,}** at the shop and still have **{bal:,} UKPence**. Off you pop.",
    ],
    "1283837687551361117": [
        "💼 **{bal:,} UKPence** in the account, <@{uid}>. Go play round number **{casino_games}** with your own money.",
        "💼 Denied: **{bal:,} UKPence** in hand. You don't need a handout, you need to step away from the tables.",
    ],
    "1398652914737741956": [
        "💼 <@{uid}>, you're flush with **{bal:,} UKPence**. Quietly take your money and move along.",
        "💼 Denied, <@{uid}>. **{bal:,} UKPence** is comfortably over {threshold:,}. Shhh and get back to work.",
    ],
    "355962189175324674": [
        "💼 <@{uid}>, you're **{casino_up:,}** up at the tables and have **{bal:,} UKPence**. Genuinely shameless.",
        "💼 Hardship claim rejected. You're in profit by **{casino_up:,}** and holding **{bal:,} UKPence**.",
    ],
    "860098855621623809": [
        "💼 <@{uid}>, **{bal:,} UKPence** in the bank and **{casino_up:,}** casino profit. The council tax is going up because of people like you.",
        "💼 Denied. **{bal:,} UKPence** on the books. Come back when the tables have taken it back.",
    ],
    "479207279850291221": [
        "💼 <@{uid}>, you've got **{bal:,} UKPence**. That will buy plenty of picks for the mines without government assistance.",
        "💼 Denied: **{bal:,} UKPence** on file. The case worker knows you're good for it.",
    ],
    "795003706717372462": [
        "💼 <@{uid}>, you're sitting on **{bal:,} UKPence**. That should sustain your transfer velocity for a while.",
    ],
    "311526098884362242": [
        "💼 <@{uid}>, with **{bal:,} UKPence** you can afford your own higher/lower guesses today.",
    ],
    "966101821527588885": [
        "💼 <@{uid}>, **{bal:,} UKPence** is enough to hit or stand on your own dime. Denied.",
    ],
    "235505165321502731": [
        "💼 <@{uid}>, **{bal:,} UKPence** is plenty for mining equipment. No relief payment today.",
    ],
    "265927604303953920": [
        "💼 <@{uid}>, you have **{bal:,} UKPence** to your name. No dole for comfortable players.",
    ],
    "1525639310697562232": [
        "💼 <@{uid}>, **{bal:,} UKPence** in the account. The fraud squad will not be issuing pocket money today.",
    ],
    "797901947734065162": [
        "💼 <@{uid}>, **{bal:,} UKPence** banked. Your spotless frugality disqualifies you today.",
    ],
    "335303938301624324": [
        "💼 <@{uid}>, holding **{bal:,} UKPence** and **{casino_up:,}** in casino profit. Denied.",
    ],
    "1129755209195859988": [
        "💼 <@{uid}>, **{bal:,} UKPence** and **{casino_up:,}** up. The state does not subsidise winners.",
    ],
    "792139113587277835": [
        "💼 <@{uid}>, **{bal:,} UKPence** in wallet and **{bonded:,}** in bonds. Truly shameless.",
    ],
    "811987329707147264": [
        "💼 <@{uid}>, **{bal:,} UKPence** in hand and **{casino_up:,}** ahead. Sparta does not do handouts today.",
    ],
}

_BENEFITS_DATA_RICH = [
    "💼 You're sat on **{bal:,} UKPence** and have **{bonded:,}** locked in bonds. The DWP is a welfare office, not your private family wealth office.",
    "💼 **{bal:,} UKPence** in hand and **{casino_up:,}** up at the tables. You should be funding this department, not begging from it.",
    "💼 Denied. With **{bal:,} UKPence** in the wallet and **{shop_spent:,}** blown on shop frivolities, you are remarkably far from destitution.",
    "💼 You gave away **{paid_out:,}** to your mates and still have **{bal:,} UKPence**. Come back when your philanthropy actually hurts.",
    "💼 **{bal:,} UKPence** in the bank. Even after losing **{casino_lost:,}** to machines, the state concludes you will survive without emergency aid.",
    "💼 Denied. **{bal:,} UKPence** in your account and you're queueing for the dole? Have some dignity.",
    "💼 Claim refused. **{bal:,} UKPence** liquid and **{bonds}** active bonds maturing. Do you think we don't have eyes?",
    "💼 **{bal:,} UKPence** in the bank. You've won **{best_win:,}** in a single round before; go repeat the trick.",
    "💼 Denied: with **{bal:,} UKPence** in the wallet and **{shop_items}** items in your inventory, you are not skint, just greedy.",
    "💼 **{bal:,} UKPence** in hand. You received **{paid_in:,}** from friends and now want public funds too. Astonishing cheek.",
]

_BENEFITS_PERSONAL_ALREADY = {
    "479207279850291221": [
        "🧾 <@{uid}>, you've already had your dole today. That's **{claims}** claims on your file - back to the mines until <t:{ts}:R>.",
        "🧾 The case worker already stamped your paper today, <@{uid}>. Shut the door on your way out and return <t:{ts}:R>.",
    ],
    "1398652914737741956": [
        "🧾 <@{uid}>, today's payment is already through. Shhh until midnight <t:{ts}:R>.",
        "🧾 Told to be quiet **{shut}** times and still back for double dole. The till reopens <t:{ts}:R>.",
    ],
    "285860055570579457": [
        "🧾 <@{uid}>, you already claimed today's handout. Step away from the roulette table until <t:{ts}:R>.",
        "🧾 Defeated by the once-per-day rule. Come back <t:{ts}:R>, <@{uid}>.",
    ],
    "1283837687551361117": [
        "🧾 <@{uid}>, one giro per calendar day. You've played **{casino_games:,}** rounds; go take a breather until <t:{ts}:R>.",
        "🧾 Already claimed today, <@{uid}>. Put the shop catalogue down until <t:{ts}:R>.",
    ],
    "564147759108718664": [
        "🧾 <@{uid}>, you already collected today's money. Don't give it all away before <t:{ts}:R>.",
    ],
    "795003706717372462": [
        "🧾 Today's giro is gone, <@{uid}>. Try to keep whatever is left in the account until <t:{ts}:R>.",
    ],
    "311526098884362242": [
        "🧾 <@{uid}>, you've claimed your share today. The higher/lower deck rests until <t:{ts}:R>.",
    ],
    "966101821527588885": [
        "🧾 Already claimed today, <@{uid}>. Step back from the blackjack table until <t:{ts}:R>.",
    ],
    "235505165321502731": [
        "🧾 <@{uid}>, today's allowance is spent. Put down the mining pick until <t:{ts}:R>.",
    ],
    "265927604303953920": [
        "🧾 Today's giro has been dispatched, <@{uid}>. No more handouts until <t:{ts}:R>.",
    ],
    "1525639310697562232": [
        "🧾 <@{uid}>, you've had your legal claim today. Don't make the fraud squad look at you again before <t:{ts}:R>.",
    ],
    "792139113587277835": [
        "🧾 <@{uid}>, you've claimed today. Go check your **{bonded:,}** in bonds until <t:{ts}:R>.",
    ],
}

_BENEFITS_DATA_ALREADY = [
    "🧾 You're on day **{streak}** of your welfare streak. Miraculously, one claim per day applies to you too. Reopens <t:{ts}:R>.",
    "🧾 Claim number **{claims}** was already processed today, <@{uid}>. Give the printing press a breather until <t:{ts}:R>.",
    "🧾 Deposited today's payout straight into the casino ({casino_lost:,} down overall)? The office remains closed until <t:{ts}:R>.",
    "🧾 The giro was already cashed today. The mines took **{mines_lost:,}** off you and won't be getting any more public funds until <t:{ts}:R>.",
    "🧾 Back already? That's **{claims}** lifetime claims and today's quota is exhausted. Reopens <t:{ts}:R>.",
    "🧾 You've had today's handout, <@{uid}>. Go admire your **{shop_items}** shop items and come back <t:{ts}:R>.",
    "🧾 Already claimed today. Shut up **{shut}** times and still hammering on the DWP door. Return <t:{ts}:R>.",
    "🧾 One claim every 24 hours. Checking early will not accelerate British bureaucracy. Try <t:{ts}:R>.",
    "🧾 Already had your giro today. Go spend some of that **{paid_in:,}** your mates sent you and return <t:{ts}:R>.",
]

_BENEFITS_PERSONAL_FRAUD_WARN = {
    "276119377395449856": [
        "🕵️ <@{uid}>, the fraud team flagged **{out:,} UKPence** leaving your wallet. We know your tricks - one warning only.",
    ],
    "564147759108718664": [
        "🕵️ <@{uid}>, gifting **{out:,} UKPence** to friends doesn't qualify you for the dole. Claim blocked; don't make us ban you.",
    ],
    "795003706717372462": [
        "🕵️ <@{uid}>, that velocity of **{out:,} UKPence** out the door didn't go unnoticed. Warning issued.",
    ],
    "1525639310697562232": [
        "🕵️ <@{uid}>, **{out:,} UKPence** shifted again. The fraud office has you under a microscope. Last warning.",
    ],
    "285860055570579457": [
        "🕵️ <@{uid}>, moving **{out:,} UKPence** off-balance won't work on the DWP. Warning recorded.",
    ],
    "1398652914737741956": [
        "🕵️ <@{uid}>, shipping **{out:,} UKPence** away before claiming is not subtle. One warning.",
    ],
}

_BENEFITS_DATA_FRAUD_WARN = [
    "🕵️ Outbound ledger check: you sent **{out:,} UKPence** across **{paid_out_n}** transfers recently. We count that as yours. Warning logged.",
    "🕵️ Shifting **{out:,} UKPence** to mates right before your **{claims}th** benefits claim? We see you. Claim denied; consider this your final warning.",
    "🕵️ Nice try hiding **{out:,} UKPence**. You have **{claims}** legitimate claims on record, don't spoil it with fraud. Warning issued.",
    "🕵️ We clocked **{out:,} UKPence** moving out. Even with **{casino_lost:,}** in casino losses, parking money is a strike. Back off.",
]

_BENEFITS_PERSONAL_FRAUD_BAN = {
    "1525639310697562232": [
        "🚫 <@{uid}>, repeat offender status upgraded. **{days} days** in the sin bin for benefits fraud.",
    ],
    "276119377395449856": [
        "🚫 <@{uid}>, the margin note said you'd try this again. Banned for **{days} days**.",
    ],
    "564147759108718664": [
        "🚫 <@{uid}>, the charity act ended in the fraud office. **{days} days** without benefits.",
    ],
    "285860055570579457": [
        "🚫 <@{uid}>, caught trying to bypass the means test. Sanctioned for **{days} days**.",
    ],
}

_BENEFITS_DATA_FRAUD_BAN = [
    "🚫 **Benefits fraud confirmed.** Shifted **{out:,} UKPence** to cheat the means test. Offense number **{offenses}**: banned for **{days} days**.",
    "🚫 Caught red-handed hiding **{out:,} UKPence**. That's **{offenses}** strikes on your file. Sanctioned for **{days} days**.",
    "🚫 The DWP fraud squad is unimpressed. After **{claims}** claims, you chose fraud. Banned for **{days} days**.",
    "🚫 You moved **{out:,} UKPence** and tried to feign hardship. Banned for **{days} days**.",
]

_BENEFITS_PERSONAL_BANNED = {
    "1525639310697562232": [
        "🚫 <@{uid}>, the VIP lounge of the fraud blacklist remains yours until <t:{ts}:R>.",
    ],
    "276119377395449856": [
        "🚫 <@{uid}>, no shortcuts out of the sin bin. Come back <t:{ts}:R>.",
    ],
    "285860055570579457": [
        "🚫 <@{uid}>, the sanction stands. Back to the tables and debt until <t:{ts}:R>.",
    ],
    "564147759108718664": [
        "🚫 <@{uid}>, you're serving your fraud suspension. Re-entry granted <t:{ts}:R>.",
    ],
}

_BENEFITS_DATA_BANNED = [
    "🚫 You are currently serving a benefits sanction (offense **{offenses}**). The doors unlock <t:{ts}:R>.",
    "🚫 Still in the doghouse. With **{claims}** lifetime claims, you know the rules. Ban lifts <t:{ts}:R>.",
    "🚫 DWP compliance hold in effect. Access returns <t:{ts}:R>.",
    "🚫 Sanction remains active. You can wait until <t:{ts}:R> or pay off the fine.",
]

_BENEFITS_RICH = [
    "💼 You've got **{bal:,} UKPence** — benefits are for people with under {threshold:,}. Do something productive.",
    "💼 Claim denied. **{bal:,} UKPence** is vastly too rich for state assistance (cutoff is {threshold:,}).",
    "💼 The DWP reviewed your balance of **{bal:,} UKPence** and decided you'll live. Off you pop.",
    "💼 Nice try, but **{bal:,} UKPence** is comfortably over {threshold:,}. We don't hand out pocket money to the comfortable.",
    "💼 Hardly destitute with **{bal:,} UKPence**. Come back when you're properly skint (under {threshold:,}).",
    "💼 The means test says no. **{bal:,} UKPence** sails straight past the {threshold:,} line. Tighten your belt.",
    "💼 With **{bal:,} UKPence** in the bank, you don't need a handout, you need shame.",
    "💼 Computer says no. **{bal:,} UKPence** disqualifies you instantly (limit is {threshold:,}).",
    "💼 We don't subsidise the wealthy. **{bal:,} UKPence** puts you firmly in the 'doing fine' bracket (cutoff {threshold:,}).",
    "💼 Claim rejected: **{bal:,} UKPence** would make half the server weep. Back over {threshold:,} you go.",
    "💼 Save it for someone who actually needs it. **{bal:,} UKPence** is well above {threshold:,}. Denied.",
    "💼 You're not on your uppers with **{bal:,} UKPence**. The {threshold:,} cutoff says you will manage.",
    "💼 The fund is for the broke, not the comfortable. **{bal:,} UKPence** disqualifies you.",
    "💼 Sorry, your **{bal:,} UKPence** triggered the 'doing alright, actually' filter. No free cash today.",
    "💼 No benefits for the solvent. Come back when you have less than {threshold:,}, you've got **{bal:,} UKPence**.",
]
_BENEFITS_ALREADY = [
    "🧾 You've already had your benefits today. The state is a bureaucracy, not an infinite tap. Office reopens <t:{ts}:R>.",
    "🧾 One claim a day, that's the rule. It applies even to you. Back <t:{ts}:R>.",
    "🧾 Today's giro is gone. Next one <t:{ts}:R>.",
    "🧾 Patience. Your next assessment is <t:{ts}:R>.",
    "🧾 You've drained today's allowance. Reopens <t:{ts}:R>.",
    "🧾 Already claimed, and knowing you, already spent. Try again <t:{ts}:R>.",
    "🧾 The till's firmly shut for the day. Next handout <t:{ts}:R>.",
    "🧾 No double-dipping. The system unlocks <t:{ts}:R>.",
    "🧾 That's your lot for today. The caseworker is having tea. Reopens <t:{ts}:R>.",
    "🧾 Easy now. One payment per calendar day. Back <t:{ts}:R>.",
    "🧾 The cupboard's bare until midnight UK. Return <t:{ts}:R>.",
    "🧾 You've had your dole today. Come back <t:{ts}:R>.",
    "🧾 Claim on cooldown. The shutters lift <t:{ts}:R>.",
    "🧾 Today's giro is spent. Sit on your hands until <t:{ts}:R>.",
    "🧾 We gave at the office today. Try again <t:{ts}:R>.",
]
_BENEFITS_FRAUD_WARN = [
    "🕵️ Hang on. You shifted **{out:,} UKPence** to other users recently, and we count that as yours. Parking money on mates doesn't make you poor. Denied — consider this your one warning.",
    "🕵️ The fraud office clocked **{out:,} UKPence** leaving your account. Do you think we run this department on an abacus? Denied, and don't try it again.",
    "🕵️ Benefits are means-tested on what you've **had**, not what you've hidden. You moved **{out:,} UKPence** out. No claim today — don't push your luck.",
    "🕵️ Nice try. **{out:,} UKPence** of recent outbound transfers says you're hiding wealth, not suffering poverty. Refused.",
    "🕵️ The audit flagged **{out:,} UKPence** flowing out of your wallet. Stashing it with accomplices doesn't fool us. Denied — warning logged.",
    "🕵️ Funny how you're suddenly 'broke' right after shipping **{out:,} UKPence** away. We count it as yours. No payment today.",
    "🕵️ Compliance here. **{out:,} UKPence** left your wallet recently, so on paper and in reality you are ineligible. Refused.",
    "🕵️ We can read a ledger. **{out:,} UKPence** of outbound transfers means you're hiding assets. Denied. Consider yourself warned.",
    "🕵️ Convenient timing: **{out:,} UKPence** shipped out, followed instantly by a hardship claim. We weren't born yesterday. Refused.",
    "🕵️ The means test includes what you gave away. That's **{out:,} UKPence** recently. Claim refused. Do it again and access goes.",
    "🕵️ Spotted: **{out:,} UKPence** quietly moved to other accounts. That counts against you. Denied today, banned if repeated.",
    "🕵️ You can't gift away **{out:,} UKPence** and then cry poverty to the state. Claim blocked. One more stunt and you'll be sanctioned.",
    "🕵️ Our system loves patterns, and yours is **{out:,} UKPence** out, then a claim in. Refused. Push it and you're barred.",
    "🕵️ Means-tested means means-tested. **{out:,} UKPence** of recent transfers disqualifies you. Warning issued.",
]
_BENEFITS_FRAUD_BAN = [
    "🚫 **Benefits fraud detected.** Caught hiding UKPence to cheat the means test — barred from benefits for **{days} days**.",
    "🚫 That's quite enough. The DWP fraud squad has sanctioned you for **{days} days**. Time to experience genuine financial reality.",
    "🚫 Caught red-handed shuffling UKPence to feign poverty. Benefits suspended for **{days} days**.",
    "🚫 **Sanctioned.** Repeated benefits fraud has earned you a **{days}-day** ban. Try earning it honestly.",
    "🚫 The fraud squad has seen enough of your accounting gymnastics. Benefits revoked for **{days} days**.",
    "🚫 Funnelling UKPence to dodge the means test? Banned for **{days} days**. The DWP has a very long memory.",
    "🚫 **Investigation closed, verdict guilty.** No welfare for **{days} days**. Next time the penalty doubles.",
    "🚫 You gamed the system one time too many. **{days}-day** sanction applied. Sit and think about it.",
    "🚫 Benefits access suspended for **{days} days** for persistent fiddling. Don't make us double it.",
    "🚫 Caught laundering your 'poverty' again. **{days} days** in the sin bin.",
    "🚫 **Sanction issued.** Repeat offender, **{days} days** without state support. The honest taxpayers thank you.",
    "🚫 That's a wrap on your claiming career for **{days} days**. The fraud office wishes you a humbling period of reflection.",
    "🚫 Three strikes and a shovel: you kept digging, so it's a **{days}-day** ban.",
    "🚫 **Fraud confirmed.** Benefits frozen for **{days} days**. Keep this up and the freeze becomes permanent.",
]
_BENEFITS_BANNED = [
    "🚫 You're serving a benefits-fraud ban. Spamming the command will not accelerate the calendar. Access returns <t:{ts}:R>.",
    "🚫 No benefits for you — your fraud sanction lifts <t:{ts}:R>.",
    "🚫 The DWP hasn't forgotten your little stunt. Benefits ban ends <t:{ts}:R>.",
    "🚫 Still sanctioned. The system unlocks <t:{ts}:R>.",
    "🚫 Your fraud ban is very much active. Try again <t:{ts}:R>.",
    "🚫 Nope. You remain on the blacklist until <t:{ts}:R>.",
    "🚫 Benefits remain frozen. The thaw comes <t:{ts}:R>.",
    "🚫 Access denied, ban in progress. Lifts <t:{ts}:R>.",
    "🚫 You're still doing your time. Released <t:{ts}:R>.",
    "🚫 The sanction stands. Come back <t:{ts}:R>.",
    "🚫 No dole for the disgraced just yet. Ends <t:{ts}:R>.",
    "🚫 Patience, fraudster. Your ban expires <t:{ts}:R>.",
    "🚫 The fraud office says not yet. Ban lifts <t:{ts}:R>.",
]

_BENEFITS_POOLS = {
    "success": (_BENEFITS_PERSONAL, _BENEFITS_DATA, _BENEFITS_SUCCESS),
    "rich": (_BENEFITS_PERSONAL_RICH, _BENEFITS_DATA_RICH, _BENEFITS_RICH),
    "already": (_BENEFITS_PERSONAL_ALREADY, _BENEFITS_DATA_ALREADY, _BENEFITS_ALREADY),
    "fraud_warn": (_BENEFITS_PERSONAL_FRAUD_WARN, _BENEFITS_DATA_FRAUD_WARN, _BENEFITS_FRAUD_WARN),
    "fraud_ban": (_BENEFITS_PERSONAL_FRAUD_BAN, _BENEFITS_DATA_FRAUD_BAN, _BENEFITS_FRAUD_BAN),
    "banned": (_BENEFITS_PERSONAL_BANNED, _BENEFITS_DATA_BANNED, _BENEFITS_BANNED),
}


def _benefits_stats(uid, **context) -> dict:
    """Live figures for benefits lines, read at claim time.

    A field is None when the joke that needs it has stopped being true - they were up at
    the casino and now aren't, they had never gambled and now have.
    """
    def pos(n):
        return n if n else None

    s = {
        "uid": uid,
        "name": context.get("name", ""),
        "amount": context.get("amount", 0),
        "bal": context.get("bal", 0),
        "threshold": context.get("threshold", 0),
        "ts": context.get("ts", 0),
        "streak": context.get("streak", 0),
        "days": context.get("days", 0),
        "out": context.get("out", 0),
        "fine": context.get("fine", 0),
        "offenses": context.get("offenses", 0),
    }
    s.update(context)
    try:
        rows = DatabaseManager.fetch_all(
            "SELECT game, COUNT(*) n, SUM(net) net, SUM(CASE WHEN net > 0 THEN 1 ELSE 0 END) w "
            "FROM casino_results WHERE user_id = ? GROUP BY game", (str(uid),)) or []
        games = sum(r[1] for r in rows)
        net = sum(r[2] or 0 for r in rows)
        wins = sum(r[3] or 0 for r in rows)
        s["casino_games"] = pos(games)
        s["casino_lost"] = -net if net < 0 else None
        s["casino_up"] = net if net > 0 else None
        s["never_gambled"] = "" if games == 0 else None
        s["no_casino_wins"] = "" if games and not wins else None
        for game, n, gnet, _w in rows:
            s[f"{game}_played"] = pos(n)
            s[f"{game}_lost"] = -(gnet or 0) if (gnet or 0) < 0 else None
        # the payment as a share of what a given table has taken
        amt = s.get("amount") or 0
        for key in ("casino", "roulette", "mines", "blackjack", "slots", "higherlower", "chest"):
            lost = s.get("casino_lost") if key == "casino" else s.get(f"{key}_lost")
            s[f"pct_of_{key}"] = f"{amt / lost * 100:.2f}" if (lost and amt) else None
        row = DatabaseManager.fetch_one(
            "SELECT MIN(net), MAX(net) FROM casino_results WHERE user_id = ?", (str(uid),))
        if row:
            s["worst_loss"] = -row[0] if row[0] and row[0] < 0 else None
            s["best_win"] = row[1] if row[1] and row[1] > 0 else None

        row = DatabaseManager.fetch_one(
            "SELECT COUNT(*), SUM(amount) FROM pay_transfers WHERE payer_id = ?", (str(uid),))
        s["paid_out_n"], s["paid_out"] = (pos(row[0]), pos(row[1])) if row else (None, None)
        row = DatabaseManager.fetch_one(
            "SELECT COUNT(*), SUM(amount) FROM pay_transfers WHERE recipient_id = ?", (str(uid),))
        s["paid_in_n"], s["paid_in"] = (pos(row[0]), pos(row[1])) if row else (None, None)

        row = DatabaseManager.fetch_one(
            "SELECT COUNT(*) FROM user_transactions WHERE user_id = ? AND amount > 0 "
            "AND reason LIKE 'Benefits payment%'", (str(uid),))
        s["claims"] = pos(row[0]) if row else None

        row = DatabaseManager.fetch_one(
            "SELECT COUNT(*), SUM(principal) FROM bonds WHERE user_id = ? AND status = 'active'",
            (str(uid),))
        s["bonds"], s["bonded"] = (pos(row[0]), pos(row[1])) if row else (None, None)

        row = DatabaseManager.fetch_one(
            "SELECT COUNT(*), SUM(price_paid) FROM shop_purchases WHERE user_id = ?", (str(uid),))
        s["shop_items"], s["shop_spent"] = (pos(row[0]), pos(row[1])) if row else (None, None)

        row = DatabaseManager.fetch_one("SELECT count FROM shut_counts WHERE user_id = ?", (str(uid),))
        s["shut"] = pos(row[0]) if row else None

        if "bal" not in context:
            b = get_bb(uid)
            s["bal"] = b
            s["balance"] = b
        elif "balance" not in s:
            s["balance"] = s["bal"]
    except Exception:
        log.debug("benefits stats lookup failed", exc_info=True)
    return s


def _fields(line: str) -> set:
    """Placeholder names a line needs, ignoring the format spec after any colon."""
    return {f.split("!")[0].split("[")[0] for _t, f, _s, _c in Formatter().parse(line) if f}


_BENEFITS_ALWAYS = {"uid", "amount", "name", "bal", "threshold", "ts", "streak", "days", "out", "fine", "offenses", "balance"}


def _benefits_line(category: str, uid: int | str, **context) -> str:
    """Pick and format a line for any benefits category (success, rich, already, fraud_warn,
    fraud_ban, banned) using the 3-tier lookup: Personal -> Data-Driven -> Generic House Pool.
    """
    suid = str(uid)
    stats = _benefits_stats(uid, **context)
    personal_dict, data_pool, house_pool = _BENEFITS_POOLS.get(
        category, (_BENEFITS_PERSONAL, _BENEFITS_DATA, _BENEFITS_SUCCESS)
    )

    def live(pool):
        return [l for l in pool
                if all(stats.get(f) is not None for f in _fields(l) - _BENEFITS_ALWAYS)]

    personal = live(personal_dict.get(suid, ()))
    if personal and random.random() < _BENEFITS_PERSONAL_CHANCE:
        template = random.choice(personal)
    else:
        generic = live(data_pool)
        if generic and random.random() < _BENEFITS_DATA_CHANCE:
            template = random.choice(generic)
        else:
            template = random.choice(house_pool)

    return template.format(**stats)


def _benefits_success_line(uid, amount, stats=None) -> tuple:
    """Pick a success line and stats tuple for backward compatibility."""
    if stats is None:
        stats = _benefits_stats(uid, amount=amount)

    def live(pool):
        return [l for l in pool
                if all(stats.get(f) is not None for f in _fields(l) - _BENEFITS_ALWAYS)]

    personal = live(_BENEFITS_PERSONAL.get(str(uid), ()))
    if personal and random.random() < _BENEFITS_PERSONAL_CHANCE:
        return random.choice(personal), stats
    generic = live(_BENEFITS_DATA)
    if generic and random.random() < _BENEFITS_DATA_CHANCE:
        return random.choice(generic), stats
    return random.choice(_BENEFITS_SUCCESS), stats


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
    name = getattr(interaction.user, "display_name", None) or getattr(interaction.user, "name", "")

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
        line = _benefits_line("banned", uid, ts=rec["banned_until"], fine=fine, offenses=rec.get("offenses", 0), name=name, bal=bal)
        await interaction.response.send_message(
            line + f"\n\n-# You can pay a fine of **{fine:,} UKPence** to lift the ban and reset your offense history.",
            view=view
        )
        return

    # Genuinely well-off (hid nothing) - plain denial, no penalty.
    if bal >= threshold:
        msg = _benefits_line("rich", uid, bal=bal, threshold=threshold, name=name)
        await _reply(msg)
        return

    # Effective wealth = balance + recent /pay outflows. Parking UKP on an alt to drop
    # under the threshold doesn't make you poor.
    recent_out = _recent_pay_out(suid, getattr(config, "BENEFITS_LOOKBACK_DAYS", 3), rec.get("fine_paid_at", 0))
    if bal + recent_out >= threshold:
        ramp = getattr(config, "BENEFITS_BAN_RAMP", [3, 7, 14, 30])
        if rec["offenses"] == 0 and not rec["warned"]:
            rec["warned"] = True  # one warning before any ban (protects honest givers)
            _save()
            msg = _benefits_line("fraud_warn", uid, out=recent_out, bal=bal, threshold=threshold, name=name)
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
        line = _benefits_line("fraud_ban", uid, days=days, out=recent_out, offenses=rec["offenses"], fine=fine, name=name, bal=bal)
        await _reply(
            line + f"\n\n-# You can pay a fine of **{fine:,} UKPence** to lift the ban and reset your offense history.",
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
        ts = _next_uk_midnight_ts()
        msg = _benefits_line("already", uid, ts=ts, streak=rec.get("streak", 0), name=name, bal=bal)
        await _reply(msg)
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
    msg = _benefits_line("success", uid, amount=amount, name=name, streak=rec["streak"], bal=bal)
    await _reply(msg)

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
