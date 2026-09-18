"""Big Brother teams: split the house into Team A and Team B with a private room each.

The host taps names on a grid to cycle them A / B / none, then opens the rooms: two private
text channels beside the house channel that only that team (and the host) can see. Closing
the rooms deletes the channels; the team assignments stay until cleared. Room chat goes into
the event transcript like the house does.
"""

from __future__ import annotations

import logging
from typing import Optional

import discord

from database import DatabaseManager
from lib.features import big_brother as bb

log = logging.getLogger(__name__)

TEAMS = ("A", "B")
TEAM_LABEL = {"A": "🅰️ Team A", "B": "🅱️ Team B"}
ROOM_NAME = {"A": "🅰️-team-a", "B": "🅱️-team-b"}
STATE_ROOMS = "team_rooms"  # {"A": channel_id, "B": channel_id}
_tables_ready = False
GRID_PAGE = 21  # names per page, leaving room for the three action buttons


def ensure_tables() -> None:
    global _tables_ready
    if _tables_ready:
        return
    bb.ensure_tables()
    with DatabaseManager.transaction() as c:
        try:
            c.execute("ALTER TABLE bb_housemates ADD COLUMN team TEXT")
        except Exception:
            pass
    _tables_ready = True


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def team_of(user_id: int) -> Optional[str]:
    ensure_tables()
    row = DatabaseManager.fetch_one("SELECT team FROM bb_housemates WHERE user_id = ?", (str(user_id),))
    return row[0] if row and row[0] in TEAMS else None


def set_team(user_id: int, team: Optional[str]) -> None:
    ensure_tables()
    DatabaseManager.execute("UPDATE bb_housemates SET team = ? WHERE user_id = ?",
                            (team if team in TEAMS else None, str(user_id)))


def cycle_team(user_id: int) -> Optional[str]:
    """none -> A -> B -> none. Returns the new team."""
    order = [None, "A", "B"]
    new = order[(order.index(team_of(user_id)) + 1) % len(order)]
    set_team(user_id, new)
    return new


def teams() -> dict[str, list[int]]:
    """Current housemates by team, in join order."""
    ensure_tables()
    rows = DatabaseManager.fetch_all(
        "SELECT user_id, team FROM bb_housemates WHERE status = ? AND team IN ('A', 'B') ORDER BY joined_at",
        (bb.STATUS_IN,))
    out = {t: [] for t in TEAMS}
    for uid, t in rows:
        out[t].append(int(uid))
    return out


def clear_teams() -> None:
    ensure_tables()
    DatabaseManager.execute("UPDATE bb_housemates SET team = NULL")


def room_ids() -> dict[str, int]:
    rooms = bb.get_state(STATE_ROOMS) or {}
    return {t: int(cid) for t, cid in rooms.items() if t in TEAMS and cid}


def is_room(channel_id: int) -> bool:
    return int(channel_id) in room_ids().values()


# ---------------------------------------------------------------------------
# Rooms
# ---------------------------------------------------------------------------

def _room_overwrites(guild: discord.Guild, members: list[int]) -> dict:
    ow = {guild.default_role: discord.PermissionOverwrite(view_channel=False)}
    host = guild.get_member(bb.host_id())
    if host:
        ow[host] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
    for uid in members:
        m = guild.get_member(uid)
        if m:
            ow[m] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
    return ow


async def open_rooms(client: discord.Client) -> tuple[dict[str, int], str]:
    """Create (or re-sync) the two team rooms. Returns ({team: channel_id}, error)."""
    house = await bb.house_channel(client)
    if not isinstance(house, discord.TextChannel):
        return {}, "House channel not found."
    guild = house.guild
    split = teams()
    if not split["A"] or not split["B"]:
        return {}, "Both teams need at least one housemate first."
    rooms = room_ids()
    created = {}
    for t in TEAMS:
        ow = _room_overwrites(guild, split[t])
        ch = guild.get_channel(rooms[t]) if t in rooms else None
        try:
            if isinstance(ch, discord.TextChannel):
                await ch.edit(overwrites=ow, reason="Big Brother: team room re-synced")
            else:
                ch = await guild.create_text_channel(
                    ROOM_NAME[t], category=house.category, overwrites=ow, position=house.position + 1,
                    reason="Big Brother: team room")
                mentions = " ".join(f"<@{u}>" for u in split[t])
                await bb.bb_send(ch, f"{bb.EYE} **Welcome to {TEAM_LABEL[t]}.** {mentions}\n\n"
                                     f"Only your team and Big Brother can see this room. Plan here.")
        except discord.HTTPException as e:
            log.warning("Big Brother: could not open team room %s: %s", t, e)
            return created, f"Couldn't create or update the {TEAM_LABEL[t]} room: {e}"
        created[t] = ch.id
    bb.set_state(STATE_ROOMS, created)
    bb.log_event("team_rooms_opened", teams=split, rooms=created)
    return created, ""


async def close_rooms(client: discord.Client) -> int:
    """Delete the team rooms; team assignments are kept."""
    rooms = room_ids()
    removed = 0
    for t, cid in rooms.items():
        ch = await bb._channel(client, cid)
        if ch:
            try:
                await ch.delete(reason="Big Brother: team room closed")
                removed += 1
            except discord.HTTPException as e:
                log.warning("Big Brother: could not delete team room %s: %s", t, e)
    bb.set_state(STATE_ROOMS, None)
    bb.log_event("team_rooms_closed", removed=removed)
    return removed


# ---------------------------------------------------------------------------
# Control panel screen
# ---------------------------------------------------------------------------

def _summary(guild) -> str:
    split = teams()
    rooms = room_ids()
    unassigned = [u for u in bb.housemates() if team_of(u) is None]
    lines = ["Tap a name to cycle it: none → 🅰️ → 🅱️ → none."]
    for t in TEAMS:
        names = ", ".join(bb._name(guild, u) for u in split[t]) or "nobody yet"
        room = f" · <#{rooms[t]}>" if t in rooms else ""
        lines.append(f"**{TEAM_LABEL[t]}** ({len(split[t])}){room}: {names}")
    if unassigned:
        if len(unassigned) > 8:
            lines.append(f"-# Not on a team yet: {len(unassigned)}")
        else:
            lines.append(f"-# Not on a team: {', '.join(bb._name(guild, u) for u in unassigned)}")
    return "\n".join(lines)[:1900]


def _page_content(guild, index: int, pages: int) -> str:
    """Every page carries the live summary, so whichever one the host is looking at is
    correct as of her last press there."""
    return _summary(guild) + (f"\n-# page {index + 1} of {pages}" if pages > 1 else "")


class _TeamsPage(discord.ui.View):
    """One page of the teams grid. The action buttons live on page one only. A page only ever
    edits its own message - see the note on _GridSet.views for why."""

    def __init__(self, guild, ids: list[int], *, index: int = 0, pages: int = 1, actions: bool = True):
        super().__init__(timeout=600)
        self.guild, self.ids, self.index, self.pages, self.actions = guild, ids, index, pages, actions
        self._build()

    def content(self, guild) -> str:
        return _page_content(guild, self.index, self.pages)

    def _build(self):
        self.clear_items()
        for uid in self.ids[:GRID_PAGE]:
            t = team_of(uid)
            style = {"A": discord.ButtonStyle.primary, "B": discord.ButtonStyle.success}.get(t, discord.ButtonStyle.secondary)
            btn = discord.ui.Button(label=(f"{t} · " if t else "") + bb._name(self.guild, uid)[:70], style=style)

            async def _cycle(interaction: discord.Interaction, _uid=uid):
                cycle_team(_uid)
                self._build()
                await interaction.response.edit_message(content=self.content(interaction.guild), view=self)
            btn.callback = _cycle
            self.add_item(btn)

        if not self.actions:
            return
        rooms_open = bool(room_ids())
        open_btn = discord.ui.Button(label="Re-sync rooms" if rooms_open else "Open rooms",
                                     style=discord.ButtonStyle.success, emoji="🚪", row=4)
        close_btn = discord.ui.Button(label="Close rooms", style=discord.ButtonStyle.danger, emoji="🧹",
                                      row=4, disabled=not rooms_open)
        clear_btn = discord.ui.Button(label="Clear teams", emoji="♻️", row=4)

        async def _open(interaction: discord.Interaction):
            await interaction.response.defer()
            rooms, err = await open_rooms(interaction.client)
            note = err or "Rooms are open. Each team has been welcomed in theirs."
            self._build()
            await interaction.edit_original_response(content=f"{note}\n\n" + self.content(interaction.guild), view=self)

        async def _close(interaction: discord.Interaction):
            await interaction.response.defer()
            n = await close_rooms(interaction.client)
            self._build()
            await interaction.edit_original_response(
                content=f"Closed {n} room(s). Teams are still assigned.\n\n" + self.content(interaction.guild), view=self)

        async def _clear(interaction: discord.Interaction):
            clear_teams()
            bb.log_event("teams_cleared", actor=interaction.user.id)
            self._build()
            await interaction.response.edit_message(content=self.content(interaction.guild), view=self)

        open_btn.callback, close_btn.callback, clear_btn.callback = _open, _close, _clear
        for b in (open_btn, close_btn, clear_btn):
            self.add_item(b)


async def act_teams(interaction: discord.Interaction):
    ins = bb.housemates()
    if not ins:
        await interaction.response.send_message("No housemates yet.", ephemeral=True)
        return
    pages = [ins[i:i + GRID_PAGE] for i in range(0, len(ins), GRID_PAGE)]
    views = [_TeamsPage(interaction.guild, page, index=i, pages=len(pages), actions=(i == 0))
             for i, page in enumerate(pages)]
    await bb._GridSet().deliver(interaction, _summary(interaction.guild), views)


def export() -> dict:
    return {"teams": teams(), "rooms": room_ids()}
