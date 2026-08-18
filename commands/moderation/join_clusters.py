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


# --- detection ---------------------------------------------------------------------
def find_clusters(records: Iterable[dict[str, Any]], now: int | None = None) -> list[dict[str, Any]]:
    """Group recent joiners whose accounts were created within CREATION_WINDOW of each other.

    Pure and side-effect free so it can be tested without a guild. Returns clusters newest
    join first, each: {"created_from", "created_to", "spread", "tight", "members": [record]}.
    """
    current = int(time.time() if now is None else now)
    recent = [
        r for r in records
        if isinstance(r, dict)
        and current - int(r.get("joined_at", 0) or 0) <= JOIN_WINDOW_SECONDS
    ]
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
def build_cluster_view(clusters: list[dict[str, Any]], banned: list[str] | None = None
                       ) -> discord.ui.LayoutView:
    banned = banned or []
    ids = cluster_user_ids(clusters)
    view = discord.ui.LayoutView(timeout=None)
    card = discord.ui.Container(accent_colour=0xE67E22 if not banned else 0x95A5A6)

    header = (
        "## 🛰️ Batch-created accounts joining together\n"
        f"{len(ids)} account(s) across {len(clusters)} creation cluster(s) in the last "
        f"{JOIN_WINDOW_SECONDS // 3600}h."
    )
    card.add_item(discord.ui.TextDisplay(header))
    card.add_item(discord.ui.Separator())

    for c in clusters:
        members = c["members"]
        spread = c["spread"]
        when = f"<t:{c['created_from']}:f>"
        spread_txt = ("all within "
                      + (f"{spread // 60}m" if spread >= 60 else f"{spread}s"))
        flag = " · ⚠️ **scripted spread**" if c["tight"] else ""
        lines = [f"**{len(members)} accounts created {when}** · {spread_txt}{flag}"]
        for m in members[:MAX_LISTED]:
            mark = " ~~banned~~" if m["user_id"] in banned else ""
            lines.append(
                f"`{m['user_id']}` {discord.utils.escape_markdown(str(m.get('username','?')))}"
                f" · joined <t:{int(m.get('joined_at', 0))}:R>{mark}"
            )
        if len(members) > MAX_LISTED:
            lines.append(f"-# …and {len(members) - MAX_LISTED} more")
        card.add_item(discord.ui.TextDisplay("\n".join(lines)[:1800]))
        card.add_item(discord.ui.Separator())

    if banned:
        card.add_item(discord.ui.TextDisplay(
            f"✅ **{len(banned)} account(s) banned** from this report."))
    else:
        card.add_item(discord.ui.ActionRow(MassBanButton(len(ids))))
        card.add_item(discord.ui.TextDisplay(
            "-# Creation clustering is evidence, not proof - two friends who signed up "
            "together look the same. Nothing here is automatic; check the list before banning."
        ))
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
        await msg.edit(view=build_cluster_view(state.get("clusters", []),
                                               banned=banned or state.get("banned", [])),
                       allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        logger.debug("could not refresh the join-cluster report", exc_info=True)


async def evaluate_joins(client: Any, records: Iterable[dict[str, Any]]) -> None:
    """Detect clusters and post or edit the single live report. Never raises."""
    try:
        clusters = find_clusters(records)
        state = _load_state()
        if not clusters:
            return
        sig = _signature(clusters)
        if sig == state.get("signature") and state.get("message_id"):
            return                      # nothing new arrived; leave the card alone

        channel = await _get_channel(client, CHANNELS.POLICE_STATION)
        if channel is None:
            return
        banned = state.get("banned", [])
        view = build_cluster_view(clusters, banned=banned)
        message_id = state.get("message_id")
        msg = None
        if message_id:
            try:
                msg = await channel.fetch_message(int(message_id))
                await msg.edit(view=view, allowed_mentions=discord.AllowedMentions.none())
            except Exception:
                msg = None              # deleted or too old; post a fresh one
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
            "updated_at": int(time.time()),
        })
        logger.info("join-cluster report updated: %s accounts in %s cluster(s)",
                    len(cluster_user_ids(clusters)), len(clusters))
    except Exception:
        logger.exception("join-cluster evaluation failed")
