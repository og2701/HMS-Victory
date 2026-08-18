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

from config import CHANNELS, JSON_DATA_DIR, ROLES

logger = logging.getLogger(__name__)

CLUSTER_STATE_FILE = os.path.join(JSON_DATA_DIR, "join_clusters.json")
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
                       dismissed_by: str | None = None,
                       now: int | None = None) -> discord.ui.LayoutView:
    banned = set(banned or [])
    quarantined = set(quarantined or [])
    watching = set(watching or [])
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
            name = discord.utils.escape_markdown(str(m.get("username", "?")))
            line = f"**{name}** · joined <t:{int(m.get('joined_at', 0))}:R> · `{uid}`"
            if uid in banned:
                line = f"~~{line}~~ 🔨"
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
        done, failed = await quarantine_ids(interaction.guild, ids, interaction.user)
        state["quarantined"] = sorted(set(state.get("quarantined", [])) | set(done))
        _save_state(state)
        await _refresh_report(interaction.client)
        note = f"🔒 Quarantined {len(done)} account(s)."
        if failed:
            note += f"\nFailed for {len(failed)} (missing role, or already gone)."
        await interaction.followup.send(note, ephemeral=True)


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
        done, failed = await watch_ids(interaction.guild, ids)
        state["watching"] = sorted(set(state.get("watching", [])) | set(done))
        _save_state(state)
        await _refresh_report(interaction.client)
        await interaction.followup.send(
            f"👁️ Now screening the first messages from {len(done)} account(s). "
            "They are not restricted - if they post anything flaggable the usual "
            "join-watch report fires.", ephemeral=True)


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


async def quarantine_ids(guild: Any, ids: list[str], actor: Any) -> tuple[list[str], list[str]]:
    """Give the quarantine role instead of banning. Reversible, and the right first move
    when the evidence is a coincidence away from being innocent."""
    from commands.moderation.anti_raid import QUARANTINE_ROLE_ID, mark_join_quarantined
    role = guild.get_role(QUARANTINE_ROLE_ID)
    if role is None:
        return [], list(ids)
    done, failed = [], []
    reason = f"Batch-created account cluster · quarantined by {getattr(actor, 'id', '?')}"
    for uid in ids:
        try:
            member = guild.get_member(int(uid)) or await guild.fetch_member(int(uid))
            await member.add_roles(role, reason=reason[:500])
            try:
                mark_join_quarantined(int(uid))
            except Exception:
                logger.debug("could not mark %s quarantined in join history", uid)
            done.append(uid)
        except Exception:
            logger.exception("join-cluster quarantine failed for %s", uid)
            failed.append(uid)
    return done, failed


async def watch_ids(guild: Any, ids: list[str]) -> tuple[list[str], list[str]]:
    """Screen these accounts' first messages, without arming the watch server-wide."""
    from commands.moderation.join_watch import watch_member
    done, failed = [], []
    for uid in ids:
        try:
            member = guild.get_member(int(uid)) or await guild.fetch_member(int(uid))
            (done if watch_member(member) else failed).append(uid)
        except Exception:
            logger.debug("join-cluster watch failed for %s", uid, exc_info=True)
            failed.append(uid)
    return done, failed


async def ban_ids(guild: Any, ids: list[str], actor: Any) -> tuple[list[str], list[str]]:
    """Ban each id, reporting rather than raising. One failure must not stop the rest."""
    banned, failed = [], []
    reason = f"Batch-created account cluster · authorised by {getattr(actor, 'id', '?')}"
    for uid in ids:
        try:
            await guild.ban(discord.Object(id=int(uid)), reason=reason[:500],
                            delete_message_seconds=0)
            banned.append(uid)
        except Exception:
            logger.exception("join-cluster ban failed for %s", uid)
            failed.append(uid)
    return banned, failed


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
        ), allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        logger.debug("could not refresh the join-cluster report", exc_info=True)


async def evaluate_joins(client: Any, records: Iterable[dict[str, Any]],
                         now: int | None = None) -> None:
    """Detect clusters and post or edit the single live report. Never raises.

    `now` is injectable so the reporting path can be tested against a fixed clock.
    """
    try:
        clusters = find_clusters(records, now=now)
        state = _load_state()
        if not clusters:
            return
        sig = _signature(clusters)
        if sig == state.get("signature") and state.get("message_id"):
            return                      # nothing new arrived; leave the card alone
        if state.get("dismissed_by") and sig == state.get("signature"):
            return                      # staff said this was nothing

        channel = await _get_channel(client, CHANNELS.POLICE_STATION)
        if channel is None:
            return
        banned = state.get("banned", [])
        view = build_cluster_view(clusters, banned=banned,
                                  quarantined=state.get("quarantined", []),
                                  watching=state.get("watching", []), now=now)
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
