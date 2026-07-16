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
from config import CHANNELS, JOIN_WATCH_FILE, ROLES, USERS
from lib.core.file_operations import load_json_file, save_json_file

logger = logging.getLogger(__name__)

MAX_SCANNED_MESSAGES = 20
MAX_WATCHED_MEMBERS = 1000  # raid-scale bound on the in-memory watch list
TIMEOUT_HOURS = 24
# A timeout is only applied on a confident "troll" verdict. Every watched member
# is scanned for all MAX_SCANNED_MESSAGES messages unless actioned first.
ACT_CONFIDENCE = 0.7
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
    while len(_buffers) >= MAX_WATCHED_MEMBERS:
        evicted = next(iter(_buffers))
        _buffers.pop(evicted, None)
        _locks.pop(evicted, None)
    _buffers.setdefault(member.id, {"messages": [], "done": False})
    logger.info("join-watch is now listening to new joiner %s", member.id)


def _snapshot(message: Any) -> dict[str, Any]:
    content = " ".join((message.content or "").split())[:400]
    if not content:
        content = f"[no text; {len(getattr(message, 'attachments', None) or [])} attachment(s)]"
    return {
        "channel": f"#{getattr(message.channel, 'name', message.channel)}",
        "content": content,
        "jump_url": getattr(message, "jump_url", ""),
        "ts": int(message.created_at.timestamp()),
        # Live reference kept so a troll verdict can delete the evidence trail;
        # never persisted and never sent to the model.
        "message": message,
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
            await _apply_verdict(client, member, entry, verdict, usage)
    except Exception:
        logger.exception("join-watch screening failed for message %s", getattr(message, "id", "?"))


async def _apply_verdict(
    client: Any,
    member: Any,
    entry: dict[str, Any],
    verdict: dict[str, Any] | None,
    usage: dict[str, int] | None,
) -> None:
    call = str(verdict.get("verdict", "unsure")).lower() if verdict else "no verdict (model error)"
    try:
        confidence = max(0.0, min(1.0, float((verdict or {}).get("confidence", 0))))
    except (TypeError, ValueError):
        confidence = 0.0
    if call == "troll" and confidence >= ACT_CONFIDENCE:
        entry["done"] = True
        outcome = f"timed out for {TIMEOUT_HOURS}h and reported"
        await _action_troll(client, member, entry, verdict or {}, confidence)
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
    prompt: str, images: list[tuple[str, bytes]]
) -> tuple[str | None, str | None, dict[str, Any] | None]:
    key = os.getenv("OPENAI_TOKEN") or os.getenv("OPENAI_API_KEY")
    if not key:
        return None, "no OpenAI key in the environment", None
    try:
        from openai import AsyncOpenAI
    except ImportError:
        return None, "openai package not installed", None
    model = _openai_model()
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
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
    request = dict(
        model=model,
        messages=[{"role": "user", "content": content}],
        response_format={"type": "json_object"},
        # GPT-5 family: max_tokens is rejected (use max_completion_tokens) and
        # only the default temperature is supported. Reasoning is billed as
        # output, so keep the effort down for this simple judgment.
        max_completion_tokens=2048,
    )
    client = AsyncOpenAI(api_key=key, max_retries=2, timeout=60.0)
    response, last_error = None, None
    for attempt in (dict(request, reasoning_effort="low"), request):
        try:
            response = await client.chat.completions.create(**attempt)
            break
        except Exception as exc:
            last_error = exc
            if "reasoning_effort" not in str(exc):
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
    prompt: str, images: list[tuple[str, bytes]]
) -> tuple[str | None, str | None, dict[str, int] | None]:
    key = os.getenv("GEMINI_TOKEN") or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not key:
        return None, "no Gemini key in the environment", None
    model = _gemini_model()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    parts: list[dict[str, Any]] = [{"text": prompt}]
    for mime, blob in images:
        parts.append(
            {"inline_data": {"mime_type": mime, "data": base64.b64encode(blob).decode("ascii")}}
        )
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
    prompt = _build_prompt(member, messages, context, [label for label, _, _ in images])
    image_blobs = [(mime, blob) for _, mime, blob in images]
    raw, err, usage = await _call_openai_json(prompt, image_blobs)
    if err:
        logger.warning(
            "join-watch openai error for member %s: %s; falling back to gemini", member.id, err
        )
        raw, err, usage = await _call_gemini_json(prompt, image_blobs)
    if err:
        logger.warning("join-watch gemini error for member %s: %s", member.id, err)
        return None, usage
    return _parse_verdict(raw), usage


# --- enforcement + reporting ------------------------------------------------------
async def _delete_recorded_messages(entry: dict[str, Any]) -> tuple[int, int]:
    """Delete the flagged messages, marking each snapshot; contents stay in the report."""
    deleted = 0
    total = 0
    for snapshot in entry["messages"]:
        message = snapshot.get("message")
        if message is None:
            continue
        total += 1
        try:
            await message.delete()
            snapshot["deleted"] = True
            deleted += 1
        except discord.NotFound:
            snapshot["deleted"] = True
            deleted += 1
        except (discord.Forbidden, discord.HTTPException):
            snapshot["deleted"] = False
    return deleted, total


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
    deleted, total = await _delete_recorded_messages(entry)
    action += f" · {deleted}/{total} message(s) deleted"
    logger.info(
        "join-watch flagged member %s at %.0f%% confidence: %s", member.id, confidence * 100, action
    )
    await _send_report(client, member, entry, reason, confidence, action)


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
    for i, m in enumerate(entry["messages"], 1):
        if m.get("deleted"):
            suffix = " · ✂️ deleted"
        elif m.get("deleted") is False:
            suffix = f" · ⚠️ could not delete · [jump]({m['jump_url']})" if m.get("jump_url") else " · ⚠️ could not delete"
        elif m.get("jump_url"):
            suffix = f" · [jump]({m['jump_url']})"
        else:
            suffix = ""
        lines.append(
            f"{i}. **{m['channel']}** <t:{m['ts']}:R>{suffix}\n"
            f"{discord.utils.escape_markdown(m['content'])[:300]}"
        )
    card.add_item(discord.ui.TextDisplay(("### Messages\n" + "\n".join(lines))[:1500]))
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
            f"-# Context: {state['context'][:300]}"
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


