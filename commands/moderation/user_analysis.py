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


# --- scraped-message cache (so follow-ups skip re-scraping Discord) -----------
def _save_context(member_id, msgs):
    try:
        import time
        from lib.core.file_operations import load_json_file, save_json_file
        store = load_json_file(config.USER_ANALYSIS_CONTEXT_FILE) or {}
        store[str(member_id)] = {"ts": int(time.time()), "msgs": msgs}
        cutoff = int(time.time()) - 7 * 86400          # drop entries older than a week
        store = {k: v for k, v in store.items() if v.get("ts", 0) >= cutoff}
        save_json_file(config.USER_ANALYSIS_CONTEXT_FILE, store)
        log.info("[analyse] cached %d msgs for member %s (store now holds %d members)",
                 len(msgs), member_id, len(store))
    except Exception:
        log.warning("[analyse] failed to cache context for %s", member_id, exc_info=True)


def _load_context(member_id):
    try:
        from lib.core.file_operations import load_json_file
        entry = (load_json_file(config.USER_ANALYSIS_CONTEXT_FILE) or {}).get(str(member_id))
        if entry and entry.get("msgs"):
            return entry["msgs"]
    except Exception:
        log.warning("[analyse] failed to load cached context for %s", member_id, exc_info=True)
    return None


# --- gather the member's recent messages with context -------------------------
async def gather_user_messages(client, guild, user, channel_ids, target=250, per_channel=40000,
                               days=14, concurrency=4, progress=None):
    """Scan only `channel_ids` (the main chats) in parallel for the member's recent messages.

    Discord has no per-user message API, so we read history and filter. Each channel scan
    stops once its messages cross `days` old, and overall stops at `target` (or returns
    however many it finds within the window).
    """
    import asyncio
    import time
    from datetime import timedelta

    me = guild.me
    cutoff = discord.utils.utcnow() - timedelta(days=days)
    chans = [guild.get_channel(cid) for cid in channel_ids]
    chans = [c for c in chans if c is not None and c.permissions_for(me).read_message_history]
    t_start = time.monotonic()
    log.info("[analyse] scanning %d channels for member %s (target=%d, days=%d): %s",
             len(chans), user.id, target, days, [c.name for c in chans])

    collected = []
    state = {"scanned": 0, "last_edit": 0.0}
    lock = asyncio.Lock()
    stop = asyncio.Event()

    async def _tick(ch_name):
        if progress is None:
            return
        now = time.monotonic()
        if now - state["last_edit"] < 1.5:
            return
        state["last_edit"] = now
        await progress(state["scanned"], len(collected), ch_name)

    async def scan(ch):
        before_for = None
        cursor = None      # resume point (oldest message seen) so a transient error doesn't abandon the channel
        seen = [0]         # messages read in this channel (our own per-channel cap)
        found = [0]        # the member's messages found here
        why = "exhausted"
        for attempt in range(4):
            try:
                async for msg in ch.history(limit=None, before=cursor):  # newest-first, resume-able
                    cursor = msg.created_at
                    seen[0] += 1
                    if stop.is_set():
                        why = "target reached"; break
                    if msg.created_at < cutoff:
                        why = "past 2-week window"; break
                    if seen[0] >= per_channel:
                        why = "hit per-channel cap"; break
                    async with lock:
                        state["scanned"] += 1
                    if before_for is not None:
                        if msg.author.id != user.id:
                            before_for["before"] = f"{msg.author.display_name}: {(msg.content or '')[:110]}"
                        before_for = None
                    if msg.author.id == user.id and (msg.content or msg.attachments):
                        content = (msg.content or "").replace("\n", " ")[:300]
                        if msg.attachments:
                            content += " [attachment]"
                        entry = {
                            "ts": int(msg.created_at.timestamp()), "channel": ch.name,
                            "content": content, "jump": msg.jump_url,
                            "reactions": sum(r.count for r in msg.reactions),
                            "reply_to": None, "before": None,
                        }
                        ref = msg.reference
                        if ref is not None and isinstance(getattr(ref, "resolved", None), discord.Message):
                            r = ref.resolved
                            entry["reply_to"] = f"{r.author.display_name}: {(r.content or '')[:120]}"
                        async with lock:
                            collected.append(entry)
                            found[0] += 1
                            if len(collected) >= target:
                                stop.set()
                        before_for = entry
                    await _tick(ch.name)
                break  # finished this channel (broke out or history exhausted)
            except Exception:
                log.warning("[analyse] history error in #%s (attempt %d/4), resuming from cursor",
                            ch.name, attempt + 1, exc_info=True)
                await asyncio.sleep(1.5)
        log.info("[analyse] #%s done: found %d of theirs from %d read (%s)",
                 ch.name, found[0], seen[0], why)

    sem = asyncio.Semaphore(concurrency)

    async def run(ch):
        async with sem:
            if not stop.is_set():
                await scan(ch)

    await asyncio.gather(*(run(c) for c in chans))          # the chosen channels, in parallel

    collected.sort(key=lambda e: e["ts"])  # chronological for the model
    log.info("[analyse] scan complete for %s: %d msgs from %d messages read in %.1fs",
             user.id, len(collected), state["scanned"], time.monotonic() - t_start)
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
async def _call_gemini(prompt, json_mode=True):
    key = os.getenv("GEMINI_TOKEN") or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not key:
        return None, "No Gemini key in the environment (GEMINI_TOKEN / GEMINI_API_KEY)."
    model = getattr(config, "GEMINI_MODEL", "gemini-2.0-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    # 2.5 "thinking" tokens share the output budget, so keep it large; cap thinking so the
    # actual answer can't get starved/truncated (which breaks JSON parsing).
    base = {"temperature": 0.3, "maxOutputTokens": 8192}
    if json_mode:
        base["responseMimeType"] = "application/json"
    import time as _t
    t0 = _t.monotonic()
    log.info("[analyse] gemini call model=%s json=%s prompt_chars=%d", model, json_mode, len(prompt))

    async def _post(gen):
        body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": gen}
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=body, timeout=aiohttp.ClientTimeout(total=90)) as r:
                return r.status, await r.json()

    # Try with capped thinking first; if the API rejects thinkingConfig (400), retry without it.
    attempts = [dict(base, thinkingConfig={"thinkingBudget": 2048}), base]
    status, data = None, None
    for gen in attempts:
        try:
            status, data = await _post(gen)
        except Exception as e:
            log.warning("[analyse] gemini request error in %.1fs: %s", _t.monotonic() - t0, e, exc_info=True)
            return None, f"Gemini request failed: {e}"
        if status == 200:
            try:
                out = data["candidates"][0]["content"]["parts"][0]["text"]
            except Exception:
                fr = (data.get("candidates") or [{}])[0].get("finishReason")
                log.warning("[analyse] gemini 200 but no text (finishReason=%s): %s", fr, str(data)[:300])
                return None, f"Gemini returned no text (finishReason={fr})."
            log.info("[analyse] gemini ok in %.1fs (%d chars out)", _t.monotonic() - t0, len(out or ""))
            return out, None
        log.warning("[analyse] gemini HTTP %d: %s", status, str(data)[:300])
        if status != 400:
            break  # only a 400 is worth retrying without thinkingConfig
    return None, f"Gemini error {status}: {str(data)[:300]}"


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


def _build_followup_prompt(member, msgs, rules, question):
    body = "\n".join(f"{i}. #{m['channel']}: {m['content']}" for i, m in enumerate(msgs, 1))
    return (
        "You are a moderation assistant. A moderator has a follow-up question about a member. "
        "Answer concisely and factually using ONLY the member's recent messages below (and the "
        "rules for context). Quote verbatim where useful. If the messages don't show enough to "
        "answer, say so plainly. Do not invent anything.\n\n"
        f"SERVER RULES:\n{rules}\n\n"
        f"MEMBER: {member.display_name}. {len(msgs)} recent messages:\n{body}\n\n"
        f"MODERATOR'S QUESTION: {question}"
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


class FollowupModal(discord.ui.Modal, title="Ask about this member"):
    question = discord.ui.TextInput(
        label="Your question", style=discord.TextStyle.paragraph, max_length=300,
        placeholder="e.g. is the politics stuff a pattern? are they targeting anyone?")

    def __init__(self, user_id):
        super().__init__()
        self.user_id = int(user_id)

    async def on_submit(self, interaction):
        from lib.core.discord_helpers import has_any_role
        if not has_any_role(interaction, _ALLOWED_ROLES()):
            await interaction.response.send_message("Deputy PM only for now.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        member = interaction.guild.get_member(self.user_id)
        if member is None:
            try:
                member = await interaction.guild.fetch_member(self.user_id)
            except Exception:
                member = None
        if member is None:
            await interaction.followup.send("Couldn't resolve that member.", ephemeral=True)
            return
        q = str(self.question.value)
        log.info("[analyse] follow-up by %s about %s: %r", interaction.user.id, self.user_id, q[:120])
        msgs = _load_context(self.user_id)  # reuse the first scan, no re-scrape
        if msgs:
            log.info("[analyse] follow-up using %d cached msgs for %s (no re-scrape)",
                     len(msgs), self.user_id)
        else:
            log.info("[analyse] follow-up cache miss for %s, re-scraping", self.user_id)
            msgs = await gather_user_messages(
                interaction.client, interaction.guild, member,
                getattr(config, "USER_ANALYSIS_CHANNELS", []),
                target=getattr(config, "USER_ANALYSIS_MSG_LIMIT", 250),
                days=getattr(config, "USER_ANALYSIS_DAYS", 14))
            _save_context(self.user_id, msgs)
        if not msgs:
            await interaction.followup.send("No recent messages to answer from.", ephemeral=True)
            return
        rules = await _load_rules(interaction.client)
        text, err = await _call_gemini(_build_followup_prompt(member, msgs, rules, q), json_mode=False)
        if err:
            await interaction.followup.send(f"Follow-up failed: {err}", ephemeral=True)
            return
        try:
            await interaction.channel.send(
                view=_followup_view(member, interaction.user, q, text),
                allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            await interaction.followup.send("Couldn't post the answer here.", ephemeral=True)
            return
        await interaction.followup.send("Posted.", ephemeral=True)


class FollowupButton(discord.ui.DynamicItem[discord.ui.Button], template=r"analysefu:(?P<uid>\d+)"):
    """Persistent per-member 'Ask a follow-up' button (the member id rides in the custom_id)."""

    def __init__(self, user_id):
        self.user_id = int(user_id)
        super().__init__(discord.ui.Button(
            label="Ask a follow-up", emoji="\U0001f4ac",
            style=discord.ButtonStyle.secondary, custom_id=f"analysefu:{self.user_id}"))

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["uid"]))

    async def callback(self, interaction):
        from lib.core.discord_helpers import has_any_role
        if not has_any_role(interaction, _ALLOWED_ROLES()):
            await interaction.response.send_message("Deputy PM only for now.", ephemeral=True)
            return
        await interaction.response.send_modal(FollowupModal(self.user_id))


def _build_view(member, requester, msgs, text):
    """Compact Components V2 report: one accent-coloured container of markdown blocks."""
    import json
    import re
    data = None
    raw = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.MULTILINE).strip()
    try:
        data = json.loads(raw)
    except Exception:
        log.debug("gemini json parse failed", exc_info=True)

    name = discord.utils.escape_markdown(member.display_name)
    foot = (f"-# {_activity_line(msgs)}\n-# {member.mention} (`{member.id}`) · by {requester.mention} · "
            f"{len(msgs)} msgs")
    view = discord.ui.LayoutView(timeout=None)

    def _header_section(text_md):
        # header text with the member's avatar as a thumbnail accessory
        return discord.ui.Section(
            discord.ui.TextDisplay(text_md),
            accessory=discord.ui.Thumbnail(member.display_avatar.url))

    if not isinstance(data, dict):
        c = discord.ui.Container(accent_colour=0xCF142B)
        c.add_item(_header_section(f"## \U0001f50e Moderation analysis: {name}\n{(text or '')[:3500]}"))
        c.add_item(discord.ui.TextDisplay(foot))
        c.add_item(discord.ui.ActionRow(FollowupButton(member.id)))
        view.add_item(c)
        return view

    risk = str(data.get("risk_level", "low")).lower()
    c = discord.ui.Container(accent_colour=_RISK_COLOR.get(risk, 0xCF142B))

    head = f"## \U0001f50e Moderation analysis: {name}\n{_RISK_EMOJI.get(risk, '⚪')} **{risk.capitalize()} risk**"
    if data.get("tone"):
        head += f" · *{str(data['tone'])[:80]}*"
    if data.get("recommended_action"):
        head += f" · ⚖️ **{data['recommended_action']}**"
    if data.get("summary"):
        head += f"\n{str(data['summary'])[:1200]}"
    if data.get("justification"):
        head += f"\n-# {str(data['justification'])[:300]}"
    c.add_item(_header_section(head))

    concerns = data.get("concerns") or []
    if concerns:
        lines = ["**Concerns**"]
        for con in concerns[:6]:
            se = _SEV_EMOJI.get(str(con.get("severity", "low")).lower(), "\U0001f7e1")
            seg = f"{se} **{con.get('issue', '')}**"
            if con.get("quote"):
                seg += f' · "{str(con["quote"])[:120]}"'
            if con.get("why"):
                seg += f" · _{str(con['why'])[:120]}_"
            lines.append(seg)
        c.add_item(discord.ui.TextDisplay("\n".join(lines)[:3500]))
    else:
        c.add_item(discord.ui.TextDisplay("**Concerns** None notable."))

    extra = []
    if data.get("patterns"):
        extra.append(f"**Patterns** {str(data['patterns'])[:400]}")
    quotes = data.get("notable_quotes") or []
    if quotes:
        extra.append("**Notable quotes** " + " · ".join(f'"{str(q)[:80]}"' for q in quotes[:5]))
    if data.get("positives"):
        extra.append(f"**Positives** {str(data['positives'])[:400]}")
    if extra:
        c.add_item(discord.ui.TextDisplay("\n".join(extra)[:3500]))

    c.add_item(discord.ui.TextDisplay(foot))
    c.add_item(discord.ui.ActionRow(FollowupButton(member.id)))
    view.add_item(c)
    return view


def _followup_view(member, requester, question, answer):
    """Components V2 card for a follow-up Q&A (blurple to distinguish from the report)."""
    name = discord.utils.escape_markdown(member.display_name)
    view = discord.ui.LayoutView(timeout=None)
    c = discord.ui.Container(accent_colour=0x5865F2)
    c.add_item(discord.ui.Section(
        discord.ui.TextDisplay(f"## \U0001f4ac Follow-up: {name}\n**Q:** {question[:300]}"),
        accessory=discord.ui.Thumbnail(member.display_avatar.url)))
    c.add_item(discord.ui.TextDisplay((answer or "").strip()[:3500] or "_(no answer)_"))
    c.add_item(discord.ui.TextDisplay(f"-# asked by {requester.mention} about {member.mention}"))
    view.add_item(c)
    return view


# --- entry point --------------------------------------------------------------
async def handle_analyse_user(interaction, member):
    from lib.core.discord_helpers import has_any_role
    if not has_any_role(interaction, _ALLOWED_ROLES()):
        await interaction.response.send_message("This tool is Deputy PM only for now.", ephemeral=True)
        return
    import time
    log.info("[analyse] %s (%s) requested analysis of %s (%s)",
             interaction.user, interaction.user.id, member, member.id)
    await interaction.response.defer(ephemeral=True, thinking=True)

    async def _status(content):
        try:
            await interaction.edit_original_response(content=content)
        except Exception:
            pass

    last_edit = [0.0]

    async def progress(scanned, found, ch_name):
        now = time.monotonic()
        if now - last_edit[0] < 1.5:  # throttle edits to dodge rate limits
            return
        last_edit[0] = now
        await _status(f"\U0001f50d Scanning **#{ch_name}** ... {scanned:,} messages read, "
                      f"**{found}** from {member.display_name} so far.")

    await _status(f"\U0001f50d Gathering {member.display_name}'s recent messages ...")
    msgs = await gather_user_messages(
        interaction.client, interaction.guild, member,
        getattr(config, "USER_ANALYSIS_CHANNELS", []),
        target=getattr(config, "USER_ANALYSIS_MSG_LIMIT", 250),
        days=getattr(config, "USER_ANALYSIS_DAYS", 14), progress=progress)
    if not msgs:
        await _status(f"Couldn't find recent messages from {member.mention} in channels I can read.")
        return
    _save_context(member.id, msgs)  # cache for fast follow-ups (no re-scrape)

    await _status(f"\U0001f4dd Gathered **{len(msgs)}** messages. Asking Gemini to review ...")
    rules = await _load_rules(interaction.client)
    text, err = await _call_gemini(_build_prompt(member, msgs, rules))
    if err:
        await _status(f"Analysis failed: {err}")
        return

    channel = interaction.client.get_channel(config.USER_ANALYSIS_CHANNEL_ID)
    if channel is None:
        try:
            channel = await interaction.client.fetch_channel(config.USER_ANALYSIS_CHANNEL_ID)
        except Exception:
            await _status("Couldn't reach the report channel.")
            return

    try:
        await channel.send(view=_build_view(member, interaction.user, msgs, text))
    except Exception:
        log.error("failed to post user analysis", exc_info=True)
        await _status("Generated the report but couldn't post it.")
        return
    await _status(f"✅ Analysis of {member.mention} posted to <#{config.USER_ANALYSIS_CHANNEL_ID}>.")
