"""Unified detection, alerting and review framework.

One place to answer "this looks like cheating - what now?", so every detector reports the
same way and every alert offers the same actions. Detectors call `flag()` with what they
saw; this module renders it, posts it to the review channel, and owns the buttons that act
on it.

Three things are deliberately centralised:

Rendering. An alert is only useful if a human can judge it in a few seconds, so every one
carries the same three blocks - who, what tripped, and the surrounding evidence - rather
than each detector inventing a layout. The subject block always shows account age and join
date, because "is this a throwaway" is the first question asked of every single flag.

Acting. The buttons (dismiss, tax half, confiscate, restrict) move real money, so they live
here once and are tested once, instead of being redefined inline per detector.

Never blocking. Detection runs on the interaction path - a slow database scan or a failing
Discord call must not delay, or break, the thing the user was actually doing. `flag()`
schedules the work and returns immediately, and every detector entry point swallows its own
exceptions: a broken detector must not take a game down with it.
"""

import asyncio
import json
import logging
import time

import discord

import config
from database import DatabaseManager
from lib.economy.economy_manager import add_bb, get_bb, remove_bb

log = logging.getLogger(__name__)

# Detection kinds. The string is stored in the event log and shown in the alert title, so
# changing one loses continuity with history already recorded under the old name.
WORDLE_FAST_SOLVE = "wordle_fast_solve"
WORDLE_ONE_GUESS_STREAK = "wordle_one_guess_streak"
WORDLE_DUMMY_GUESS = "wordle_dummy_guess"
CROSSWORD_FAST_SOLVE = "crossword_fast_solve"
CROSSWORD_SEQUENCE_COPY = "crossword_sequence_copy"
ALT_CO_OCCURRENCE = "alt_co_occurrence"
WAGER_WASHING = "wager_washing"
RAPID_RECYCLING = "rapid_recycling"
FUNNEL_POOLING = "funnel_pooling"

_TITLES = {
    WORDLE_FAST_SOLVE: "Anti-Cheat: Wordle Fast Solve",
    WORDLE_ONE_GUESS_STREAK: "Anti-Cheat: Improbable First-Try Rate",
    WORDLE_DUMMY_GUESS: "Anti-Cheat: Dummy Guess Evasion",
    CROSSWORD_FAST_SOLVE: "Anti-Cheat: Crossword Fast Solve",
    CROSSWORD_SEQUENCE_COPY: "Anti-Cheat: Crossword Sequence Copying",
    ALT_CO_OCCURRENCE: "Anti-Alt: Lockstep Daily Activity",
    WAGER_WASHING: "Anti-Laundering: Wager Washing",
    RAPID_RECYCLING: "Anti-Laundering: Rapid Fund Recycling",
    FUNNEL_POOLING: "Anti-Laundering: Multi-Sender Pool",
}


def _cfg(name, default):
    """Thresholds live in config so they can be tuned without a deploy of this file."""
    return getattr(config, name, default)


# The bot, registered once at startup. Detectors sit at the bottom of the stack - the money
# ones hang off economy_manager, which knows nothing about Discord - so threading a client
# down to them would mean changing every caller in between for the sake of an alert. One
# reference, set on ready, keeps the detection concern out of those signatures entirely.
_client = None


def set_client(client) -> None:
    global _client
    _client = client


def _resolve(client):
    return client or _client


# ---------------------------------------------------------------------------
# Event store
# ---------------------------------------------------------------------------
def record_event(user_id, kind: str, meta: dict = None, ts: int = None) -> None:
    """Log one observation. Detectors write here on every relevant action, not only on a
    breach, because most rules are "N of these within a window" and cannot be answered
    without the ones that looked innocent at the time."""
    try:
        DatabaseManager.execute(
            "INSERT INTO detection_events (ts, user_id, kind, meta) VALUES (?, ?, ?, ?)",
            (int(ts if ts is not None else time.time()), str(user_id), kind,
             json.dumps(meta or {})),
        )
    except Exception:
        log.exception("could not record detection event %s for %s", kind, user_id)


def recent_events(user_id, kind: str, within_seconds: int, now: int = None) -> list:
    """[(ts, meta), ...] newest first, for one user and kind inside a rolling window."""
    now = int(now if now is not None else time.time())
    rows = DatabaseManager.fetch_all(
        "SELECT ts, meta FROM detection_events WHERE user_id = ? AND kind = ? AND ts >= ? "
        "ORDER BY ts DESC",
        (str(user_id), kind, now - int(within_seconds)),
    ) or []
    out = []
    for ts, meta in rows:
        try:
            out.append((int(ts), json.loads(meta) if meta else {}))
        except (TypeError, ValueError):
            out.append((int(ts), {}))
    return out


def events_in_window(kind: str, within_seconds: int, now: int = None) -> list:
    """[(ts, user_id, meta), ...] across all users - the cross-account rules need everyone's,
    not one person's."""
    now = int(now if now is not None else time.time())
    rows = DatabaseManager.fetch_all(
        "SELECT ts, user_id, meta FROM detection_events WHERE kind = ? AND ts >= ? "
        "ORDER BY ts ASC",
        (kind, now - int(within_seconds)),
    ) or []
    out = []
    for ts, uid, meta in rows:
        try:
            out.append((int(ts), str(uid), json.loads(meta) if meta else {}))
        except (TypeError, ValueError):
            out.append((int(ts), str(uid), {}))
    return out


def already_alerted(user_id, kind: str, within_seconds: int, now: int = None) -> bool:
    """Has this user already been reported for this, recently?

    Without this a single bad actor generates one alert per action and the review channel
    becomes unreadable, which is the same as having no alerts at all."""
    now = int(now if now is not None else time.time())
    row = DatabaseManager.fetch_one(
        "SELECT 1 FROM detection_alerts WHERE user_id = ? AND kind = ? AND ts >= ? LIMIT 1",
        (str(user_id), kind, now - int(within_seconds)),
    )
    return row is not None


def _mark_alerted(user_id, kind: str, ts: int = None) -> None:
    try:
        DatabaseManager.execute(
            "INSERT INTO detection_alerts (ts, user_id, kind) VALUES (?, ?, ?)",
            (int(ts if ts is not None else time.time()), str(user_id), kind),
        )
    except Exception:
        log.exception("could not mark alert for %s/%s", user_id, kind)


# ---------------------------------------------------------------------------
# Flags
# ---------------------------------------------------------------------------
def set_flag(user_id, flag: str, by_id=None, note: str = "") -> None:
    DatabaseManager.execute(
        "INSERT INTO detection_flags (user_id, flag, ts, by_id, note) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(user_id, flag) DO UPDATE SET ts = excluded.ts, by_id = excluded.by_id, "
        "note = excluded.note",
        (str(user_id), flag, int(time.time()), str(by_id) if by_id else None, note),
    )


def clear_flag(user_id, flag: str) -> None:
    DatabaseManager.execute(
        "DELETE FROM detection_flags WHERE user_id = ? AND flag = ?", (str(user_id), flag)
    )


def is_flagged(user_id, flag: str = "flagged_alt") -> bool:
    row = DatabaseManager.fetch_one(
        "SELECT 1 FROM detection_flags WHERE user_id = ? AND flag = ? LIMIT 1",
        (str(user_id), flag),
    )
    return row is not None


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def _message_count(user_id) -> str:
    """Chat volume, from the XP leaderboard. Shown because a farmed alt is usually rich and
    silent, and that contrast is the tell a reviewer looks for first."""
    try:
        from lib.core.file_operations import load_json_file
        data = load_json_file(config.XP_FILE) or {}
        entry = data.get(str(user_id))
        if isinstance(entry, dict):
            for key in ("messages", "message_count", "count", "xp"):
                if key in entry:
                    return f"{int(entry[key]):,}"
        elif isinstance(entry, (int, float)):
            return f"{int(entry):,}"
    except Exception:
        log.debug("could not read message count for %s", user_id, exc_info=True)
    return "unknown"


def _subject_block(client, user_id) -> str:
    """Who this is, in the terms a reviewer judges by: how old the account is, how long it
    has been here, and whether it ever talks."""
    uid = int(user_id)
    user = client.get_user(uid) if client else None
    name = discord.utils.escape_markdown(getattr(user, "display_name", None) or "unknown")

    lines = [f"<@{uid}> **{name}** · `{uid}`"]
    created = getattr(user, "created_at", None)
    if created:
        lines.append(f"• Account created <t:{int(created.timestamp())}:R>")
    member = None
    for guild in (getattr(client, "guilds", None) or []):
        member = guild.get_member(uid)
        if member:
            break
    joined = getattr(member, "joined_at", None)
    if joined:
        lines.append(f"• Joined server <t:{int(joined.timestamp())}:R>")
    lines.append(f"• Messages sent: **{_message_count(uid)}** · Balance: **{get_bb(uid):,}** UKP")
    return "\n".join(lines)


def _flow_between(a, b) -> dict:
    """Every /pay between two accounts, split by direction.

    Co-occurrence proves two accounts move at the same time; it says nothing about
    whether either of them profits. For a /pay flag that is the whole question, so the
    card answers it directly rather than leaving a reviewer to go and query the ledger.
    """
    out = {}
    try:
        for key, payer, recipient in (("a_to_b", a, b), ("b_to_a", b, a)):
            row = DatabaseManager.fetch_one(
                "SELECT COUNT(*), COALESCE(SUM(amount), 0), MIN(timestamp), MAX(timestamp) "
                "FROM pay_transfers WHERE payer_id = ? AND recipient_id = ?",
                (str(payer), str(recipient)),
            ) or (0, 0, None, None)
            out[key] = {
                "count": int(row[0] or 0),
                "total": int(row[1] or 0),
                "first": row[2],
                "last": row[3],
            }
    except Exception:
        log.exception("could not read pay flow between %s and %s", a, b)
        return {}
    return out


def _flow_block(a, b) -> str:
    """Render the two-way /pay history, or say plainly that there is none."""
    flow = _flow_between(a, b)
    if not flow:
        return ""
    ab, ba = flow.get("a_to_b", {}), flow.get("b_to_a", {})
    if not ab.get("count") and not ba.get("count"):
        return "• No /pay has ever moved between these two accounts."

    lines = []
    for src, dst, f in ((a, b, ab), (b, a, ba)):
        if f.get("count"):
            when = f" · last <t:{int(f['last'])}:R>" if f.get("last") else ""
            lines.append(
                f"• <@{int(src)}> → <@{int(dst)}>: **{f['total']:,} UKP** "
                f"over {f['count']} transfer{'s' if f['count'] != 1 else ''}{when}"
            )
    net = ab.get("total", 0) - ba.get("total", 0)
    if net:
        winner, loser = (b, a) if net > 0 else (a, b)
        lines.append(f"• **Net: {abs(net):,} UKP** from <@{int(loser)}> to <@{int(winner)}>")
    else:
        lines.append("• **Net: nothing** - the flow cancels out both ways")
    return "\n".join(lines)


def build_embed(client, kind: str, subjects, triggers: dict, context: str = "") -> discord.Embed:
    """The standard alert card: who, what tripped, and the evidence behind it."""
    subjects = [subjects] if isinstance(subjects, (int, str)) else list(subjects)
    embed = discord.Embed(
        title=f"🚨 {_TITLES.get(kind, kind)}",
        colour=discord.Colour.red(),
        timestamp=discord.utils.utcnow(),
    )
    for n, uid in enumerate(subjects, start=1):
        label = "Subject" if len(subjects) == 1 else f"Subject {n}"
        embed.add_field(name=label, value=_subject_block(client, uid), inline=False)
    if triggers:
        embed.add_field(
            name="Trigger",
            value="\n".join(f"• {k}: **{v}**" for k, v in triggers.items())[:1024],
            inline=False,
        )
    if len(subjects) == 2:
        flow = _flow_block(subjects[0], subjects[1])
        if flow:
            embed.add_field(name="Money between them (/pay)", value=flow[:1024], inline=False)
    if context:
        embed.add_field(name="Context", value=context[:1024], inline=False)
    embed.set_footer(text=f"detection · {kind}")
    return embed


# ---------------------------------------------------------------------------
# Review actions
# ---------------------------------------------------------------------------
class ReviewView(discord.ui.View):
    """The four decisions a reviewer can make, on every alert.

    `amount` is the sum the detection is about - the payout to claw back, or the funds
    moved. When it is 0 the money buttons are hidden rather than shown doing nothing, so a
    reviewer is never offered an action that cannot apply.
    """

    def __init__(self, kind: str, subject_id, amount: int = 0, funder_id=None):
        super().__init__(timeout=None)     # reviews outlive any sensible timeout
        self.kind = kind
        self.subject_id = int(subject_id)
        self.amount = max(0, int(amount))
        self.funder_id = int(funder_id) if funder_id else None
        if not self.amount:
            self.remove_item(self.tax_half)
            self.remove_item(self.confiscate)

    async def _finish(self, interaction, note: str) -> None:
        for child in self.children:
            child.disabled = True
        try:
            await interaction.message.edit(view=self)
        except Exception:
            log.debug("could not disable review buttons", exc_info=True)
        await interaction.response.send_message(
            f"{note} — by {interaction.user.mention}", allowed_mentions=discord.AllowedMentions.none()
        )
        self.stop()

    @discord.ui.button(label="Allow / Dismiss", style=discord.ButtonStyle.success, emoji="✅")
    async def dismiss(self, interaction: discord.Interaction, button: discord.ui.Button):
        clear_flag(self.subject_id, "flagged_alt")
        await self._finish(interaction, f"✅ Dismissed — <@{self.subject_id}> cleared")

    @discord.ui.button(label="Tax 50%", style=discord.ButtonStyle.primary, emoji="📉")
    async def tax_half(self, interaction: discord.Interaction, button: discord.ui.Button):
        take = self.amount // 2
        ok = remove_bb(self.subject_id, take, reason=f"detection {self.kind}: 50% penalty")
        if not ok:
            return await interaction.response.send_message(
                "❌ Balance no longer covers it.", ephemeral=True)
        await self._finish(interaction, f"📉 Taxed 50% — **{take:,} UKP** from <@{self.subject_id}>")

    @discord.ui.button(label="Confiscate 100%", style=discord.ButtonStyle.secondary, emoji="💸")
    async def confiscate(self, interaction: discord.Interaction, button: discord.ui.Button):
        ok = remove_bb(self.subject_id, self.amount, reason=f"detection {self.kind}: confiscated")
        if not ok:
            return await interaction.response.send_message(
                "❌ Balance no longer covers it.", ephemeral=True)
        # Washing moves money between two accounts, so returning it to the sender undoes the
        # transfer rather than just fining the receiver - otherwise confiscation quietly
        # destroys UKP that a legitimate pair would want back.
        if self.funder_id:
            add_bb(self.funder_id, self.amount,
                   reason=f"detection {self.kind}: returned", taxable=False)
            note = (f"💸 Confiscated **{self.amount:,} UKP** from <@{self.subject_id}>, "
                    f"returned to <@{self.funder_id}>")
        else:
            note = f"💸 Confiscated **{self.amount:,} UKP** from <@{self.subject_id}>"
        await self._finish(interaction, note)

    @discord.ui.button(label="Flag / Restrict", style=discord.ButtonStyle.danger, emoji="🚫")
    async def restrict(self, interaction: discord.Interaction, button: discord.ui.Button):
        from lib.core import restrictions as R
        tier = R.apply(self.subject_id, R.DEFAULT_TIER, by_id=interaction.user.id, note=self.kind)
        await self._finish(
            interaction,
            f"🚫 Restricted — <@{self.subject_id}> on **{R.tier_label(tier)}**, "
            f"blocked from {R.summary(tier)}. Change the tier in /flags.")


# ---------------------------------------------------------------------------
# Review panel
# ---------------------------------------------------------------------------
def flagged_members() -> list:
    """[(user_id, flag, ts, note), ...], most recently flagged first."""
    return DatabaseManager.fetch_all(
        "SELECT user_id, flag, ts, note FROM detection_flags ORDER BY ts DESC") or []


def _panel_text(client, rows) -> str:
    """The list, with each member's tier and what it actually stops them doing.

    The old panel said "restricted from economy commands" for everyone, which was wrong
    for every tier including the only one that existed. Each line now names the tier and
    the tier names its own blocks, so the panel cannot drift from what the gate enforces.
    """
    from lib.core import restrictions as R
    if not rows:
        return ("**Economy restrictions**\nNobody is restricted.\n"
                "-# Use **Restrict a member** below to add one.")
    lines = ["**Economy restrictions**", f"-# {len(rows)} member(s) restricted", ""]
    for uid, tier, ts, note in rows[:25]:
        user = client.get_user(int(uid)) if client else None
        name = getattr(user, "display_name", None) or uid
        why = f" · {note}" if note else ""
        lines.append(
            f"🚫 **{discord.utils.escape_markdown(str(name))}** — "
            f"`{R.tier_label(tier)}` · <t:{int(ts)}:R>{why}\n"
            f"-# blocked from {R.summary(tier)}"
        )
    if len(rows) > 25:
        lines.append(f"-# …and {len(rows) - 25} more")
    return "\n".join(lines)


def _history_text(client, uid=None) -> str:
    from lib.core import restrictions as R
    rows = R.history(uid, limit=15)
    if not rows:
        return "**Restriction log**\nNothing recorded yet."
    head = "**Restriction log**" if uid is None else f"**Restriction log — <@{int(uid)}>**"
    lines = [head]
    for ts, ruid, action, tier, by_id, note in rows:
        who = f"<@{int(by_id)}>" if by_id else "the system"
        label = R.tier_label(tier) if tier else "—"
        subject = "" if uid is not None else f" <@{int(ruid)}>"
        extra = f" · {note}" if note else ""
        icon = "🚫" if action == "applied" else "✅"
        lines.append(f"{icon} **{action}**{subject} `{label}` by {who} · <t:{int(ts)}:R>{extra}")
    return "\n".join(lines)


class FlagsPanel(discord.ui.View):
    """Review, change and record economy restrictions without retyping anything.

    Rebuilt from the database on every action rather than held in memory, so two reviewers
    working at once never act on a stale list - the dropdown is the current state, not a
    snapshot from when the panel opened.
    """

    def __init__(self, client, viewer_id):
        super().__init__(timeout=600)
        self.client = client
        self.viewer_id = int(viewer_id)
        self.selected = None          # member currently being worked on
        self._rebuild()

    # --- construction -------------------------------------------------------------
    def _rebuild(self) -> None:
        from lib.core import restrictions as R
        self.clear_items()
        rows = R.restricted_members()

        if rows:
            options = []
            for uid, tier, ts, note in rows[:25]:
                user = self.client.get_user(int(uid)) if self.client else None
                label = str(getattr(user, "display_name", None) or uid)[:100]
                options.append(discord.SelectOption(
                    label=label,
                    value=str(uid),
                    description=f"{R.tier_label(tier)} · {(note or 'flagged')}"[:100],
                    default=(self.selected is not None and str(uid) == str(self.selected)),
                ))
            pick = discord.ui.Select(placeholder="Choose a restricted member…", options=options)
            pick.callback = self._on_pick
            self.add_item(pick)

        # Adding a restriction needs a member who is by definition not in the list above,
        # so it gets its own picker rather than sharing one.
        add = discord.ui.UserSelect(placeholder="Restrict a member…", max_values=1)
        add.callback = self._on_add
        self.add_item(add)

        if self.selected is not None:
            tier_pick = discord.ui.Select(
                placeholder="Change tier…",
                options=[
                    discord.SelectOption(
                        label=meta["label"], value=key, description=meta["summary"][:100],
                        default=(key == R.tier_of(self.selected)),
                    )
                    for key, meta in sorted(R.TIERS.items(), key=lambda kv: kv[1]["rank"])
                ],
            )
            tier_pick.callback = self._on_tier
            self.add_item(tier_pick)

            lift = discord.ui.Button(label="Lift restriction", style=discord.ButtonStyle.success,
                                     emoji="✅")
            lift.callback = self._on_lift
            self.add_item(lift)
            detail = discord.ui.Button(label="Details", style=discord.ButtonStyle.secondary,
                                       emoji="🔍")
            detail.callback = self._on_details
            self.add_item(detail)

        log_btn = discord.ui.Button(label="Restriction log", style=discord.ButtonStyle.secondary,
                                    emoji="📜")
        log_btn.callback = self._on_log
        self.add_item(log_btn)

    async def _guard(self, interaction) -> bool:
        if interaction.user.id != self.viewer_id:
            await interaction.response.send_message("That isn't for you.", ephemeral=True)
            return False
        return True

    async def _refresh(self, interaction, note: str = "") -> None:
        from lib.core import restrictions as R
        self._rebuild()
        body = _panel_text(self.client, R.restricted_members())
        await interaction.response.edit_message(
            content=f"{note}\n\n{body}" if note else body,
            view=self,
            allowed_mentions=discord.AllowedMentions.none())

    # --- callbacks ----------------------------------------------------------------
    async def _on_pick(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        self.selected = interaction.data["values"][0]
        await self._refresh(interaction)

    async def _on_add(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        from lib.core import restrictions as R
        uid = interaction.data["values"][0]
        tier = R.apply(uid, R.DEFAULT_TIER, by_id=interaction.user.id, note="added by staff")
        self.selected = str(uid)
        log.info("restriction %s applied to %s by %s", tier, uid, interaction.user.id)
        await self._refresh(
            interaction,
            f"🚫 <@{int(uid)}> restricted at **{R.tier_label(tier)}** — "
            f"blocked from {R.summary(tier)}. Change the tier below if that is too light.")

    async def _on_tier(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        from lib.core import restrictions as R
        tier = interaction.data["values"][0]
        R.apply(self.selected, tier, by_id=interaction.user.id, note="tier changed by staff")
        log.info("restriction on %s set to %s by %s", self.selected, tier, interaction.user.id)
        await self._refresh(
            interaction,
            f"🚫 <@{int(self.selected)}> moved to **{R.tier_label(tier)}** — "
            f"now blocked from {R.summary(tier)}.")

    async def _on_lift(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        from lib.core import restrictions as R
        uid = self.selected
        R.lift(uid, by_id=interaction.user.id, note="lifted by staff")
        self.selected = None
        log.info("restriction on %s lifted by %s", uid, interaction.user.id)
        await self._refresh(interaction, f"✅ Lifted — <@{int(uid)}> can use everything again.")

    async def _on_details(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        from lib.core import restrictions as R
        uid = int(self.selected)
        tier = R.tier_of(uid)
        parts = [
            _subject_block(self.client, uid),
            "",
            f"**Tier** `{R.tier_label(tier)}` · blocked from {R.summary(tier)}",
            f"-# {R.footnote(tier)}" if R.footnote(tier) else "",
            "",
            _history_text(self.client, uid),
        ]
        await interaction.response.send_message(
            "\n".join(x for x in parts if x != ""), ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none())

    async def _on_log(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        await interaction.response.send_message(
            _history_text(self.client), ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none())


def build_flags_panel(client, viewer_id):
    """(content, view) for the /flags panel."""
    from lib.core import restrictions as R
    return _panel_text(client, R.restricted_members()), FlagsPanel(client, viewer_id)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
async def _post(client, kind, subjects, triggers, context, amount, funder_id) -> None:
    channel = client.get_channel(_cfg("DETECTION_ALERT_CHANNEL", config.CHANNELS.BOT_WORKSHOP))
    if not channel:
        log.warning("detection alert channel unavailable; %s for %s not posted", kind, subjects)
        return
    subject_list = [subjects] if isinstance(subjects, (int, str)) else list(subjects)
    await channel.send(
        content=f"<@{_cfg('DETECTION_ALERT_PING', config.USERS.OGGERS)}>",
        embed=build_embed(client, kind, subject_list, triggers, context),
        view=ReviewView(kind, subject_list[0], amount=amount, funder_id=funder_id),
        allowed_mentions=discord.AllowedMentions(users=True),
    )


def note_daily_command(user_id, command: str, client=None) -> None:
    """Record a daily-command use and look for an account moving in lockstep with it.

    One shared entry point rather than a check per command, so adding a new daily to the
    watch list is one call rather than a new rule."""
    try:
        from lib.core import detection_rules as R
        record_event(user_id, ALT_CO_OCCURRENCE, {"command": command})
        partner, triggers = R.co_occurrence_findings(user_id)
        if partner:
            flag(client, ALT_CO_OCCURRENCE, [user_id, partner], triggers,
                 context=(
                     f"Their daily commands keep landing together (latest: /{command}). "
                     "This is a timing signal only - it does not on its own mean either "
                     "account gained anything. The /pay history above is what shows "
                     "whether value actually moved, and in which direction."
                 ))
    except Exception:
        log.exception("co-occurrence check failed for %s", user_id)


def flag(client, kind: str, subjects, triggers: dict, context: str = "",
         amount: int = 0, funder_id=None, cooldown: int = None) -> None:
    """Report a detection. Returns immediately - the posting happens on its own task.

    Called from interaction handlers, so it must never raise and never wait: a detector
    that breaks, or a Discord API that hangs, cannot be allowed to take down the game the
    player was in the middle of.
    """
    if not _cfg("DETECTION_ENABLED", True):
        return
    client = _resolve(client)
    if client is None:
        log.warning("no client registered; %s alert dropped", kind)
        return
    try:
        subject_list = [subjects] if isinstance(subjects, (int, str)) else list(subjects)
        primary = subject_list[0]
        window = cooldown if cooldown is not None else _cfg("DETECTION_ALERT_COOLDOWN", 6 * 3600)
        if already_alerted(primary, kind, window):
            return
        _mark_alerted(primary, kind)
        asyncio.create_task(_post(client, kind, subject_list, triggers, context, amount, funder_id))
    except Exception:
        log.exception("detection flag failed for %s", kind)
