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

_ALLOWED_ROLES = lambda: [config.ROLES.DEPUTY_PM]  # Deputy PM only for now


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
    key = os.getenv("GEMINI_TOKEN") or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not key:
        return None, "No Gemini key in the environment (GEMINI_TOKEN / GEMINI_API_KEY)."
    model = getattr(config, "GEMINI_MODEL", "gemini-2.0-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": 4096,            # 2.5 "thinking" shares this budget; keep it roomy
            "responseMimeType": "application/json",
        },
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
        "recent messages against the server rules. Be balanced: note positives, account for banter "
        "and context, do NOT over-flag, and never invent quotes (use only verbatim text from the "
        "messages). If nothing is wrong, say so plainly.\n\n"
        f"SERVER RULES:\n{rules}\n\n"
        f"MEMBER: {member.display_name} (id {member.id}). {len(msgs)} recent messages, oldest first. "
        "Context in {curly braces} is what they replied to / the message before / reactions.\n\n"
        f"{body}\n\n"
        "Respond with ONLY a JSON object (no markdown fences) with these keys:\n"
        '  "summary": 2-4 sentences on overall tone and behaviour.\n'
        '  "tone": a short phrase (e.g. "friendly banter", "argumentative", "edgy humour").\n'
        '  "risk_level": one of "low", "medium", "high" (overall moderation risk).\n'
        '  "concerns": array of {"severity":"low|medium|high","issue":<short>,"quote":<verbatim>,'
        '"why":<short reason>}; empty array if none.\n'
        '  "notable_quotes": array of up to 5 short verbatim quotes that characterise them '
        "(telling, funny, or concerning).\n"
        '  "patterns": a short note on any behavioural pattern (targets a person, repeats a topic, '
        "time-of-day, escalation), or empty string.\n"
        '  "positives": one line on positive contributions.\n'
        '  "recommended_action": one of "No action","Keep an eye on it","Informal nudge",'
        '"Formal warning","Escalate to senior staff".\n'
        '  "justification": one or two sentences supporting the action.'
    )


_RISK_COLOR = {"low": 0x10B981, "medium": 0xF59E0B, "high": 0xEF4444}
_RISK_EMOJI = {"low": "\U0001f7e2", "medium": "\U0001f7e1", "high": "\U0001f534"}
_SEV_EMOJI = {"low": "\U0001f7e1", "medium": "\U0001f7e0", "high": "\U0001f534"}


def _activity_line(msgs):
    import datetime
    from collections import Counter
    counts = Counter(m["channel"] for m in msgs)
    top = " · ".join(f"#{c} ({n})" for c, n in counts.most_common(4))
    reacts = sum(m["reactions"] for m in msgs)
    fmt = lambda ts: datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%-d %b")
    span = f"{fmt(msgs[0]['ts'])} to {fmt(msgs[-1]['ts'])}"
    return f"**{len(msgs)}** msgs · **{len(counts)}** channels ({top}) · **{reacts}** reactions · {span}"


def _build_embed(member, requester, msgs, text):
    import json
    import re
    data = None
    raw = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.MULTILINE).strip()
    try:
        data = json.loads(raw)
    except Exception:
        log.debug("gemini json parse failed", exc_info=True)

    if not isinstance(data, dict):
        embed = discord.Embed(title=f"\U0001f50e Moderation analysis: {member.display_name}",
                              description=text[:4096], color=0xCF142B)
    else:
        risk = str(data.get("risk_level", "low")).lower()
        embed = discord.Embed(
            title=f"\U0001f50e Moderation analysis: {member.display_name}",
            description=(data.get("summary") or "")[:1500],
            color=_RISK_COLOR.get(risk, 0xCF142B))
        embed.add_field(name="Risk", value=f"{_RISK_EMOJI.get(risk, '⚪')} {risk.capitalize()}", inline=True)
        if data.get("tone"):
            embed.add_field(name="Tone", value=str(data["tone"])[:1024], inline=True)
        action = data.get("recommended_action") or "-"
        embed.add_field(name="Recommended action",
                        value=f"**{action}**\n{data.get('justification', '')}"[:1024], inline=False)
        concerns = data.get("concerns") or []
        if concerns:
            blocks = []
            for c in concerns[:6]:
                se = _SEV_EMOJI.get(str(c.get("severity", "low")).lower(), "\U0001f7e1")
                blk = f"{se} **{c.get('issue', '')}**"
                if c.get("quote"):
                    blk += f"\n> {str(c['quote'])[:170]}"
                if c.get("why"):
                    blk += f"\n_{str(c['why'])[:170]}_"
                blocks.append(blk)
            embed.add_field(name="Concerns", value="\n\n".join(blocks)[:1024], inline=False)
        else:
            embed.add_field(name="Concerns", value="None notable.", inline=False)
        if data.get("patterns"):
            embed.add_field(name="Patterns", value=str(data["patterns"])[:1024], inline=False)
        quotes = data.get("notable_quotes") or []
        if quotes:
            embed.add_field(name="Notable quotes",
                            value="\n".join(f"> {str(q)[:150]}" for q in quotes[:5])[:1024], inline=False)
        if data.get("positives"):
            embed.add_field(name="Positives", value=str(data["positives"])[:1024], inline=False)

    embed.set_author(name=str(member), icon_url=member.display_avatar.url)
    embed.add_field(name="Activity", value=_activity_line(msgs)[:1024], inline=False)
    embed.add_field(name="Member", value=f"{member.mention} (`{member.id}`)", inline=True)
    embed.add_field(name="Requested by", value=requester.mention, inline=True)
    embed.set_footer(text=f"{len(msgs)} recent messages analysed by AI - use your own judgement.")
    return embed


# --- entry point --------------------------------------------------------------
async def handle_analyse_user(interaction, member):
    from lib.core.discord_helpers import has_any_role
    if not has_any_role(interaction, _ALLOWED_ROLES()):
        await interaction.response.send_message("This tool is Deputy PM only for now.", ephemeral=True)
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

    channel = interaction.client.get_channel(config.USER_ANALYSIS_CHANNEL_ID)
    if channel is None:
        try:
            channel = await interaction.client.fetch_channel(config.USER_ANALYSIS_CHANNEL_ID)
        except Exception:
            await interaction.followup.send("Couldn't reach the police station channel.", ephemeral=True)
            return

    embed = _build_embed(member, interaction.user, msgs, text)
    try:
        await channel.send(embed=embed)
    except Exception:
        log.error("failed to post user analysis", exc_info=True)
        await interaction.followup.send("Generated the report but couldn't post it.", ephemeral=True)
        return
    await interaction.followup.send(
        f"Analysis of {member.mention} posted to <#{config.USER_ANALYSIS_CHANNEL_ID}>.", ephemeral=True)
