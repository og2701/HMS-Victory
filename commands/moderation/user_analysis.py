"""'Analyse User' moderation tool (right-click a member -> Apps -> Analyse User).

Gathers a member's recent messages across the server with light context (what they replied
to, the message just before, reactions, channel), then asks Gemini to review them against
the server rules and recommend an action. The report is posted to the police station; the
moderator who ran it gets a quiet ephemeral confirmation. Staff-only.
"""

import logging
import os

import aiohttp
import discord

import config

log = logging.getLogger(__name__)

_STAFF_ROLES = lambda: [config.ROLES.MINISTER, config.ROLES.CABINET, config.ROLES.BORDER_FORCE]


# --- gather the member's recent messages with context -------------------------
async def gather_user_messages(client, guild, user, target=100, per_channel=300, max_channels=40):
    me = guild.me
    channels = [c for c in guild.text_channels
                if c.permissions_for(me).read_message_history]
    channels.sort(key=lambda c: (c.last_message_id or 0), reverse=True)  # busiest/most-recent first
    collected = []
    for ch in channels[:max_channels]:
        if len(collected) >= target:
            break
        capture_before_for = None
        try:
            async for msg in ch.history(limit=per_channel):  # newest-first
                if capture_before_for is not None:
                    # this message is the one immediately BEFORE the recorded one (older)
                    if msg.author.id != user.id:
                        capture_before_for["before"] = f"{msg.author.display_name}: {(msg.content or '')[:110]}"
                    capture_before_for = None
                if msg.author.id == user.id and (msg.content or msg.attachments):
                    content = (msg.content or "").replace("\n", " ")[:300]
                    if msg.attachments:
                        content += " [attachment]"
                    entry = {
                        "ts": int(msg.created_at.timestamp()),
                        "channel": ch.name,
                        "content": content,
                        "jump": msg.jump_url,
                        "reactions": sum(r.count for r in msg.reactions),
                        "reply_to": None,
                        "before": None,
                    }
                    ref = msg.reference
                    if ref is not None and isinstance(getattr(ref, "resolved", None), discord.Message):
                        r = ref.resolved
                        entry["reply_to"] = f"{r.author.display_name}: {(r.content or '')[:120]}"
                    collected.append(entry)
                    capture_before_for = entry
                    if len(collected) >= target:
                        break
        except Exception:
            continue
    collected.sort(key=lambda e: e["ts"])  # chronological for the model
    return collected


# --- rules source -------------------------------------------------------------
async def _load_rules(client):
    cid = getattr(config, "RULES_CHANNEL_ID", None)
    if cid:
        try:
            ch = client.get_channel(cid) or await client.fetch_channel(cid)
            text = "\n".join([m.content async for m in ch.history(limit=25, oldest_first=True) if m.content])
            if text.strip():
                return text[:6000]
        except Exception:
            log.debug("rules channel read failed", exc_info=True)
    try:
        with open("data/rules.txt") as f:
            text = f.read()
            if text.strip():
                return text[:6000]
    except Exception:
        pass
    return ("(No server rules configured - set RULES_CHANNEL_ID or data/rules.txt.) Apply "
            "general Discord conduct standards: no harassment, hate speech or slurs, NSFW, "
            "spam/raiding, doxxing, threats, or illegal content.")


# --- Gemini -------------------------------------------------------------------
async def _call_gemini(prompt):
    key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not key:
        return None, "GEMINI_API_KEY is not set in the environment."
    model = getattr(config, "GEMINI_MODEL", "gemini-2.0-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 1200},
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=body, timeout=aiohttp.ClientTimeout(total=60)) as r:
                data = await r.json()
                if r.status != 200:
                    return None, f"Gemini error {r.status}: {str(data)[:300]}"
                return data["candidates"][0]["content"]["parts"][0]["text"], None
    except Exception as e:
        return None, f"Gemini request failed: {e}"


def _build_prompt(member, msgs, rules):
    lines = []
    for i, m in enumerate(msgs, 1):
        ctx = []
        if m["reply_to"]:
            ctx.append(f"reply to [{m['reply_to']}]")
        if m["before"]:
            ctx.append(f"after [{m['before']}]")
        if m["reactions"]:
            ctx.append(f"{m['reactions']} reactions")
        tag = (" {" + "; ".join(ctx) + "}") if ctx else ""
        lines.append(f"{i}. #{m['channel']}{tag}: {m['content']}")
    body = "\n".join(lines)
    return (
        "You are a fair, careful moderation assistant for a Discord server. Review the member's "
        "recent messages against the server rules. Be balanced: note positives, do NOT over-flag, "
        "and only raise genuine concerns, each with a short quoted example and a severity "
        "(low/medium/high). Account for banter and context. If nothing is wrong, say so plainly.\n\n"
        f"SERVER RULES:\n{rules}\n\n"
        f"MEMBER: {member.display_name} (id {member.id}). {len(msgs)} recent messages, oldest first. "
        "Context in {curly braces} is what they replied to / the message before / reactions.\n\n"
        f"{body}\n\n"
        "Respond in this exact structure, concise and skimmable:\n"
        "**Summary** - 2-3 lines on overall tone and behaviour.\n"
        "**Concerns** - bullets, each `severity - issue - \"quoted example\"`; or `None notable`.\n"
        "**Positives** - one line.\n"
        "**Recommended action** - one of: No action / Keep an eye on it / Informal nudge / "
        "Formal warning / Escalate to senior staff - then one line of justification."
    )


# --- entry point --------------------------------------------------------------
async def handle_analyse_user(interaction, member):
    from lib.core.discord_helpers import has_any_role
    if not has_any_role(interaction, _STAFF_ROLES()):
        await interaction.response.send_message("This tool is staff-only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)

    msgs = await gather_user_messages(
        interaction.client, interaction.guild, member,
        target=getattr(config, "USER_ANALYSIS_MSG_LIMIT", 100))
    if not msgs:
        await interaction.followup.send(
            f"Couldn't find recent messages from {member.mention} in channels I can read.",
            ephemeral=True)
        return

    rules = await _load_rules(interaction.client)
    text, err = await _call_gemini(_build_prompt(member, msgs, rules))
    if err:
        await interaction.followup.send(f"Analysis failed: {err}", ephemeral=True)
        return

    channel = interaction.client.get_channel(config.CHANNELS.POLICE_STATION)
    if channel is None:
        try:
            channel = await interaction.client.fetch_channel(config.CHANNELS.POLICE_STATION)
        except Exception:
            await interaction.followup.send("Couldn't reach the police station channel.", ephemeral=True)
            return

    embed = discord.Embed(
        title=f"\U0001f50e Moderation analysis: {member.display_name}",
        description=text[:4096], color=0xCF142B)
    embed.set_author(name=str(member), icon_url=member.display_avatar.url)
    embed.add_field(name="Member", value=f"{member.mention} (`{member.id}`)", inline=True)
    embed.add_field(name="Requested by", value=interaction.user.mention, inline=True)
    embed.set_footer(text=f"{len(msgs)} recent messages analysed by AI - use your own judgement.")
    try:
        await channel.send(embed=embed)
    except Exception:
        log.error("failed to post user analysis", exc_info=True)
        await interaction.followup.send("Generated the report but couldn't post it.", ephemeral=True)
        return
    await interaction.followup.send(
        f"Analysis of {member.mention} posted to <#{config.CHANNELS.POLICE_STATION}>.", ephemeral=True)
