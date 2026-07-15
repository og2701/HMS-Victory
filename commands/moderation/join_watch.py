"""Join-watch: oggers-only AI screening of new joiners' first messages.

While armed, the first few messages from recently joined members are sent to
Gemini together with profile context (names, account age, avatar and banner
images). Members the model confidently judges to be hostile raid trolls are
timed out and reported to the police station. The armed/disarmed toggle and
incident context survive restarts; per-member scan progress is in-memory only,
so a restart simply restarts a member's scan window.
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
from config import CHANNELS, JOIN_WATCH_FILE, ROLES, USERS
from lib.core.file_operations import load_json_file, save_json_file

logger = logging.getLogger(__name__)

MAX_SCANNED_MESSAGES = 5
MAX_MEMBER_AGE_HOURS = 48
TIMEOUT_HOURS = 24
# A timeout is only applied on a confident "troll" verdict; a confident "fine"
# verdict stops scanning early. Anything else keeps watching up to the cap.
ACT_CONFIDENCE = 0.7
CLEAR_CONFIDENCE = 0.7
STAFF_ROLE_IDS = {ROLES.MINISTER, ROLES.CABINET, ROLES.BORDER_FORCE}

DEFAULT_CONTEXT = (
    "England play Argentina in the World Cup semi-final tonight. Waves of newly "
    "joined accounts (many Argentinian) are trolling, spreading anti-England "
    "hate and trying to bait members."
)

_state_cache: dict[str, Any] | None = None
_buffers: dict[int, dict[str, Any]] = {}
_locks: dict[int, asyncio.Lock] = {}


# --- toggle state ---------------------------------------------------------------
def get_join_watch_state() -> dict[str, Any]:
    global _state_cache
    if _state_cache is None:
        data = load_json_file(JOIN_WATCH_FILE) or {}
        _state_cache = {
            "enabled": bool(data.get("enabled", False)),
            "context": str(data.get("context") or DEFAULT_CONTEXT)[:1000],
        }
    return dict(_state_cache)


def set_join_watch_state(enabled: bool, context: str | None = None) -> dict[str, Any]:
    global _state_cache
    current = get_join_watch_state()
    new_state = {
        "enabled": bool(enabled),
        "context": (context.strip()[:1000] if context and context.strip() else current["context"]),
    }
    save_json_file(JOIN_WATCH_FILE, {**new_state, "updated_at": int(time.time())})
    _state_cache = new_state
    _buffers.clear()
    _locks.clear()
    return dict(new_state)


def join_watch_enabled() -> bool:
    return get_join_watch_state()["enabled"]


# --- screening ------------------------------------------------------------------
def _eligible(member: Any) -> bool:
    if getattr(member, "bot", False):
        return False
    if getattr(member, "id", None) == USERS.OGGERS:
        return False
    joined_at = getattr(member, "joined_at", None)
    if joined_at is None:
        return False
    if discord.utils.utcnow() - joined_at > timedelta(hours=MAX_MEMBER_AGE_HOURS):
        return False
    if any(getattr(role, "id", None) in STAFF_ROLE_IDS for role in getattr(member, "roles", [])):
        return False
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


async def maybe_watch_message(client: Any, message: Any) -> None:
    """on_message hook: screen an eligible new joiner's message, acting at most once."""
    try:
        if not join_watch_enabled():
            return
        if message.guild is None:
            return
        member = message.author
        if not _eligible(member):
            return
        lock = _locks.setdefault(member.id, asyncio.Lock())
        async with lock:
            entry = _buffers.setdefault(member.id, {"messages": [], "done": False})
            if entry["done"]:
                return
            entry["messages"].append(_snapshot(message))
            verdict = await _evaluate(client, member, entry["messages"])
            await _apply_verdict(client, member, entry, verdict)
    except Exception:
        logger.exception("join-watch screening failed for message %s", getattr(message, "id", "?"))


async def _apply_verdict(
    client: Any, member: Any, entry: dict[str, Any], verdict: dict[str, Any] | None
) -> None:
    call = str((verdict or {}).get("verdict", "unsure")).lower()
    try:
        confidence = max(0.0, min(1.0, float((verdict or {}).get("confidence", 0))))
    except (TypeError, ValueError):
        confidence = 0.0
    if call == "troll" and confidence >= ACT_CONFIDENCE:
        entry["done"] = True
        await _action_troll(client, member, entry, verdict or {}, confidence)
    elif call == "fine" and confidence >= CLEAR_CONFIDENCE:
        entry["done"] = True
        logger.info(
            "join-watch cleared member %s after %d message(s)", member.id, len(entry["messages"])
        )
    elif len(entry["messages"]) >= MAX_SCANNED_MESSAGES:
        entry["done"] = True
        logger.info("join-watch finished watching member %s without a confident verdict", member.id)


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


def _build_prompt(member: Any, messages: list[dict[str, Any]], context: str, image_labels: list[str]) -> str:
    now = int(time.time())
    created = getattr(member, "created_at", None)
    joined = getattr(member, "joined_at", None)
    created_days = ((now - int(created.timestamp())) // 86400) if created else "unknown"
    joined_minutes = ((now - int(joined.timestamp())) // 60) if joined else "unknown"
    names = {
        "username": getattr(member, "name", ""),
        "display name": getattr(member, "display_name", ""),
        "global name": getattr(member, "global_name", "") or "",
    }
    name_lines = "\n".join(f"- {label}: {value}" for label, value in names.items() if value)
    message_lines = "\n".join(
        f'{i}. [{m["channel"]}] "{m["content"]}"' for i, m in enumerate(messages, 1)
    )
    attached = (
        "Attached image(s): " + ", ".join(image_labels) + "."
        if image_labels
        else "No profile images could be fetched."
    )
    return f"""You are the moderation screener for a casual, British, banter-heavy Discord server.
Strong language, swearing, dark humour and edgy football banter are completely normal here
and must NOT be flagged on their own.

INCIDENT CONTEXT (why screening is on right now):
{context}

A recently joined member is being screened. Decide whether they are a hostile raider or
troll who joined to disrupt (hate, slurs, bait, spam, brigading) rather than a genuine new
member. A rival fan being cocky or cheeky about football is NOT enough; look for clear
hostile intent. Weigh their name(s), account age, profile images and messages together.

MEMBER PROFILE
{name_lines}
- account created: {created_days} day(s) ago
- joined the server: {joined_minutes} minute(s) ago
{attached}

FIRST MESSAGES SINCE JOINING ({len(messages)} of {MAX_SCANNED_MESSAGES} scanned)
{message_lines}

If the evidence is thin, answer "unsure"; you will be shown their next message.
A "troll" verdict times the member out, so only give one when the intent is clear.

Respond with raw JSON only:
{{"verdict": "troll" | "unsure" | "fine", "confidence": <0.0-1.0>, "reason": "<one or two short sentences>"}}"""


async def _call_gemini_json(
    prompt: str, images: list[tuple[str, bytes]]
) -> tuple[str | None, str | None]:
    key = os.getenv("GEMINI_TOKEN") or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not key:
        return None, "no Gemini key in the environment"
    model = getattr(config, "JOIN_WATCH_GEMINI_MODEL", None) or getattr(
        config, "GEMINI_MODEL", "gemini-2.5-flash"
    )
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    parts: list[dict[str, Any]] = [{"text": prompt}]
    for mime, blob in images:
        parts.append(
            {"inline_data": {"mime_type": mime, "data": base64.b64encode(blob).decode("ascii")}}
        )
    body = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            # Thinking tokens share the output budget on 2.5+ models; the answer
            # itself is a tiny JSON object, so leave plenty of headroom.
            "temperature": 0.2,
            "maxOutputTokens": 8192,
            "responseMimeType": "application/json",
        },
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, json=body, timeout=aiohttp.ClientTimeout(total=60)
            ) as resp:
                status, data = resp.status, await resp.json()
    except Exception as exc:
        return None, f"request failed: {exc}"
    if status != 200:
        return None, f"HTTP {status}: {str(data)[:300]}"
    candidate = (data.get("candidates") or [{}])[0]
    answer_parts = (candidate.get("content") or {}).get("parts") or []
    text = "".join(
        p.get("text", "") for p in answer_parts if isinstance(p, dict) and not p.get("thought")
    )
    if not text:
        return None, f"no text (finishReason={candidate.get('finishReason')})"
    return text, None


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
) -> dict[str, Any] | None:
    context = get_join_watch_state()["context"]
    images = await _profile_images(client, member)
    prompt = _build_prompt(member, messages, context, [label for label, _, _ in images])
    raw, err = await _call_gemini_json(prompt, [(mime, blob) for _, mime, blob in images])
    if err:
        logger.warning("join-watch gemini error for member %s: %s", member.id, err)
        return None
    return _parse_verdict(raw)


# --- enforcement + reporting ------------------------------------------------------
async def _action_troll(
    client: Any, member: Any, entry: dict[str, Any], verdict: dict[str, Any], confidence: float
) -> None:
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
    logger.info(
        "join-watch flagged member %s at %.0f%% confidence: %s", member.id, confidence * 100, action
    )
    await _send_report(client, member, entry, reason, confidence, action)


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
    for i, m in enumerate(entry["messages"], 1):
        link = f" · [jump]({m['jump_url']})" if m.get("jump_url") else ""
        lines.append(
            f"{i}. **{m['channel']}** <t:{m['ts']}:R>{link}\n"
            f"{discord.utils.escape_markdown(m['content'])[:300]}"
        )
    card.add_item(discord.ui.TextDisplay(("### Messages\n" + "\n".join(lines))[:1500]))
    card.add_item(discord.ui.Separator())
    card.add_item(
        discord.ui.TextDisplay(
            f"-# Action: {action} · flagged by join-watch AI screening; review and undo if wrong"
        )
    )
    view.add_item(card)
    return view


async def _send_report(
    client: Any, member: Any, entry: dict[str, Any], reason: str, confidence: float, action: str
) -> None:
    channel = client.get_channel(CHANNELS.POLICE_STATION)
    if channel is None:
        try:
            channel = await client.fetch_channel(CHANNELS.POLICE_STATION)
        except Exception:
            logger.warning("join-watch could not reach the police station channel")
            return
    try:
        await channel.send(
            view=_report_view(member, entry, reason, confidence, action),
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except Exception:
        logger.exception("join-watch could not post the police station report")


