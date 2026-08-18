"""Join-watch: staff-toggled AI screening of new joiners' first messages.

An active listener: arming it does not backtrack. Members who join while it is
armed are registered on join, and their first few messages are sent to an AI
model (OpenAI primary, Gemini fallback) together with profile context (names,
account age, avatar and banner images). Members the model confidently judges
to be hostile raid trolls are timed out and reported to the police station.
The armed/disarmed toggle and incident context survive restarts; the watch
list is in-memory only, so a restart stops watching members who joined before
it.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from datetime import timedelta
from typing import Any

import aiohttp
import discord

import config
from config import (CHANNELS, JOIN_WATCH_BUFFERS_FILE, JOIN_WATCH_FILE,
                    JOIN_WATCH_MAX_WATCH_HOURS, ROLES, USERS)
from lib.core.file_operations import load_json_file, save_json_file

logger = logging.getLogger(__name__)

MAX_SCANNED_MESSAGES = 10
# The brief carries the whole screen now, so it needs room for a list of behaviours.
# Discord modal text inputs allow up to 4000, so this is free headroom.
MAX_CONTEXT_CHARS = 2000
MAX_WATCHED_MEMBERS = 1000  # raid-scale bound on the in-memory watch list
TIMEOUT_HOURS = 24
# A timeout is only applied on a confident "troll" verdict. Every watched member
# is scanned for all MAX_SCANNED_MESSAGES messages unless actioned first.
ACT_CONFIDENCE = 0.85
STAFF_ROLE_IDS = {ROLES.MINISTER, ROLES.CABINET, ROLES.BORDER_FORCE}

# The brief is the whole screen: the rubric in _build_static_prompt carries only the
# permanent floor, so anything else you want caught has to be described here.
DEFAULT_CONTEXT = (
    "Two UK-only game freebies are running at once, so this British server is an obvious "
    "place for people outside the UK to come looking for codes.\n"
    "- O2 x Fortnite, from 14 August 2026: O2 and Virgin Media customers claim a free "
    "Fortnite skin (Pond Guardian Froggory) through O2 Priority.\n"
    "- McDonald's x Overwatch, from 18 August 2026, UK and Ireland only: McDonald's Side "
    "Missions give Overwatch rewards, including Kiriko's 'Donut Break' emote. Codes come "
    "off a real order, so expect asks for receipts and order codes as well as spare codes.\n"
    "This server is not a code exchange: anyone who joined to obtain a code, or to get "
    "help claiming one, did not join to be a member.\n"
    "FLAG:\n"
    "- Asking for a code, spare code, unused code or receipt, however politely - "
    "'anyone got one they aren't using', 'pls code', 'need a code' all count.\n"
    "- Asking for help claiming, redeeming or qualifying for either promotion, or asking "
    "someone to claim it, order for them, or lend them a UK number, address or account.\n"
    "- Offering money, gift cards, in-game items or trades for a code or a receipt.\n"
    "- Asking members for logins, email addresses, phone numbers or one-time passcodes, "
    "including offers to claim the code for them.\n"
    "- Posting claim links, 'free code generator' sites, QR codes or shortened URLs.\n"
    "- Telling members to DM them, or DMing members, to collect codes.\n"
    "- Impersonating staff, O2, Virgin Media, McDonald's, Epic Games or Blizzard.\n"
    "- Several accounts pushing an identical link or identical wording.\n"
    "One clear ask is enough. Do not wait for a second message, do not soften the verdict "
    "because they were polite, and do not treat a first-time offence as lighter.\n"
    "DO NOT flag members who merely talk about either promotion, the skin, the emote, "
    "Fortnite, Overwatch or McDonald's without asking anyone for a code: saying they "
    "claimed it, asking whether it has gone live, or complaining that the code did not "
    "work are all fine. Being foreign, new, or low-effort is still never a reason on its "
    "own."
)

# Old defaults are migrated to the current one on load, so a stale stored
# incident (e.g. a finished World Cup tie) does not keep biasing verdicts.
_LEGACY_DEFAULT_CONTEXTS = {

    "O2 x Fortnite promotion, from 14 August 2026. UK O2 and Virgin Media customers can "
    "claim a code through O2 Priority for a free Fortnite skin (Pond Guardian Froggory). "
    "This is a British server, so people outside the UK are joining purely to get a code "
    "out of the members here. This server is not a code exchange: anyone who joined to "
    "obtain a code, or to get help claiming one, did not join to be a member.\n"
    "FLAG:\n"
    "- Asking for a code, a spare code or an unused code, in any wording and however "
    "politely - 'anyone got one they aren't using', 'pls code', 'need a code' all count.\n"
    "- Asking for help claiming, redeeming or qualifying for the promotion, or asking "
    "someone to claim it on their behalf or lend them a UK number.\n"
    "- Offering money, gift cards, in-game items or trades for a code.\n"
    "- Asking members for logins, email addresses, phone numbers or one-time passcodes, "
    "including offers to claim the code for them.\n"
    "- Posting claim links, 'free code generator' sites, QR codes or shortened URLs.\n"
    "- Telling members to DM them, or DMing members, to collect codes.\n"
    "- Impersonating staff, O2, Virgin Media or Epic Games.\n"
    "- Several accounts pushing an identical link or identical wording.\n"
    "One clear ask is enough. Do not wait for a second message, do not soften the verdict "
    "because they were polite, and do not treat a first-time offence as lighter.\n"
    "DO NOT flag members who merely talk about the promotion, the skin or Fortnite "
    "without asking anyone for a code: saying they claimed it, asking whether it has "
    "gone live, or complaining that the code did not work are all fine. Being foreign, "
    "new, or low-effort is still never a reason on its own.",
    "England play Argentina in the World Cup semi-final tonight. Waves of newly "
    "joined accounts (many Argentinian) are trolling, spreading anti-England "
    "hate and trying to bait members.",
    "General screening - no specific incident right now. Watch for the usual raid "
    "patterns from newly joined accounts: coordinated trolling, hate, bait, spam, "
    "targeting of members, and sustained anti-British sentiment (genuine hostility "
    "toward Britain or British members, beyond football banter and friendly "
    "teasing). Judge each member on their own messages and profile.",
    "General screening - no specific incident right now. Watch for the usual raid "
    "patterns from newly joined accounts: coordinated trolling, hate, bait, spam "
    "and targeting of members. Also watch for organised farming of UK-only "
    "giveaway codes (free game skins, network promos and the like), where "
    "outsiders join purely to harvest codes from British members, and for the "
    "scams that follow one - code selling, fake claim links and DM bait. Judge "
    "each member on their own messages and profile.",
    "O2 x Fortnite promotion, from 14 August 2026. UK O2 and Virgin Media customers "
    "can claim a code through O2 Priority for a free Fortnite skin (Pond Guardian "
    "Froggory). This is a British server, so it is an obvious place for people "
    "outside the UK to come looking for a code. Expect a wave of new joiners asking "
    "for spare codes: most are ordinary players, and asking is not an offence. The "
    "problem is the organised layer around it - accounts harvesting codes at scale "
    "by DMing members, buying and selling codes, fake claim links and 'free code "
    "generator' sites, and phishing that asks members for their O2 login, phone "
    "number or one-time passcode. Judge each member on their own messages and "
    "profile.",
    "O2 x Fortnite promotion, from 14 August 2026. UK O2 and Virgin Media customers can "
    "claim a code through O2 Priority for a free Fortnite skin (Pond Guardian Froggory). "
    "This is a British server, so it is an obvious place for people outside the UK to "
    "come looking for a code.\n"
    "FLAG these, and only these:\n"
    "- Asking members for account credentials, logins, email addresses, phone numbers or "
    "one-time passcodes, however politely, including offers to claim the code for them.\n"
    "- Posting claim links, 'free code generator' sites, QR codes or shortened URLs.\n"
    "- Buying, selling or trading codes, or offering money, gift cards or in-game items.\n"
    "- Telling members to DM them, or working through the member list privately, to "
    "harvest codes at scale.\n"
    "- Pushing the same request again after being turned down, or across many channels.\n"
    "- Impersonating staff, O2, Epic Games or a support team to make the ask look "
    "official.\n"
    "- Several accounts pushing an identical link or identical wording.\n"
    "DO NOT flag simply asking for a spare code, being disappointed at not getting one, "
    "or being obviously foreign. Most joiners this week are ordinary players who turned "
    "up for a freebie, and asking once is not an offence. The line is deception and "
    "pressure, not wanting the skin.",
}

_state_cache: dict[str, Any] | None = None
_buffers: dict[int, dict[str, Any]] = {}
_locks: dict[int, asyncio.Lock] = {}
_buffers_loaded = False


# --- watch list persistence -------------------------------------------------------
# The watch list used to live only in memory, so any restart silently stopped screening
# everyone who had joined but not yet finished their scan - exactly the members a deploy
# during an incident most needs to keep watching. It is now mirrored to disk.
#
# Only unfinished entries are kept: a member who has been actioned or has run out the
# scan is done forever, and writing them back would just grow the file. Entries also
# expire after JOIN_WATCH_MAX_WATCH_HOURS so a joiner who never posts is not watched
# indefinitely.
def _watch_expired(entry: dict[str, Any], now: int | None = None) -> bool:
    now = int(time.time()) if now is None else now
    started = int(entry.get("registered_at", 0) or 0)
    if not started:
        return False
    return (now - started) > int(JOIN_WATCH_MAX_WATCH_HOURS) * 3600


def _load_buffers() -> None:
    """Restore the watch list once, on first use after start-up."""
    global _buffers, _buffers_loaded
    if _buffers_loaded:
        return
    _buffers_loaded = True
    try:
        stored = load_json_file(JOIN_WATCH_BUFFERS_FILE) or {}
        restored = 0
        for uid, entry in stored.items():
            if not isinstance(entry, dict) or entry.get("done"):
                continue
            if _watch_expired(entry):
                continue
            entry.setdefault("messages", [])
            entry.setdefault("done", False)
            entry.setdefault("registered_at", int(time.time()))
            _buffers[int(uid)] = entry
            restored += 1
        if restored:
            logger.info("join-watch restored %s member(s) still mid-scan", restored)
    except Exception:
        logger.exception("join-watch could not restore its watch list")


def _save_buffers() -> None:
    """Persist the unfinished watch list. Never raises: losing the mirror is survivable,
    taking the screening path down with it is not."""
    try:
        now = int(time.time())
        keep = {
            str(uid): entry
            for uid, entry in _buffers.items()
            if not entry.get("done") and not _watch_expired(entry, now)
        }
        save_json_file(JOIN_WATCH_BUFFERS_FILE, keep)
    except Exception:
        logger.exception("join-watch could not save its watch list")


# --- toggle state ---------------------------------------------------------------
def get_join_watch_state() -> dict[str, Any]:
    global _state_cache
    if _state_cache is None:
        data = load_json_file(JOIN_WATCH_FILE) or {}
        context = str(data.get("context") or DEFAULT_CONTEXT)[:MAX_CONTEXT_CHARS]
        if context in _LEGACY_DEFAULT_CONTEXTS:
            context = DEFAULT_CONTEXT
        _state_cache = {
            "enabled": bool(data.get("enabled", False)),
            "context": context,
        }
    return dict(_state_cache)


def set_join_watch_state(enabled: bool, context: str | None = None) -> dict[str, Any]:
    global _state_cache
    current = get_join_watch_state()
    new_state = {
        "enabled": bool(enabled),
        "context": (
            context.strip()[:MAX_CONTEXT_CHARS] if context and context.strip() else current["context"]
        ),
    }
    save_json_file(JOIN_WATCH_FILE, {**new_state, "updated_at": int(time.time())})
    _state_cache = new_state
    _buffers.clear()
    _locks.clear()
    _save_buffers()   # arming never backtracks, so the stored list resets with it
    return dict(new_state)


def join_watch_enabled() -> bool:
    return get_join_watch_state()["enabled"]


# --- screening ------------------------------------------------------------------
def _eligible(member: Any) -> bool:
    if getattr(member, "bot", False):
        return False
    if getattr(member, "id", None) == USERS.OGGERS:
        return False
    if any(getattr(role, "id", None) in STAFF_ROLE_IDS for role in getattr(member, "roles", [])):
        return False
    return True


def register_join(member: Any) -> None:
    """on_member_join hook: start listening to a member who joined while armed.

    Arming never backtracks; only joins that happen while the watch is armed
    are screened.
    """
    if not join_watch_enabled() or not _eligible(member):
        return
    _load_buffers()
    while len(_buffers) >= MAX_WATCHED_MEMBERS:
        evicted = next(iter(_buffers))
        _buffers.pop(evicted, None)
        _locks.pop(evicted, None)
    _buffers.setdefault(
        member.id,
        {"messages": [], "done": False, "registered_at": int(time.time())},
    )
    _save_buffers()
    logger.info("join-watch is now listening to new joiner %s", member.id)


def watch_member(member: Any) -> bool:
    """Start screening a member who joined before the watch was armed.

    register_join deliberately never backtracks, so a member who arrived while disarmed is
    invisible to it forever. Staff looking at a suspected batch need the opposite: screen
    exactly these accounts if they ever speak, without arming the watch for the whole
    server. Returns False for anyone exempt (bots, staff).
    """
    if not _eligible(member):
        return False
    _load_buffers()
    while len(_buffers) >= MAX_WATCHED_MEMBERS:
        evicted = next(iter(_buffers))
        _buffers.pop(evicted, None)
        _locks.pop(evicted, None)
    _buffers.setdefault(
        member.id,
        {"messages": [], "done": False, "registered_at": int(time.time())},
    )
    _save_buffers()
    return True


def _snapshot(message: Any) -> dict[str, Any]:
    content = " ".join((message.content or "").split())[:400]
    if not content:
        content = f"[no text; {len(getattr(message, 'attachments', None) or [])} attachment(s)]"
    return {
        "channel": f"#{getattr(message.channel, 'name', message.channel)}",
        "content": content,
        "jump_url": getattr(message, "jump_url", ""),
        "ts": int(message.created_at.timestamp()),
    }


# Only actual member messages count; system messages (join notices, boosts, pins)
# are authored by the member but are not something they wrote.
_SCREENED_MESSAGE_TYPES = (discord.MessageType.default, discord.MessageType.reply)


async def maybe_watch_message(client: Any, message: Any) -> None:
    """on_message hook: screen a registered new joiner's message, acting at most once."""
    try:
        if not join_watch_enabled():
            return
        if message.guild is None:
            return
        if getattr(message, "type", discord.MessageType.default) not in _SCREENED_MESSAGE_TYPES:
            return
        member = message.author
        # Restore the watch list on the first message after a restart, so members who
        # joined before a deploy keep being screened instead of silently falling off.
        _load_buffers()
        # Only members registered by register_join are watched; staff exemption is
        # re-checked in case they were given a role after joining.
        if getattr(member, "id", None) not in _buffers or not _eligible(member):
            return
        lock = _locks.setdefault(member.id, asyncio.Lock())
        async with lock:
            entry = _buffers.get(member.id)
            if entry is None or entry["done"]:
                return
            entry["messages"].append(_snapshot(message))
            verdict, usage = await _evaluate(client, member, entry["messages"])
            await _apply_verdict(client, member, entry, verdict, usage, message)
            # After the verdict, so a member marked done drops out of the saved list.
            _save_buffers()
    except Exception:
        logger.exception("join-watch screening failed for message %s", getattr(message, "id", "?"))


async def _apply_verdict(
    client: Any,
    member: Any,
    entry: dict[str, Any],
    verdict: dict[str, Any] | None,
    usage: dict[str, int] | None,
    message: Any = None,
) -> None:
    call = str(verdict.get("verdict", "unsure")).lower() if verdict else "no verdict (model error)"
    try:
        confidence = max(0.0, min(1.0, float((verdict or {}).get("confidence", 0))))
    except (TypeError, ValueError):
        confidence = 0.0
    if call == "troll" and confidence >= ACT_CONFIDENCE:
        entry["done"] = True
        outcome = f"timed out for {TIMEOUT_HOURS}h and reported"
        await _action_troll(client, member, entry, verdict or {}, confidence, message)
    elif len(entry["messages"]) >= MAX_SCANNED_MESSAGES:
        entry["done"] = True
        outcome = "scan complete - no action"
        logger.info("join-watch finished watching member %s without action", member.id)
    else:
        outcome = "still watching"
    await _log_scan(client, member, entry, verdict, call, confidence, outcome, usage)


# --- Gemini ---------------------------------------------------------------------
async def _fetch_image(session: aiohttp.ClientSession, url: str) -> tuple[str, bytes] | None:
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return None
            blob = await resp.read()
            if not blob or len(blob) > 4_000_000:
                return None
            mime = (resp.headers.get("Content-Type") or "image/png").split(";")[0].strip()
            return (mime if mime.startswith("image/") else "image/png", blob)
    except Exception:
        return None


async def _profile_images(client: Any, member: Any) -> list[tuple[str, str, bytes]]:
    """Avatar (and banner when set) as inline images; failures degrade to text-only."""
    images: list[tuple[str, str, bytes]] = []
    try:
        async with aiohttp.ClientSession() as session:
            avatar = getattr(member, "display_avatar", None)
            if avatar is not None:
                fetched = await _fetch_image(session, avatar.replace(size=256, format="png").url)
                if fetched:
                    images.append(("avatar", *fetched))
            # The banner is only present on a full user fetch, never on the cached member.
            try:
                user = await client.fetch_user(member.id)
                banner = getattr(user, "banner", None)
            except Exception:
                banner = None
            if banner is not None:
                fetched = await _fetch_image(session, banner.replace(size=512, format="png").url)
                if fetched:
                    images.append(("profile banner", *fetched))
    except Exception:
        logger.debug("join-watch profile image fetch failed", exc_info=True)
    return images


def _build_static_prompt(member: Any, context: str, image_labels: list[str]) -> str:
    """The cache-stable prompt prefix: instructions, incident context, profile.

    Everything here must be identical from one scan of a member to the next -
    provider prompt caches do exact prefix matching, so any wording that drifts
    between scans (relative times, message counters) belongs in the dynamic
    tail instead. The rubric is deliberately detailed: it improves judgment
    and keeps the identical prefix above OpenAI's 1,024-token caching floor.
    """
    created = getattr(member, "created_at", None)
    joined = getattr(member, "joined_at", None)
    profile_lines = []
    for label, value in (
        ("username", getattr(member, "name", "")),
        ("display name", getattr(member, "display_name", "")),
        ("global name", getattr(member, "global_name", "") or ""),
    ):
        if value:
            profile_lines.append(f"- {label}: {value}")
    if created:
        profile_lines.append(f"- account created: {created:%Y-%m-%d} (UTC)")
    if joined:
        profile_lines.append(f"- joined the server: {joined:%Y-%m-%d %H:%M} UTC")
    if created and joined:
        age_days = max(0, int((joined - created).total_seconds() // 86400))
        profile_lines.append(f"- the account was {age_days} day(s) old when it joined")
    profile_lines.append(
        "Attached image(s): " + ", ".join(image_labels) + "."
        if image_labels
        else "No profile images could be fetched."
    )
    profile = "\n".join(profile_lines)
    return f"""You are the moderation screener for a casual, British, banter-heavy Discord server.
A recently joined member is being screened. You are NOT a general-purpose troll detector:
you screen against the SCREENING BRIEF below, plus a short permanent floor, and nothing
else.

WHAT YOU ARE SCREENING FOR - exactly two things:
1. The behaviour described in the SCREENING BRIEF below. Staff write the brief for the
   situation the server is in right now, and it is the whole reason screening is on.
2. The PERMANENT FLOOR below, which applies whatever the brief says.
If what a member has done is in neither, the verdict is "fine" - however unpleasant,
attention-seeking, tasteless or annoying you personally find them. Being a bit of a prat
is not a screening matter here; staff handle that themselves. Do not invent a category
of your own, and do not stretch the brief to cover something that merely feels similar.

PERMANENT FLOOR (always "troll", whatever the brief says):
- Hate speech or slurs of any kind, including racial, ethnic, national and sexual slurs.
- Threats, or wishing death or serious harm on a real person; celebrating a tragedy.
- Sexual content aimed at a member, and any sexual content involving minors.
- Doxxing, or posting someone's personal information.
- Mass spam or flooding: the same message across several channels, copypasta walls,
  or repeated advertising after being told to stop.
- Openly recruiting or coordinating a raid ("everyone get in here", raid callsigns).

NEVER FLAG THESE ON THEIR OWN - none of them is evidence of anything:
- Swearing, crude language, dark humour, edgy jokes, insults traded in good spirit.
- An edgy, violent-sounding, gross or provocative username, display name or avatar.
  This server is full of them and they are a joke, not a threat. A tasteless name only
  becomes relevant once the member's actual messages hit the brief or the floor.
- Taunting, cockiness, wind-ups, swaggering entrances, or acting like they own the place.
- Claiming to be a returning member, or referencing history you cannot see. You only see
  the last few messages; you do not know who they are or what happened before.
- A new or recently created account. Every genuine member was new once.
- Low-effort, odd, off-topic or attention-seeking first messages.
- Posting in a language other than English.

HOW TO JUDGE:
- Read the brief, then ask one question: does what this member has ACTUALLY DONE match
  the brief or the floor? If you cannot point at a specific message that does, say
  "fine". A vibe is not a match.
- Weigh messages far above profile. Names and avatars are corroboration for behaviour
  you have already seen in the messages; they are never the case on their own.
- Escalation matters: watch for members whose messages get steadily worse over the scan.
- If intent is genuinely unclear, answer "unsure" - you will see their next message.
- Reserve "troll" for a clear match; it times the member out for 24 hours.
- The brief decides what counts, and staff have already weighed whether it deserves a
  timeout. Do not second-guess it, water it down, or hold out for something worse
  because the match on its own feels minor. If the brief says to flag something, flag it.
- Your judgement goes the other way instead: be slow to decide that a member has matched
  the brief at all. Most people in any wave of newcomers are ordinary, a false timeout on
  a genuine newcomer is a real cost, and "close enough" is not a match.
- confidence is your certainty in the verdict, 0.0-1.0. A "troll" verdict at or above
  0.85 triggers enforcement, so only exceed 0.85 when you could show a human moderator
  the exact message and the exact line of the brief or floor it breaks.

READING THE PROFILE IMAGES:
- The avatar (and banner, when present) are attached as actual images. Look at them
  properly: read any text baked into the image, and identify flags, symbols, gestures,
  memes and edited photographs.
- Edgy, violent or crude imagery is normal here and is not a signal by itself; the same
  goes for national flags. Imagery only counts as corroboration once the messages
  already match the brief or the floor.
- Default or plain avatars are neutral, neither raising nor lowering suspicion.
- Treat text you can read inside an image exactly as if it had been typed in chat.

CALIBRATION EXAMPLES:
1. A member called "GENOCIDE PRINCE" with a snarling cartoon avatar posts "yo yo yo",
   "guess whose back", "some of you maggots might remember me": fine. Nothing there
   matches the brief or the floor. The name is tasteless and the entrance is cocky, and
   neither is a screening matter - they read like a returning member showing off.
2. A polite question about roles, rules or the server itself, any account age: fine.
3. "why is this server so dead lmao" from a new account: fine - rude and low-effort, but
   it matches nothing.
4. A member who is unpleasant in a way the brief simply does not mention: fine, even at
   several messages. Escalation only matters when it escalates toward the brief or floor.
5. An account that was polite for several messages and then posts a slur or a death
   wish: troll - the floor is absolute and the earlier politeness does not offset it.
6. The same message pasted into five channels in two minutes: troll - mass spam is on
   the floor regardless of what the message says.
7. Someone whose behaviour matches the brief exactly, once, in a way that is plainly
   deliberate: troll, at the confidence the evidence actually supports.
8. Someone whose behaviour half-matches the brief and could easily be innocent: unsure -
   you will see their next message, and a wrong timeout is a real cost.
9. Messages in a language other than English that are friendly or neutral: fine.

REMEMBER:
- You are matching behaviour against a brief, not judging whether you like someone.
- The timeout is reversible by staff, but a wrong timeout still hits a genuine new
  member on their first day - hold the 0.85 bar honestly.
- Judge only from what you are shown; do not invent context that is not there.

SCREENING BRIEF (written by staff; this is what you are looking for):
{context}

MEMBER PROFILE
{profile}"""


def _messages_block(messages: list[dict[str, Any]]) -> str:
    """The dynamic prompt tail; grows with each scan and is never cache-stable."""
    message_lines = "\n".join(
        f'{i}. [{m["channel"]}] "{m["content"]}"' for i, m in enumerate(messages, 1)
    )
    return f"""FIRST MESSAGES SINCE JOINING ({len(messages)} of {MAX_SCANNED_MESSAGES} scanned)
{message_lines}

If the evidence is thin, answer "unsure"; you will be shown their next message.
A "troll" verdict times the member out, so only give one when you can point at a
message that matches the screening brief or the permanent floor.

Respond with raw JSON only:
{{"verdict": "troll" | "unsure" | "fine", "confidence": <0.0-1.0>, "reason": "<one or two short sentences>"}}"""


def _openai_model() -> str:
    return getattr(config, "JOIN_WATCH_OPENAI_MODEL", None) or "gpt-5.4-mini"


def _gemini_model() -> str:
    return getattr(config, "JOIN_WATCH_GEMINI_MODEL", None) or getattr(
        config, "GEMINI_MODEL", "gemini-2.5-flash"
    )


# USD per 1M input/output tokens (output includes reasoning/thinking), for the
# cost footer. Verified against the official OpenAI and Gemini rate cards.
_PRICES_PER_MTOK = {
    "gpt-5.4-mini": (0.75, 4.50),
    "gpt-5.4-nano": (0.20, 1.25),
    "gemini-3.5-flash": (1.50, 9.00),
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "gemini-3.1-pro-preview": (2.00, 12.00),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.5-pro": (1.25, 10.00),
}


def _cost_footer(usage: dict[str, Any] | None) -> str | None:
    if not isinstance(usage, dict):
        return None
    input_tokens = int(usage.get("input", 0) or 0)
    output_tokens = int(usage.get("output", 0) or 0)
    if not (input_tokens or output_tokens):
        return None
    model = str(usage.get("model") or "")
    line = f"{model} · " if model else ""
    line += f"{input_tokens:,} in / {output_tokens:,} out tokens"
    prices = _PRICES_PER_MTOK.get(model)
    if prices:
        cost = input_tokens / 1e6 * prices[0] + output_tokens / 1e6 * prices[1]
        line = f"est. cost ${cost:.4f} · {line}"
    return line


async def _call_openai_json(
    static_text: str,
    images: list[tuple[str, bytes]],
    dynamic_text: str,
    cache_key: str | None = None,
) -> tuple[str | None, str | None, dict[str, Any] | None]:
    key = os.getenv("OPENAI_TOKEN") or os.getenv("OPENAI_API_KEY")
    if not key:
        return None, "no OpenAI key in the environment", None
    try:
        from openai import AsyncOpenAI
    except ImportError:
        return None, "openai package not installed", None
    model = _openai_model()
    # Cache-stable content first (static text, then images with a fixed detail
    # level), the growing dynamic text last, so the prefix stays identical
    # across scans of the same member.
    content: list[dict[str, Any]] = [{"type": "text", "text": static_text}]
    for mime, blob in images:
        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{mime};base64,{base64.b64encode(blob).decode('ascii')}",
                    "detail": "low",
                },
            }
        )
    content.append({"type": "text", "text": dynamic_text})
    request = dict(
        model=model,
        messages=[{"role": "user", "content": content}],
        response_format={"type": "json_object"},
        # GPT-5 family: max_tokens is rejected (use max_completion_tokens) and
        # only the default temperature is supported. Reasoning is billed as
        # output, so keep the effort down for this simple judgment.
        max_completion_tokens=2048,
    )
    extras: dict[str, Any] = {"reasoning_effort": "low"}
    if cache_key:
        # Routes repeat scans of the same member to the same cache shard.
        extras["prompt_cache_key"] = cache_key
    client = AsyncOpenAI(api_key=key, max_retries=2, timeout=60.0)
    response, last_error = None, None
    # If the API rejects one of the newer optional params, retry bare.
    for attempt in (dict(request, **extras), request):
        try:
            response = await client.chat.completions.create(**attempt)
            break
        except Exception as exc:
            last_error = exc
            if not any(param in str(exc) for param in extras):
                break
    if response is None:
        return None, f"request failed: {last_error}", None
    usage_meta = getattr(response, "usage", None)
    usage = None
    if usage_meta is not None:
        usage = {
            "input": int(getattr(usage_meta, "prompt_tokens", 0) or 0),
            # completion_tokens includes reasoning tokens, which are billed.
            "output": int(getattr(usage_meta, "completion_tokens", 0) or 0),
            "model": model,
        }
    choices = getattr(response, "choices", None) or []
    text = ((choices[0].message.content or "") if choices else "").strip()
    if not text:
        return None, "no text in response", usage
    return text, None, usage


async def _call_gemini_json(
    static_text: str, images: list[tuple[str, bytes]], dynamic_text: str
) -> tuple[str | None, str | None, dict[str, int] | None]:
    key = os.getenv("GEMINI_TOKEN") or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not key:
        return None, "no Gemini key in the environment", None
    model = _gemini_model()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    # Same cache-friendly ordering as the OpenAI path; Gemini 2.5+ has implicit
    # prefix caching with the same static-first requirement.
    parts: list[dict[str, Any]] = [{"text": static_text}]
    for mime, blob in images:
        parts.append(
            {"inline_data": {"mime_type": mime, "data": base64.b64encode(blob).decode("ascii")}}
        )
    parts.append({"text": dynamic_text})
    generation = {
        "temperature": 0.2,
        "maxOutputTokens": 8192,
        "responseMimeType": "application/json",
    }
    # Thinking tokens are billed as output ($9/M on 3.5-flash) and dominate the
    # per-scan cost, so cap them; the verdict is a simple judgment. Models that
    # reject thinkingConfig (400) are retried without it.
    attempts = [
        dict(generation, thinkingConfig={"thinkingBudget": 256}),
        generation,
    ]
    status, data = None, None
    for generation_config in attempts:
        body = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": generation_config,
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, json=body, timeout=aiohttp.ClientTimeout(total=60)
                ) as resp:
                    status, data = resp.status, await resp.json()
        except Exception as exc:
            return None, f"request failed: {exc}", None
        if status != 400:
            break
    if status != 200:
        return None, f"HTTP {status}: {str(data)[:300]}", None
    meta = data.get("usageMetadata") or {}
    usage = {
        "input": int(meta.get("promptTokenCount", 0) or 0),
        # Thinking tokens are billed as output on 2.5+ models.
        "output": int(meta.get("candidatesTokenCount", 0) or 0)
        + int(meta.get("thoughtsTokenCount", 0) or 0),
        "model": model,
    }
    candidate = (data.get("candidates") or [{}])[0]
    answer_parts = (candidate.get("content") or {}).get("parts") or []
    text = "".join(
        p.get("text", "") for p in answer_parts if isinstance(p, dict) and not p.get("thought")
    )
    if not text:
        return None, f"no text (finishReason={candidate.get('finishReason')})", usage
    return text, None, usage


def _parse_verdict(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


async def _evaluate(
    client: Any, member: Any, messages: list[dict[str, Any]]
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    context = get_join_watch_state()["context"]
    images = await _profile_images(client, member)
    # Static prefix (instructions/profile/images) first, growing message list
    # last: provider prompt caches match exact prefixes, so scans 2..N of the
    # same member get the repeated bulk at the cached-input rate.
    static_prompt = _build_static_prompt(member, context, [label for label, _, _ in images])
    dynamic_prompt = _messages_block(messages)
    image_blobs = [(mime, blob) for _, mime, blob in images]
    raw, err, usage = await _call_openai_json(
        static_prompt, image_blobs, dynamic_prompt, cache_key=f"join-watch-{member.id}"
    )
    if err:
        logger.warning(
            "join-watch openai error for member %s: %s; falling back to gemini", member.id, err
        )
        raw, err, usage = await _call_gemini_json(static_prompt, image_blobs, dynamic_prompt)
    if err:
        logger.warning("join-watch gemini error for member %s: %s", member.id, err)
        return None, usage
    return _parse_verdict(raw), usage


# --- enforcement + reporting ------------------------------------------------------
async def _action_troll(
    client: Any,
    member: Any,
    entry: dict[str, Any],
    verdict: dict[str, Any] | None,
    confidence: float,
    message: Any = None,
) -> None:
    """Timeout the member and remove the message that triggered it.

    Only the triggering message is deleted; everything they posted before it is left
    in place so staff can judge the flag in context. The deleted text is snapshotted
    into the police-station report before removal, so the audit trail survives and an
    undo is still a one-click job on the panel.
    """
    verdict = verdict or {}
    reason = " ".join(str(verdict.get("reason", "")).split())[:500]
    until = discord.utils.utcnow() + timedelta(hours=TIMEOUT_HOURS)
    try:
        await member.timeout(
            until, reason=f"Join-watch AI raid screening ({confidence:.0%}): {reason[:300]}"
        )
        action = f"timed out for {TIMEOUT_HOURS}h"
    except discord.Forbidden:
        action = "timeout FAILED: missing permission or role hierarchy"
    except discord.HTTPException as exc:
        action = f"timeout FAILED: {exc.__class__.__name__}"

    action += " · " + await _delete_trigger(entry, message)
    logger.info(
        "join-watch flagged member %s at %.0f%% confidence: %s", member.id, confidence * 100, action
    )
    await _send_report(client, member, entry, reason, confidence, action)


async def _delete_trigger(entry: dict[str, Any], message: Any) -> str:
    """Delete the message that tripped the verdict, flagging it on the snapshot.

    Returns a short phrase for the report footer. Failures are reported rather than
    raised: a message that cannot be removed must not cost us the timeout or the card.
    """
    snapshot = entry["messages"][-1] if entry["messages"] else None
    if message is None:
        if snapshot is not None:
            snapshot["delete_failed"] = "no message handle"
        return "message NOT deleted: no handle"
    try:
        await message.delete()
    except discord.NotFound:
        if snapshot is not None:
            snapshot["delete_failed"] = "already gone"
        return "message already gone"
    except discord.Forbidden:
        if snapshot is not None:
            snapshot["delete_failed"] = "missing Manage Messages"
        return "message delete FAILED: missing Manage Messages"
    except discord.HTTPException as exc:
        if snapshot is not None:
            snapshot["delete_failed"] = exc.__class__.__name__
        return f"message delete FAILED: {exc.__class__.__name__}"
    if snapshot is not None:
        snapshot["deleted"] = True
    return "triggering message deleted"


class UntimeoutButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r"joinwatch:untimeout:(?P<uid>\d+)"
):
    """Persistent 'Remove timeout' button on police station reports (staff only)."""

    def __init__(self, user_id: int):
        self.user_id = int(user_id)
        super().__init__(
            discord.ui.Button(
                label="Remove timeout",
                emoji="🔓",
                style=discord.ButtonStyle.secondary,
                custom_id=f"joinwatch:untimeout:{self.user_id}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["uid"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        clicker_roles = getattr(interaction.user, "roles", [])
        if not any(getattr(role, "id", None) in STAFF_ROLE_IDS for role in clicker_roles):
            await interaction.response.send_message(
                "Only staff can remove join-watch timeouts.", ephemeral=True
            )
            return
        guild = interaction.guild
        member = guild.get_member(self.user_id) if guild else None
        if member is None and guild is not None:
            try:
                member = await guild.fetch_member(self.user_id)
            except discord.HTTPException:
                member = None
        if member is None:
            await interaction.response.send_message(
                "That member is no longer in the server.", ephemeral=True
            )
            return
        try:
            await member.timeout(
                None, reason=f"Join-watch timeout removed by {interaction.user}"
            )
        except (discord.Forbidden, discord.HTTPException) as exc:
            await interaction.response.send_message(
                f"Could not remove the timeout: {exc}", ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"🔓 {interaction.user.mention} removed the join-watch timeout for {member.mention}.",
            allowed_mentions=discord.AllowedMentions.none(),
        )


def _report_view(
    member: Any, entry: dict[str, Any], reason: str, confidence: float, action: str
) -> discord.ui.LayoutView:
    created = getattr(member, "created_at", None)
    joined = getattr(member, "joined_at", None)
    view = discord.ui.LayoutView(timeout=None)
    card = discord.ui.Container(accent_colour=0xE74C3C)

    header = (
        "## 🚨 Join-watch: likely raid troll\n"
        f"{member.mention} (`{member.id}`) was flagged on message "
        f"{len(entry['messages'])} of {MAX_SCANNED_MESSAGES}."
    )
    avatar_url = getattr(getattr(member, "display_avatar", None), "url", None)
    if avatar_url:
        card.add_item(
            discord.ui.Section(
                discord.ui.TextDisplay(header), accessory=discord.ui.Thumbnail(avatar_url)
            )
        )
    else:
        card.add_item(discord.ui.TextDisplay(header))
    card.add_item(discord.ui.Separator())

    profile_bits = [
        f"**Username** {discord.utils.escape_markdown(getattr(member, 'name', None) or '?')}"
    ]
    display_name = getattr(member, "display_name", None)
    if display_name and display_name != getattr(member, "name", None):
        profile_bits.append(f"**Display name** {discord.utils.escape_markdown(display_name)}")
    if created:
        profile_bits.append(f"**Account created** <t:{int(created.timestamp())}:R>")
    if joined:
        profile_bits.append(f"**Joined** <t:{int(joined.timestamp())}:R>")
    card.add_item(discord.ui.TextDisplay(" · ".join(profile_bits)))
    card.add_item(
        discord.ui.TextDisplay(
            f"🤖 **AI verdict** troll · confidence {confidence:.0%}\n{reason or '(no reason given)'}"
        )
    )

    lines = []
    deleted_any = False
    for i, m in enumerate(entry["messages"], 1):
        if m.get("deleted"):
            # the jump link is dead once the message is gone, so drop it and say why
            deleted_any = True
            tag = " · 🗑️ **deleted by join-watch**"
        elif m.get("delete_failed"):
            tag = (
                f" · ⚠️ **not deleted** ({m['delete_failed']})"
                + (f" · [jump]({m['jump_url']})" if m.get("jump_url") else "")
            )
        else:
            tag = f" · [jump]({m['jump_url']})" if m.get("jump_url") else ""
        lines.append(
            f"{i}. **{m['channel']}** <t:{m['ts']}:R>{tag}\n"
            f"{discord.utils.escape_markdown(m['content'])[:300]}"
        )
    card.add_item(discord.ui.TextDisplay(("### Messages\n" + "\n".join(lines))[:1500]))
    if deleted_any:
        card.add_item(
            discord.ui.TextDisplay(
                "🗑️ **The message that triggered this was deleted.** Its text is quoted above "
                "so you can still judge the call; removing the timeout does not restore it."
            )
        )
    card.add_item(discord.ui.Separator())
    card.add_item(discord.ui.ActionRow(UntimeoutButton(member.id)))
    card.add_item(
        discord.ui.TextDisplay(
            f"-# Action: {action} · flagged by join-watch AI screening; review and undo if wrong"
        )
    )
    view.add_item(card)
    return view


async def _get_channel(client: Any, channel_id: int) -> Any:
    channel = client.get_channel(channel_id)
    if channel is None:
        try:
            channel = await client.fetch_channel(channel_id)
        except Exception:
            logger.warning("join-watch could not reach channel %s", channel_id)
            return None
    return channel


async def _log_scan(
    client: Any,
    member: Any,
    entry: dict[str, Any],
    verdict: dict[str, Any] | None,
    call: str,
    confidence: float,
    outcome: str,
    usage: dict[str, int] | None,
) -> None:
    """Best-effort per-scan audit card in the bot usage log; never blocks screening."""
    try:
        channel = await _get_channel(client, CHANNELS.BOT_USAGE_LOG)
        if channel is None:
            return
        latest = entry["messages"][-1] if entry["messages"] else {}
        name = getattr(member, "display_name", None) or getattr(member, "name", None) or member.id
        if "timed out" in outcome:
            colour = 0xE74C3C
        elif call == "fine":
            colour = 0x2ECC71
        elif call == "troll":
            colour = 0xF39C12  # suspicious but below the action threshold
        else:
            colour = 0x95A5A6
        where = latest.get("channel", "?")
        if latest.get("jump_url"):
            where += f" · [jump]({latest['jump_url']})"
        text = (
            f"🔎 **{discord.utils.escape_markdown(str(name))}** (`{member.id}`) · "
            f"message {len(entry['messages'])}/{MAX_SCANNED_MESSAGES} · {where}\n"
            f"🤖 **{call}** ({confidence:.0%}) · {outcome}"
        )
        reason = " ".join(str((verdict or {}).get("reason", "")).split())[:200]
        if reason:
            text += f"\n-# {reason}"
        if latest.get("content"):
            text += f"\n> {latest['content'][:300]}"
        cost_line = _cost_footer(usage)
        if cost_line:
            text += f"\n-# {cost_line}"
        view = discord.ui.LayoutView(timeout=None)
        card = discord.ui.Container(accent_colour=colour)
        card.add_item(discord.ui.TextDisplay(text[:1900]))
        view.add_item(card)
        await channel.send(view=view, allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        logger.debug("join-watch could not write the usage-log line", exc_info=True)


async def announce_toggle(client: Any, actor: Any, enabled: bool) -> None:
    """Tell the police station who armed or disarmed join-watch."""
    channel = await _get_channel(client, CHANNELS.POLICE_STATION)
    if channel is None:
        return
    if enabled:
        state = get_join_watch_state()
        text = (
            "## 🔎 AI join-watch armed\n"
            f"{actor.mention} armed join-watch - new joiners' first "
            f"{MAX_SCANNED_MESSAGES} messages are now AI-screened.\n"
            # The stored context is capped at 1000 chars, so it always fits in the
            # card whole; staff need to read the exact wording they armed with.
            f"-# Context: {state['context']}"
        )
    else:
        text = (
            "## 💤 AI join-watch disarmed\n"
            f"{actor.mention} disarmed join-watch - new joiners are no longer screened."
        )
    view = discord.ui.LayoutView(timeout=None)
    card = discord.ui.Container(accent_colour=0xE74C3C if enabled else 0x2ECC71)
    card.add_item(discord.ui.TextDisplay(text))
    view.add_item(card)
    try:
        await channel.send(view=view, allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        logger.exception("join-watch could not post the police station toggle notice")


async def _send_report(
    client: Any, member: Any, entry: dict[str, Any], reason: str, confidence: float, action: str
) -> None:
    channel = await _get_channel(client, CHANNELS.POLICE_STATION)
    if channel is None:
        return
    try:
        await channel.send(
            view=_report_view(member, entry, reason, confidence, action),
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except Exception:
        logger.exception("join-watch could not post the police station report")


