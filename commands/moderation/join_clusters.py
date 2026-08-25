"""Batch-creation detection: accounts that were made together and now arrive together.

join_watch only ever sees a member who *speaks*. A botnet that joins and sits there is
invisible to it, which is exactly what a staged raid looks like in the hour before it
starts - 22 accounts arriving inside an hour, none of them saying a word.

The tell those accounts cannot hide is their own ID. A Discord snowflake carries the
account's creation timestamp, so a batch registered by a script months ago still shows up
as a run of accounts created within minutes of each other. One member with a six-month-old
account is nothing; four of them created inside the same twenty minutes and walking in
together is a cluster.

What this does NOT do is act on its own. Account age has never been grounds for anything
here, and a coincidence is always possible - two friends who signed up together are a real
thing. It reports, staff decide, and the ban is a deliberate two-step press by a human.

The report is a single message in the police station that is edited in place as more of the
same cluster arrives, rather than a new alert per joiner: during a wave the useful artefact
is one growing list you can act on, not forty notifications you have to reconcile.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Iterable

import discord

from config import CHANNELS, GUILD_ID, JSON_DATA_DIR, ROLES

logger = logging.getLogger(__name__)

CLUSTER_STATE_FILE = os.path.join(JSON_DATA_DIR, "join_clusters.json")
APPEALS_FILE = os.path.join(JSON_DATA_DIR, "join_cluster_appeals.json")
STAFF_ROLE_IDS = {ROLES.MINISTER, ROLES.CABINET, ROLES.BORDER_FORCE}

# A run is chained on the gap between consecutive creations rather than measured from the
# first account, because a registration script pauses: a real batch looked like 01:49,
# 01:53, 02:03, 02:35, where every step is small but the last account is 46 minutes from
# the first. Anchoring on the first would have dropped it. MAX_SPREAD stops a chain of
# small gaps walking across a whole day.
MAX_GAP_SECONDS = 45 * 60          # break the run when consecutive creations are further apart
MAX_SPREAD_SECONDS = 4 * 60 * 60   # ...and cap how far one run may stretch in total
TIGHT_GAP_SECONDS = 5 * 60         # every step this close is scripted, not coincidence
MIN_CLUSTER_SIZE = 3
JOIN_WINDOW_SECONDS = 12 * 60 * 60  # only consider members who arrived in this window
MAX_LISTED = 40                     # ids shown on the card; the rest are counted
# Editing in place keeps one authoritative card, but a card that has scrolled away is a
# card nobody sees. Past this many messages the report is reposted at the bottom instead,
# and the old one is reduced to a pointer at the new one.
REPOST_AFTER_MESSAGES = 10


# --- detection ---------------------------------------------------------------------
def find_clusters(records: Iterable[dict[str, Any]], now: int | None = None) -> list[dict[str, Any]]:
    """Group recent joiners whose accounts were created within CREATION_WINDOW of each other.

    Pure and side-effect free so it can be tested without a guild. Returns clusters newest
    join first, each: {"created_from", "created_to", "spread", "tight", "members": [record]}.
    """
    current = int(time.time() if now is None else now)
    # One member per id, keeping their latest join. A member who leaves and rejoins writes
    # a record each time, and counting those as separate accounts turned one person into a
    # "cluster" with a 0s spread - the exact shape this flags as scripted. Deduping here as
    # well as at the source keeps the detector correct whatever the store contains.
    latest: dict[str, dict[str, Any]] = {}
    for r in records:
        if not isinstance(r, dict):
            continue
        joined = int(r.get("joined_at", 0) or 0)
        if current - joined > JOIN_WINDOW_SECONDS:
            continue
        uid = str(r.get("user_id", ""))
        if not uid:
            continue
        if uid not in latest or joined > int(latest[uid].get("joined_at", 0) or 0):
            latest[uid] = r
    recent = list(latest.values())
    if len(recent) < MIN_CLUSTER_SIZE:
        return []

    by_creation = sorted(recent, key=lambda r: int(r.get("account_created_at", 0) or 0))
    clusters: list[dict[str, Any]] = []
    run: list[dict[str, Any]] = []

    def _created(r: dict[str, Any]) -> int:
        return int(r.get("account_created_at", 0) or 0)

    def _flush(run: list[dict[str, Any]]) -> None:
        if len(run) < MIN_CLUSTER_SIZE:
            return
        first, last = _created(run[0]), _created(run[-1])
        gaps = [_created(b) - _created(a) for a, b in zip(run, run[1:])]
        clusters.append({
            "created_from": first,
            "created_to": last,
            "spread": last - first,
            "max_gap": max(gaps) if gaps else 0,
            "tight": bool(gaps) and max(gaps) <= TIGHT_GAP_SECONDS,
            "members": sorted(run, key=lambda r: int(r.get("joined_at", 0) or 0)),
        })

    for rec in by_creation:
        created = _created(rec)
        if run:
            gap = created - _created(run[-1])
            spread = created - _created(run[0])
            if gap > MAX_GAP_SECONDS or spread > MAX_SPREAD_SECONDS:
                _flush(run)
                run = []
        run.append(rec)
    _flush(run)

    clusters.sort(key=lambda c: max(int(m.get("joined_at", 0) or 0) for m in c["members"]),
                  reverse=True)
    return clusters


# --- live report state -------------------------------------------------------------
def _load_state() -> dict[str, Any]:
    try:
        with open(CLUSTER_STATE_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict[str, Any]) -> None:
    try:
        from lib.core.file_operations import atomic_write_json
        atomic_write_json(CLUSTER_STATE_FILE, state, indent=2)
    except Exception:
        logger.exception("could not persist join-cluster report state")


def _signature(clusters: list[dict[str, Any]]) -> str:
    """What the card currently shows, so an unchanged picture is not re-rendered."""
    return "|".join(
        f"{c['created_from']}:" + ",".join(sorted(m["user_id"] for m in c["members"]))
        for c in clusters
    )


def cluster_user_ids(clusters: list[dict[str, Any]]) -> list[str]:
    seen, out = set(), []
    for c in clusters:
        for m in c["members"]:
            if m["user_id"] not in seen:
                seen.add(m["user_id"])
                out.append(m["user_id"])
    return out


# --- rendering ---------------------------------------------------------------------
# The card is read by someone deciding whether to ban strangers, so it leads with the
# claim in plain words, ranks the batches by how mechanical they look, and gives each
# batch its own button. One "ban all" across five batches of differing quality forced an
# all-or-nothing call on evidence that is not all the same strength.
def _severity(cluster: dict[str, Any], now: int) -> tuple[int, str, str]:
    """(rank, label, why) - higher rank is more mechanical."""
    gap = int(cluster.get("max_gap", 0))
    age_days = max(0, (now - int(cluster["created_from"])) // 86400)
    if gap <= TIGHT_GAP_SECONDS:
        return (2, "LIKELY SCRIPTED",
                f"registered {_dur(gap)} apart - a person signing up cannot do that")
    if age_days <= 7:
        return (1, "WORTH A LOOK",
                f"registered {_dur(cluster['spread'])} apart and only {age_days}d old")
    return (0, "WORTH A LOOK",
            f"registered {_dur(cluster['spread'])} apart, {age_days}d ago")


def _dur(seconds: int) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"


def build_cluster_view(clusters: list[dict[str, Any]], banned: list[str] | None = None,
                       quarantined: list[str] | None = None, watching: list[str] | None = None,
                       dismissed_by: str | None = None, gone: list[str] | None = None,
                       now: int | None = None) -> discord.ui.LayoutView:
    banned = set(banned or [])
    quarantined = set(quarantined or [])
    watching = set(watching or [])
    gone = set(gone or [])
    current = int(time.time() if now is None else now)
    ranked = sorted(clusters, key=lambda c: (-_severity(c, current)[0], -len(c["members"])))
    live_ids = [u for u in cluster_user_ids(ranked) if u not in banned]

    view = discord.ui.LayoutView(timeout=None)
    if dismissed_by:
        accent = 0x2ECC71
    elif not live_ids:
        accent = 0x95A5A6
    else:
        accent = 0xE67E22
    card = discord.ui.Container(accent_colour=accent)

    card.add_item(discord.ui.TextDisplay(
        "## 🛰️ These accounts were made together\n"
        f"**{len(ranked)} batches** of accounts were registered on Discord minutes apart "
        f"from each other, and have now all joined here within {JOIN_WINDOW_SECONDS // 3600} "
        "hours. That is what an account farm looks like: someone registers a stack of "
        "accounts in one sitting, leaves them to age, then walks them in together.\n"
        f"-# {len(live_ids)} account(s) still in the server · strongest batch first"
    ))
    card.add_item(discord.ui.Separator())

    for c in ranked:
        members = c["members"]
        rank, label, why = _severity(c, current)
        remaining = [m for m in members if m["user_id"] not in banned]
        mark = "🔴" if rank == 2 else "🟠"
        head = (f"### {mark} {label} — {len(members)} accounts\n"
                f"Registered <t:{c['created_from']}:f>, {why}.")
        rows = []
        for m in members[:MAX_LISTED]:
            uid = m["user_id"]
            # The mention is the useful half: it renders as a clickable pill straight to
            # the profile. The stored name stays beside it because it is what they were
            # called when they joined, which survives both a rename and a leave - and a
            # mention for someone who has gone renders as an unresolved id. Mentions are
            # never allowed to ping; every send and edit passes AllowedMentions.none().
            name = discord.utils.escape_markdown(str(m.get("username", "?")))
            line = (f"<@{uid}> **{name}** · joined <t:{int(m.get('joined_at', 0))}:R>"
                    f" · `{uid}`")
            if uid in banned:
                line = f"~~{line}~~ 🔨"
            elif uid in gone:
                # Left on their own. Worth showing rather than hiding: it usually means
                # the batch is resolving itself and needs nothing from you.
                line = f"~~{line}~~ · 🚪 left"
            elif uid in quarantined:
                line += " · 🔒"
            elif uid in watching:
                line += " · 👁️"
            rows.append(line)
        if len(members) > MAX_LISTED:
            rows.append(f"-# …and {len(members) - MAX_LISTED} more")
        body = (head + "\n" + "\n".join(rows))[:1800]
        key = int(c["created_from"])
        if remaining and not dismissed_by:
            # Components V2 Section: the batch's own Ban sits inline against the batch it
            # acts on, so there is no ambiguity about which accounts a button refers to.
            card.add_item(discord.ui.Section(
                discord.ui.TextDisplay(body),
                accessory=BanClusterButton(key, len(remaining)),
            ))
            not_yet = len([m for m in remaining if m["user_id"] not in quarantined])
            if not_yet:
                card.add_item(discord.ui.ActionRow(QuarantineClusterButton(key, not_yet)))
        else:
            card.add_item(discord.ui.TextDisplay(body))
        card.add_item(discord.ui.Separator())

    if dismissed_by:
        card.add_item(discord.ui.TextDisplay(
            f"✅ **Marked as not a raid** by <@{dismissed_by}>. Nothing was actioned.\n"
            "-# A new report will be posted if more accounts from these batches arrive."))
    elif live_ids:
        card.add_item(discord.ui.ActionRow(
            MassBanButton(len(live_ids)),
            WatchAllButton(len([u for u in live_ids if u not in watching])),
            DismissButton(),
        ))
        card.add_item(discord.ui.TextDisplay(
            "-# 🔨 removes them · 🔒 restricts them but leaves them here · 👁️ screens their "
            "first messages if they ever speak · ✅ says this was nothing.\n"
            "-# Being made at the same time is evidence, not proof - people who signed up "
            "together look identical here. Nothing happens automatically."
        ))
    else:
        card.add_item(discord.ui.TextDisplay(
            f"✅ **All {len(banned)} account(s) on this report have been banned.**"))
    view.add_item(card)
    return view


# --- the destructive bit, deliberately two presses ---------------------------------
class MassBanButton(discord.ui.DynamicItem[discord.ui.Button],
                    template=r"joincluster:massban"):
    """Opens a confirmation. Never bans on the first press."""

    def __init__(self, count: int = 0):
        super().__init__(
            discord.ui.Button(
                label=f"Ban all {count}" if count else "Ban all listed",
                emoji="🔨",
                style=discord.ButtonStyle.danger,
                custom_id="joincluster:massban",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        if not _is_staff(interaction.user):
            await interaction.response.send_message(
                "Staff only.", ephemeral=True)
            return
        state = _load_state()
        ids = [str(x) for x in state.get("ids", [])]
        if not ids:
            await interaction.response.send_message(
                "That report has no accounts left to act on.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"### Ban {len(ids)} accounts?\n"
            "This bans every account listed on the report. It cannot be undone from here - "
            "unbanning is one at a time in Discord.\n"
            f"-# {', '.join(ids[:8])}{'…' if len(ids) > 8 else ''}",
            view=_ConfirmBan(ids),
            ephemeral=True,
        )


class BanClusterButton(discord.ui.DynamicItem[discord.ui.Button],
                       template=r"joincluster:ban:(?P<key>\d+)"):
    """Ban one batch. Same two-press rule as the ban-all; the key is the batch's
    creation timestamp, which is stable across edits of the report."""

    def __init__(self, key: int = 0, count: int = 0):
        self.key = int(key)
        super().__init__(
            discord.ui.Button(
                label=f"Ban these {count}" if count else "Ban this batch",
                emoji="🔨",
                style=discord.ButtonStyle.danger,
                custom_id=f"joincluster:ban:{self.key}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(int(match["key"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        if not _is_staff(interaction.user):
            await interaction.response.send_message("Staff only.", ephemeral=True)
            return
        state = _load_state()
        banned = set(state.get("banned", []))
        batch = next((c for c in state.get("clusters", [])
                      if int(c.get("created_from", 0)) == self.key), None)
        ids = [m["user_id"] for m in (batch or {}).get("members", [])
               if m["user_id"] not in banned]
        if not ids:
            await interaction.response.send_message(
                "That batch has already been dealt with.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"### Ban this batch of {len(ids)}?\n"
            "Only the accounts in this batch. This cannot be undone from here.\n"
            f"-# {', '.join(ids[:8])}{'…' if len(ids) > 8 else ''}",
            view=_ConfirmBan(ids), ephemeral=True)


class QuarantineClusterButton(discord.ui.DynamicItem[discord.ui.Button],
                              template=r"joincluster:quar:(?P<key>\d+)"):
    """Restrict a batch without removing it. Reversible, so no confirmation step."""

    def __init__(self, key: int = 0, count: int = 0):
        self.key = int(key)
        super().__init__(
            discord.ui.Button(
                label=f"Quarantine {count}" if count else "Quarantine",
                emoji="🔒",
                style=discord.ButtonStyle.secondary,
                custom_id=f"joincluster:quar:{self.key}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(int(match["key"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        if not _is_staff(interaction.user):
            await interaction.response.send_message("Staff only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        state = _load_state()
        handled = set(state.get("banned", [])) | set(state.get("quarantined", []))
        batch = next((c for c in state.get("clusters", [])
                      if int(c.get("created_from", 0)) == self.key), None)
        ids = [m["user_id"] for m in (batch or {}).get("members", []) if m["user_id"] not in handled]
        if not ids:
            await interaction.followup.send("Nothing left in that batch.", ephemeral=True)
            return
        done, skipped = await quarantine_ids(interaction.guild, ids, interaction.user)
        state["quarantined"] = sorted(set(state.get("quarantined", [])) | set(done))
        _save_state(state)
        await _refresh_report(interaction.client)
        await interaction.followup.send(
            f"🔒 Quarantined {len(done)} account(s).{_skip_summary(skipped)}", ephemeral=True)


class WatchAllButton(discord.ui.DynamicItem[discord.ui.Button],
                     template=r"joincluster:watch"):
    """Screen these accounts if they ever speak, without arming the whole server."""

    def __init__(self, count: int = 0):
        super().__init__(
            discord.ui.Button(
                label=f"Watch {count}" if count else "Watch them",
                emoji="👁️",
                style=discord.ButtonStyle.secondary,
                custom_id="joincluster:watch",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        if not _is_staff(interaction.user):
            await interaction.response.send_message("Staff only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        state = _load_state()
        ids = [u for u in state.get("ids", []) if u not in set(state.get("banned", []))]
        done, skipped = await watch_ids(interaction.guild, ids)
        state["watching"] = sorted(set(state.get("watching", [])) | set(done))
        state["gone"] = sorted(set(state.get("gone", []))
                               | {u for u, why in skipped.items() if "left" in why})
        _save_state(state)
        await _refresh_report(interaction.client)
        note = (f"👁️ Now screening the first messages from {len(done)} account(s). "
                "They are not restricted - if they post anything flaggable the usual "
                "join-watch report fires.")
        await interaction.followup.send(note + _skip_summary(skipped), ephemeral=True)


class DismissButton(discord.ui.DynamicItem[discord.ui.Button],
                    template=r"joincluster:dismiss"):
    """Mark the batch as looked at and innocent, so the card stops asking."""

    def __init__(self):
        super().__init__(
            discord.ui.Button(
                label="Not a raid",
                emoji="✅",
                style=discord.ButtonStyle.success,
                custom_id="joincluster:dismiss",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        if not _is_staff(interaction.user):
            await interaction.response.send_message("Staff only.", ephemeral=True)
            return
        state = _load_state()
        state["dismissed_by"] = str(interaction.user.id)
        state["dismissed_at"] = int(time.time())
        # Remember which accounts were cleared, not just that a dismissal happened, or the
        # next unrelated joiner rebuilds the same batch and asks again.
        state["dismissed_ids"] = sorted(
            set(state.get("dismissed_ids", [])) | set(state.get("ids", [])))
        _save_state(state)
        await interaction.response.edit_message(
            view=build_cluster_view(state.get("clusters", []),
                                    banned=state.get("banned", []),
                                    quarantined=state.get("quarantined", []),
                                    dismissed_by=state["dismissed_by"]),
            allowed_mentions=discord.AllowedMentions.none())


class _ConfirmBan(discord.ui.View):
    def __init__(self, ids: list[str]):
        super().__init__(timeout=120)
        self.ids = ids

    @discord.ui.button(label="Yes, ban them", style=discord.ButtonStyle.danger, emoji="🔨")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _is_staff(interaction.user):
            await interaction.response.send_message("Staff only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        banned, failed = await ban_ids(interaction.guild, self.ids, interaction.user)
        logger.warning("join-cluster mass ban by %s: %s banned, %s failed",
                       interaction.user.id, len(banned), len(failed))
        state = _load_state()
        state["banned"] = sorted(set(state.get("banned", [])) | set(banned))
        _save_state(state)
        await _refresh_report(interaction.client, banned=state["banned"])
        note = f"🔨 Banned {len(banned)} account(s)."
        if failed:
            note += f"\nCould not ban {len(failed)}: {', '.join(failed[:6])}"
        await interaction.followup.send(note, ephemeral=True)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Cancelled - nobody was banned.",
                                                view=None)
        self.stop()


def _is_staff(user: Any) -> bool:
    return any(getattr(r, "id", None) in STAFF_ROLE_IDS
               for r in getattr(user, "roles", []) or [])


async def quarantine_ids(guild: Any, ids: list[str], actor: Any
                         ) -> tuple[list[str], dict[str, str]]:
    """Give the quarantine role instead of banning. Reversible, and the right first move
    when the evidence is a coincidence away from being innocent.

    Returns (quarantined, {id: why not}) for the same reason as watch_ids: an account that
    has already left is not a failure worth chasing.
    """
    from commands.moderation.anti_raid import QUARANTINE_ROLE_ID, mark_join_quarantined
    role = guild.get_role(QUARANTINE_ROLE_ID)
    if role is None:
        return [], {uid: "quarantine role is missing" for uid in ids}
    done, skipped = [], {}
    reason = f"Batch-created account cluster · quarantined by {getattr(actor, 'id', '?')}"
    for uid in ids:
        try:
            member = guild.get_member(int(uid))
            if member is None:
                member = await guild.fetch_member(int(uid))
        except Exception:
            skipped[uid] = "already left the server"
            continue
        try:
            await member.add_roles(role, reason=reason[:500])
            try:
                mark_join_quarantined(int(uid))
            except Exception:
                logger.debug("could not mark %s quarantined in join history", uid)
            done.append(uid)
        except Exception:
            logger.exception("join-cluster quarantine failed for %s", uid)
            skipped[uid] = "could not add the role"
    return done, skipped


async def watch_ids(guild: Any, ids: list[str]) -> tuple[list[str], dict[str, str]]:
    """Screen these accounts' first messages, without arming the watch server-wide.

    Returns (watched, {id: why not}). The reason matters: most of the time a watch fails
    because the account has already left, which means the batch is resolving itself - and
    reporting that as a bare count made a correct outcome look like a broken button.
    """
    from commands.moderation.join_watch import watch_member
    done, skipped = [], {}
    for uid in ids:
        try:
            member = guild.get_member(int(uid))
            if member is None:
                member = await guild.fetch_member(int(uid))
        except Exception:
            skipped[uid] = "already left the server"
            continue
        try:
            if watch_member(member):
                done.append(uid)
            else:
                skipped[uid] = "exempt (bot or staff)"
        except Exception:
            logger.exception("join-cluster watch failed for %s", uid)
            skipped[uid] = "error"
    return done, skipped


async def ban_ids(guild: Any, ids: list[str], actor: Any) -> tuple[list[str], list[str]]:
    """Ban each id, reporting rather than raising. One failure must not stop the rest."""
    banned, failed = [], []
    reason = f"Batch-created account cluster · authorised by {getattr(actor, 'id', '?')}"
    for uid in ids:
        # Tell them first. Once the ban lands we no longer share a guild and Discord will
        # refuse the DM, so a notice sent afterwards silently goes nowhere. A closed DM is
        # not a reason to skip the ban.
        member = guild.get_member(int(uid))
        if member is not None:
            await send_ban_appeal_dm(member)
        try:
            await guild.ban(discord.Object(id=int(uid)), reason=reason[:500],
                            delete_message_seconds=0)
            banned.append(uid)
        except Exception:
            logger.exception("join-cluster ban failed for %s", uid)
            failed.append(uid)
    return banned, failed


async def send_ban_appeal_dm(member: Any, body: str | None = None) -> bool:
    """Tell someone why they are being banned and give them the appeal button.

    Call this BEFORE the ban lands. Once it does we no longer share a guild and Discord
    refuses the DM, so a notice sent afterwards goes nowhere at all. Closed DMs are not a
    reason to hold off the ban, so this reports rather than raises.
    """
    try:
        await member.send(view=_ban_dm_view(int(member.id), body),
                          allowed_mentions=discord.AllowedMentions.none())
        return True
    except Exception:
        logger.debug("could not DM %s before banning (DMs closed?)", member.id, exc_info=True)
        return False


# --- appeals -----------------------------------------------------------------------
# Clustering is evidence, not proof, and this ban is issued on a pattern rather than on
# anything the person did. So everyone banned by it gets told why and given a way to say
# it was wrong - the one case the detector cannot rule out is the innocent one.
#
# The DM has to be sent before the ban lands: once they are banned we no longer share a
# guild and Discord will not deliver it. The button is a dynamic item whose custom_id
# carries the user id, so it keeps working after any number of restarts with no state
# held in memory.
def _load_appeals() -> dict[str, Any]:
    try:
        with open(APPEALS_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_appeals(data: dict[str, Any]) -> None:
    try:
        from lib.core.file_operations import atomic_write_json
        atomic_write_json(APPEALS_FILE, data, indent=2)
    except Exception:
        logger.exception("could not persist ban appeals")


CLUSTER_BAN_TEXT = (
    "### You have been removed from UK Place\n"
    "Your account was flagged as part of an automated join pattern detection.\n\n"
    "If you believe this was an error, you can submit an appeal below.")


def _ban_dm_view(user_id: int, body: str | None = None) -> discord.ui.LayoutView:
    """The appeal DM. `body` says why, so other automated bans can reuse the appeal route
    without telling the person something that is not true of their case."""
    view = discord.ui.LayoutView(timeout=None)
    card = discord.ui.Container(accent_colour=0xE74C3C)
    card.add_item(discord.ui.TextDisplay(body or CLUSTER_BAN_TEXT))
    card.add_item(discord.ui.Separator())
    card.add_item(discord.ui.ActionRow(AppealButton(user_id)))
    card.add_item(discord.ui.TextDisplay(
        "-# There's no time limit on this. Staff will see your appeal and decide."))
    view.add_item(card)
    return view


class AppealButton(discord.ui.DynamicItem[discord.ui.Button],
                   template=r"joincluster:appeal:(?P<uid>\d+)"):
    """Lives in a DM forever. The id is in the custom_id, so no restart can orphan it."""

    def __init__(self, user_id: int = 0):
        self.user_id = int(user_id)
        super().__init__(
            discord.ui.Button(
                label="Appeal this ban",
                emoji="📩",
                style=discord.ButtonStyle.primary,
                custom_id=f"joincluster:appeal:{self.user_id}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(int(match["uid"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        appeals = _load_appeals()
        existing = appeals.get(str(self.user_id))
        if existing and existing.get("status") == "pending":
            await interaction.response.send_message(
                "You've already sent an appeal - staff have it. You'll hear back here.",
                ephemeral=True)
            return
        if existing and existing.get("status") == "rejected":
            await interaction.response.send_message(
                "Your appeal was reviewed and turned down. Sending another won't change it.",
                ephemeral=True)
            return
        await interaction.response.send_modal(_AppealModal(self.user_id))


class _AppealModal(discord.ui.Modal, title="Appeal your ban"):
    reason = discord.ui.TextInput(
        label="Why should this be reversed?",
        style=discord.TextStyle.paragraph,
        placeholder="e.g. I made my account at the same time as a friend and we joined together.",
        max_length=900,
        required=True,
    )

    def __init__(self, user_id: int):
        super().__init__()
        self.user_id = int(user_id)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        text = str(self.reason.value or "").strip()
        appeals = _load_appeals()
        appeals[str(self.user_id)] = {
            "status": "pending",
            "text": text[:900],
            "at": int(time.time()),
            "name": str(interaction.user),
        }
        _save_appeals(appeals)
        posted = await _post_appeal(interaction.client, self.user_id, text, interaction.user)
        await interaction.response.send_message(
            "📩 Sent. Staff will review it and you'll get a reply here."
            if posted else
            "📩 Saved, but it couldn't be delivered to staff automatically - "
            "they can still find it.",
            ephemeral=True)


async def _post_appeal(client: Any, user_id: int, text: str, user: Any) -> bool:
    channel = await _get_channel(client, CHANNELS.POLICE_STATION)
    if channel is None:
        return False
    view = discord.ui.LayoutView(timeout=None)
    card = discord.ui.Container(accent_colour=0x3498DB)
    card.add_item(discord.ui.TextDisplay(
        f"## 📩 Ban appeal\n<@{user_id}> `{user_id}` · "
        f"{discord.utils.escape_markdown(str(user))}\n\n"
        f">>> {discord.utils.escape_markdown(text)[:900]}"))
    card.add_item(discord.ui.ActionRow(AppealAcceptButton(user_id), AppealRejectButton(user_id)))
    view.add_item(card)
    try:
        await channel.send(view=view, allowed_mentions=discord.AllowedMentions.none())
        return True
    except Exception:
        logger.exception("could not post ban appeal for %s", user_id)
        return False


async def _tell_appellant(client: Any, user_id: int, text: str) -> None:
    try:
        user = client.get_user(int(user_id)) or await client.fetch_user(int(user_id))
        await user.send(text)
    except Exception:
        logger.debug("could not reach appellant %s", user_id, exc_info=True)


class AppealAcceptButton(discord.ui.DynamicItem[discord.ui.Button],
                         template=r"joincluster:appealok:(?P<uid>\d+)"):
    def __init__(self, user_id: int = 0):
        self.user_id = int(user_id)
        super().__init__(discord.ui.Button(
            label="Unban", emoji="✅", style=discord.ButtonStyle.success,
            custom_id=f"joincluster:appealok:{self.user_id}"))

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(int(match["uid"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        if not _is_staff(interaction.user):
            await interaction.response.send_message("Staff only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await interaction.guild.unban(discord.Object(id=self.user_id),
                                          reason=f"Appeal accepted by {interaction.user.id}")
        except Exception:
            logger.exception("unban failed for %s", self.user_id)
            await interaction.followup.send("Couldn't unban them - check the audit log.",
                                            ephemeral=True)
            return
        appeals = _load_appeals()
        entry = appeals.setdefault(str(self.user_id), {})
        entry.update({"status": "accepted", "by": str(interaction.user.id)})
        _save_appeals(appeals)
        state = _load_state()
        state["banned"] = [u for u in state.get("banned", []) if str(u) != str(self.user_id)]
        _save_state(state)
        await _tell_appellant(
            interaction.client, self.user_id,
            "✅ Your appeal was accepted and your ban has been lifted. "
            "Sorry for the trouble - you're welcome back.")
        await interaction.message.edit(
            view=_appeal_closed_view(self.user_id, "accepted", interaction.user.id),
            allowed_mentions=discord.AllowedMentions.none())
        await interaction.followup.send("✅ Unbanned and told them.", ephemeral=True)


class AppealRejectButton(discord.ui.DynamicItem[discord.ui.Button],
                         template=r"joincluster:appealno:(?P<uid>\d+)"):
    def __init__(self, user_id: int = 0):
        self.user_id = int(user_id)
        super().__init__(discord.ui.Button(
            label="Reject", emoji="🚫", style=discord.ButtonStyle.danger,
            custom_id=f"joincluster:appealno:{self.user_id}"))

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(int(match["uid"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        if not _is_staff(interaction.user):
            await interaction.response.send_message("Staff only.", ephemeral=True)
            return
        appeals = _load_appeals()
        entry = appeals.setdefault(str(self.user_id), {})
        entry.update({"status": "rejected", "by": str(interaction.user.id)})
        _save_appeals(appeals)
        await _tell_appellant(
            interaction.client, self.user_id,
            "Your appeal was reviewed and the ban stands. You won't be able to appeal again.")
        await interaction.response.edit_message(
            view=_appeal_closed_view(self.user_id, "rejected", interaction.user.id),
            allowed_mentions=discord.AllowedMentions.none())


def _appeal_closed_view(user_id: int, outcome: str, by_id: int) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=None)
    icon, word = ("✅", "accepted - unbanned") if outcome == "accepted" else ("🚫", "rejected")
    card = discord.ui.Container(accent_colour=0x2ECC71 if outcome == "accepted" else 0x95A5A6)
    card.add_item(discord.ui.TextDisplay(
        f"{icon} **Appeal {word}** — <@{user_id}> `{user_id}`\n"
        f"-# Decided by <@{by_id}>. They have been told."))
    view.add_item(card)
    return view


def _skip_summary(skipped: dict[str, str]) -> str:
    """Group the reasons so a staff member reads one line, not a list of ids."""
    if not skipped:
        return ""
    counts: dict[str, int] = {}
    for why in skipped.values():
        counts[why] = counts.get(why, 0) + 1
    parts = [f"{n} {why}" for why, n in sorted(counts.items(), key=lambda kv: -kv[1])]
    return "\n-# Skipped: " + ", ".join(parts) + "."


# --- posting / editing the live report ----------------------------------------------
async def _get_channel(client: Any, channel_id: int) -> Any:
    channel = client.get_channel(channel_id)
    if channel is None:
        try:
            channel = await client.fetch_channel(channel_id)
        except Exception:
            return None
    return channel


async def _messages_since(channel: Any, message_id: int) -> int:
    """How many messages have been posted after ours, counting no further than we need."""
    try:
        after = discord.Object(id=int(message_id))
        return len([m async for m in channel.history(after=after,
                                                     limit=REPOST_AFTER_MESSAGES + 1)])
    except Exception:
        logger.debug("could not measure police-station activity", exc_info=True)
        return 0


def _superseded_view(link: str) -> discord.ui.LayoutView:
    """What an old report becomes: a pointer, with its buttons gone so a stale list
    can never be actioned by someone scrolling past it."""
    view = discord.ui.LayoutView(timeout=None)
    card = discord.ui.Container(accent_colour=0x95A5A6)
    card.add_item(discord.ui.TextDisplay(
        f"⤴️ **Superseded** — more accounts have arrived since this.\n"
        f"The current report is here: {link}"))
    view.add_item(card)
    return view


async def _refresh_report(client: Any, banned: list[str] | None = None) -> None:
    """Re-render the stored report in place (used after a ban)."""
    state = _load_state()
    if not state.get("message_id"):
        return
    channel = await _get_channel(client, int(state.get("channel_id") or CHANNELS.POLICE_STATION))
    if channel is None:
        return
    try:
        msg = await channel.fetch_message(int(state["message_id"]))
        await msg.edit(view=build_cluster_view(
            state.get("clusters", []),
            banned=banned or state.get("banned", []),
            quarantined=state.get("quarantined", []),
            watching=state.get("watching", []),
            dismissed_by=state.get("dismissed_by"),
            gone=state.get("gone", []),
        ), allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        logger.debug("could not refresh the join-cluster report", exc_info=True)


async def note_member_left(client: Any, user_id) -> None:
    """Mark someone on the live report as gone and redraw it.

    A batch quietly emptying itself is the most useful thing the card can tell you, and
    until now it only found out when a staff member happened to press Watch and got a
    failure back. Fires on every leave, so a batch that gives up on its own visibly
    resolves without anybody touching it.
    """
    try:
        uid = str(user_id)
        state = _load_state()
        if uid not in {str(u) for u in state.get("ids", [])}:
            return
        # A ban also raises a leave. Banned wins: it says who did it, not just that they
        # are no longer here.
        if uid in {str(u) for u in state.get("banned", [])}:
            return
        gone = set(state.get("gone", []))
        if uid in gone:
            return
        gone.add(uid)
        state["gone"] = sorted(gone)
        _save_state(state)
        await _refresh_report(client)
    except Exception:
        logger.exception("could not mark %s as having left the cluster report", user_id)


async def evaluate_joins(client: Any, records: Iterable[dict[str, Any]],
                         now: int | None = None) -> None:
    """Detect clusters and post or edit the single live report. Never raises.

    `now` is injectable so the reporting path can be tested against a fixed clock.
    """
    try:
        state = _load_state()
        # Accounts already dealt with are dropped before clustering rather than after.
        # They stay in the 24h join history after being banned, so they kept forming the
        # same clusters, and any unrelated new joiner changed the signature and reposted a
        # report about people who were gone. Dismissed batches were re-raised the same way.
        handled = {str(u) for u in state.get("banned", [])}
        handled |= {str(u) for u in state.get("dismissed_ids", [])}
        live = [r for r in records
                if isinstance(r, dict) and str(r.get("user_id", "")) not in handled]

        clusters = find_clusters(live, now=now)
        if not clusters:
            return
        sig = _signature(clusters)
        if sig == state.get("signature") and state.get("message_id"):
            return                      # nothing new arrived; leave the card alone

        channel = await _get_channel(client, CHANNELS.POLICE_STATION)
        if channel is None:
            return
        banned = state.get("banned", [])
        view = build_cluster_view(clusters, banned=banned,
                                  quarantined=state.get("quarantined", []),
                                  watching=state.get("watching", []),
                                  gone=state.get("gone", []), now=now)
        message_id = state.get("message_id")
        msg = None
        previous = None
        if message_id:
            try:
                previous = await channel.fetch_message(int(message_id))
            except Exception:
                previous = None         # deleted, or too old to fetch
        if previous is not None:
            if await _messages_since(channel, previous.id) >= REPOST_AFTER_MESSAGES:
                # Buried. Post again at the bottom and leave a pointer behind, rather than
                # silently updating a card that has scrolled out of view.
                msg = await channel.send(view=view,
                                         allowed_mentions=discord.AllowedMentions.none())
                try:
                    await previous.edit(view=_superseded_view(msg.jump_url),
                                        allowed_mentions=discord.AllowedMentions.none())
                except Exception:
                    logger.debug("could not stub the previous report", exc_info=True)
            else:
                await previous.edit(view=view,
                                    allowed_mentions=discord.AllowedMentions.none())
                msg = previous
        if msg is None:
            msg = await channel.send(view=view,
                                     allowed_mentions=discord.AllowedMentions.none())
        _save_state({
            "message_id": msg.id,
            "channel_id": channel.id,
            "signature": sig,
            "ids": cluster_user_ids(clusters),
            "clusters": clusters,
            "banned": banned,
            "quarantined": state.get("quarantined", []),
            "watching": state.get("watching", []),
            "updated_at": int(time.time()),
        })
        logger.info("join-cluster report updated: %s accounts in %s cluster(s)",
                    len(cluster_user_ids(clusters)), len(clusters))
    except Exception:
        logger.exception("join-cluster evaluation failed")


# --- voice: a batch turning up in the same call ------------------------------------
# Joining a Discord because your friends are already in a call is one of the commonest
# honest reasons anyone joins at all, so arriving and going straight to voice says nothing
# on its own and would flag a great many real people. Accounts registered minutes apart
# that walked in together, then landed in the same call, is a different claim - that one is
# hard to explain by coincidence, and it is the only thing this fires on.
VOICE_SIGHTINGS_FILE = os.path.join(JSON_DATA_DIR, "cluster_voice_sightings.json")
VOICE_MIN_TOGETHER = 2              # from one batch, in one channel, at the same time
VOICE_SIGHTING_TTL = 6 * 60 * 60    # forget a call once it is this stale


def _load_sightings() -> dict[str, Any]:
    """Kept apart from the cluster state because _refresh_report rewrites that wholesale."""
    try:
        with open(VOICE_SIGHTINGS_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_sightings(data: dict[str, Any]) -> None:
    try:
        from lib.core.file_operations import atomic_write_json
        atomic_write_json(VOICE_SIGHTINGS_FILE, data, indent=2)
    except Exception:
        logger.exception("could not persist cluster voice sightings")


def voice_cluster_finding(user_id: Any, present_ids: Iterable[Any],
                          state: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Which batch this member is from, and who else from it is in the call with them.

    Returns None unless at least VOICE_MIN_TOGETHER of one batch are in there together.
    Accounts already banned or already cleared as not-a-raid do not count towards it, so a
    settled report cannot be dragged back up by one of its members rejoining a call.
    """
    state = _load_state() if state is None else state
    uid = str(user_id)
    present = {str(p) for p in present_ids}
    settled = set(state.get("banned", [])) | set(state.get("dismissed_ids", []))
    for cluster in state.get("clusters", []):
        members = {m["user_id"] for m in cluster.get("members", [])}
        if uid not in members:
            continue
        together = sorted((members & present) - settled)
        if len(together) < VOICE_MIN_TOGETHER:
            return None
        return {"key": int(cluster["created_from"]), "cluster": cluster,
                "together": together, "size": len(members)}
    return None


def _voice_view(finding: dict[str, Any], state: dict[str, Any]) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    live = [m["user_id"] for m in finding["cluster"].get("members", [])
            if m["user_id"] not in set(state.get("banned", []))]
    view.add_item(BanClusterButton(finding["key"], len(live)))
    not_yet = [u for u in live if u not in set(state.get("quarantined", []))]
    if not_yet:
        view.add_item(QuarantineClusterButton(finding["key"], len(not_yet)))
    return view


def _voice_embed(finding: dict[str, Any], channel: Any, state: dict[str, Any]
                 ) -> discord.Embed:
    together, cluster = finding["together"], finding["cluster"]
    spread = max(1, int(cluster.get("spread", 0)) // 60)
    lines = [
        f"**{len(together)}** accounts from the same batch are in {channel.mention} "
        f"right now.",
        "",
        f"They were registered within **{spread} min** of each other and joined together "
        f"({finding['size']} in the batch).",
        "",
        " ".join(f"<@{u}>" for u in together[:20]),
    ]
    if len(together) > 20:
        lines.append(f"-# …and {len(together) - 20} more")
    mid, cid = state.get("message_id"), state.get("channel_id")
    if mid and cid:
        lines.append("\n-# [The batch's full report]"
                     f"(https://discord.com/channels/{GUILD_ID}/{cid}/{mid})")
    embed = discord.Embed(title="🔊 A join batch is sitting in one call",
                          description="\n".join(lines), colour=0xE67E22)
    embed.set_footer(text="Arriving and going straight to voice is normal · a whole batch "
                          "doing it in one channel is not")
    return embed


async def report_voice_cluster(client: Any, member: Any, channel: Any) -> bool:
    """One report per batch per call, edited in place as more of them arrive.

    Edited rather than reposted: during a wave the useful artefact is one growing list you
    can act on, not a notification per arrival.
    """
    try:
        present = [m.id for m in getattr(channel, "members", None) or []]
        state = _load_state()
        finding = voice_cluster_finding(member.id, present, state)
        if not finding:
            return False

        now = int(time.time())
        key = f"{finding['key']}:{channel.id}"
        sightings = {k: v for k, v in _load_sightings().items()
                     if now - int(v.get("at", 0)) < VOICE_SIGHTING_TTL}
        seen = set(sightings.get(key, {}).get("ids", []))
        if set(finding["together"]) <= seen:
            return False        # nobody new since the last report on this call

        report_channel = await _get_channel(client, CHANNELS.POLICE_STATION)
        if report_channel is None:
            return False
        embed = _voice_embed(finding, channel, state)
        view = _voice_view(finding, state)

        message_id = sightings.get(key, {}).get("message_id")
        msg = None
        if message_id:
            try:
                msg = await report_channel.fetch_message(int(message_id))
                await msg.edit(embed=embed, view=view,
                               allowed_mentions=discord.AllowedMentions.none())
            except Exception:
                msg = None
        if msg is None:
            msg = await report_channel.send(
                embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none())

        sightings[key] = {"ids": sorted(seen | set(finding["together"])),
                          "message_id": msg.id, "at": now}
        _save_sightings(sightings)
        logger.warning("join-cluster voice: %s of batch %s in channel %s",
                       len(finding["together"]), finding["key"], channel.id)
        return True
    except Exception:
        logger.exception("join-cluster voice check failed")
        return False


# --- voice: straight off the join screen into a call --------------------------------
# Weaker than the batch rule above and deliberately so. Plenty of people join a Discord
# because friends are already in a call, so this is a note for staff rather than a finding
# against anybody - no ban button, and one per member. It exists because a raider walking
# in and immediately taking the mic is a thing staff kept noticing, and nothing was
# recording it.
VOICE_RUSH_SECONDS = 5 * 60


def _fast_join_seconds(member: Any, now: float | None = None) -> float | None:
    joined = getattr(member, "joined_at", None)
    if joined is None:
        return None
    now = discord.utils.utcnow() if now is None else now
    return (now - joined).total_seconds()


async def report_fast_voice_join(client: Any, member: Any, channel: Any) -> bool:
    """Note a member who went into voice within minutes of arriving."""
    try:
        if getattr(member, "bot", False):
            return False
        seconds = _fast_join_seconds(member)
        if seconds is None or seconds > VOICE_RUSH_SECONDS or seconds < 0:
            return False

        now = int(time.time())
        key = f"rush:{member.id}"
        sightings = {k: v for k, v in _load_sightings().items()
                     if now - int(v.get("at", 0)) < VOICE_SIGHTING_TTL}
        if key in sightings:
            return False        # one note per member, not one per channel hop
        sightings[key] = {"ids": [str(member.id)], "at": now}
        _save_sightings(sightings)

        report_channel = await _get_channel(client, CHANNELS.POLICE_STATION)
        if report_channel is None:
            return False

        from lib.core.mod_actions import (
            ModAnalyseButton, ModIgnoreButton, ModTimeoutButton, VOICE_RUSH, action_view,
        )
        created = getattr(member, "created_at", None)
        bits = [f"{member.mention} `{member.id}` joined **{int(seconds)}s** ago and went "
                f"straight into {channel.mention}."]
        if created is not None:
            bits.append(f"-# Account made <t:{int(created.timestamp())}:R>")
        embed = discord.Embed(title="🎙️ Straight from joining into a call",
                              description="\n".join(bits), colour=0x5865F2)
        embed.set_footer(text="Common enough on its own - people join because friends are "
                              "already in there. Noted, not accused.")
        await report_channel.send(
            embed=embed,
            view=action_view(VOICE_RUSH, member.id,
                             only=(ModTimeoutButton, ModAnalyseButton, ModIgnoreButton)),
            allowed_mentions=discord.AllowedMentions.none())
        return True
    except Exception:
        logger.exception("fast voice join check failed")
        return False


async def on_voice_join(client: Any, member: Any, channel: Any) -> None:
    """Both voice checks, strongest first. The batch report already names everyone in it,
    so there is no point also posting a note about one of them arriving quickly."""
    if await report_voice_cluster(client, member, channel):
        return
    await report_fast_voice_join(client, member, channel)
