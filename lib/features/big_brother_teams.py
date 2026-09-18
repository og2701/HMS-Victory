"""Big Brother teams: split the house into Team A and Team B with a private room each.

The host taps names on a grid to cycle them A / B / none, then opens the rooms: two private
text channels beside the house channel that only that team (and the host) can see. Closing
the rooms deletes the channels; the team assignments stay until cleared. Room chat goes into
the event transcript like the house does.
"""

from __future__ import annotations

import asyncio
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
GRID_PAGE = 20  # names per page, leaving room for the action buttons
RELAY_DELAY = 8.0  # seconds of room chat gathered before a mole's DM goes out
_relay_buffer: dict[int, list[str]] = {}
_relay_tasks: dict[int, asyncio.Task] = {}


def ensure_tables() -> None:
    global _tables_ready
    if _tables_ready:
        return
    bb.ensure_tables()
    with DatabaseManager.transaction() as c:
        for stmt in ("ALTER TABLE bb_housemates ADD COLUMN team TEXT",
                     "ALTER TABLE bb_housemates ADD COLUMN mole INTEGER NOT NULL DEFAULT 0"):
            try:
                c.execute(stmt)
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


def moles() -> list[int]:
    """Housemates secretly reading the other team's room."""
    ensure_tables()
    rows = DatabaseManager.fetch_all(
        "SELECT user_id FROM bb_housemates WHERE status = ? AND mole = 1 ORDER BY joined_at", (bb.STATUS_IN,))
    return [int(r[0]) for r in rows]


def toggle_mole(user_id: int) -> bool:
    ensure_tables()
    row = DatabaseManager.fetch_one("SELECT mole FROM bb_housemates WHERE user_id = ?", (str(user_id),))
    new = 0 if (row and row[0]) else 1
    DatabaseManager.execute("UPDATE bb_housemates SET mole = ? WHERE user_id = ?", (new, str(user_id)))
    return bool(new)


def room_ids() -> dict[str, int]:
    rooms = bb.get_state(STATE_ROOMS) or {}
    return {t: int(cid) for t, cid in rooms.items() if t in TEAMS and cid}


def is_room(channel_id: int) -> bool:
    return int(channel_id) in room_ids().values()


def team_for_room(channel_id: int) -> Optional[str]:
    for t, cid in room_ids().items():
        if int(cid) == int(channel_id):
            return t
    return None


# ---------------------------------------------------------------------------
# The mole relay: the other team's room, forwarded to the mole's DMs.
#
# A mole is never given access to the room they are spying on - no overwrite, nothing in
# the member list, nothing for the other team to notice. They read it in their DMs and pass
# what they learn back to their own side through the snug.
# ---------------------------------------------------------------------------

async def _flush_relay(client: discord.Client, room_id: int, team: str) -> None:
    try:
        await asyncio.sleep(RELAY_DELAY)
    except asyncio.CancelledError:
        return
    _relay_tasks.pop(room_id, None)
    lines = _relay_buffer.pop(room_id, [])
    if not lines:
        return
    targets = [m for m in moles() if team_of(m) and team_of(m) != team]
    if not targets:
        return
    body = "\n".join(lines)[:3900]
    embed = bb.bb_embed(f"{TEAM_LABEL[team]} room", body)
    embed.set_footer(text="Nobody knows you can see this. Big Brother is watching.")
    for uid in targets:
        await bb.dm_user(client, uid, embed=embed, echo=False)
    bb.log_event("mole_relay", team=team, lines=len(lines), moles=targets)


async def relay_room_message(client: discord.Client, message: discord.Message) -> None:
    """Buffer a room message and make sure a flush is pending."""
    team = team_for_room(message.channel.id)
    if not team or not moles():
        return
    name = bb._name(message.guild, message.author.id)
    text = (message.content or "").strip()
    if message.attachments:
        text = (text + " " if text else "") + f"[{len(message.attachments)} attachment(s)]"
    if not text:
        return
    _relay_buffer.setdefault(message.channel.id, []).append(f"**{name}:** {text}"[:400])
    if message.channel.id not in _relay_tasks:
        _relay_tasks[message.channel.id] = asyncio.create_task(_flush_relay(client, message.channel.id, team))


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
    spies = set(moles())
    lines = ["Tap a name to cycle it: none → 🅰️ → 🅱️ → none."]
    for t in TEAMS:
        names = ", ".join(bb._name(guild, u) + (" 🕵️" if u in spies else "") for u in split[t]) or "nobody yet"
        room = f" · <#{rooms[t]}>" if t in rooms else ""
        lines.append(f"**{TEAM_LABEL[t]}** ({len(split[t])}){room}: {names}")
    if unassigned:
        lines.append(f"-# Not on a team: {', '.join(bb._name(guild, u) for u in unassigned)}")
    return "\n".join(lines)[:1900]


class _TeamsPage(discord.ui.View):
    def __init__(self, guild, ids: list[int]):
        super().__init__(timeout=600)
        self.guild, self.ids = guild, ids
        self._build()

    def _build(self):
        self.clear_items()
        for uid in self.ids[:GRID_PAGE]:
            t = team_of(uid)
            style = {"A": discord.ButtonStyle.primary, "B": discord.ButtonStyle.success}.get(t, discord.ButtonStyle.secondary)
            btn = discord.ui.Button(label=(f"{t} · " if t else "") + bb._name(self.guild, uid)[:70], style=style)

            async def _cycle(interaction: discord.Interaction, _uid=uid):
                cycle_team(_uid)
                self._build()
                await interaction.response.edit_message(content=_summary(interaction.guild), view=self)
            btn.callback = _cycle
            self.add_item(btn)

        rooms_open = bool(room_ids())
        open_btn = discord.ui.Button(label="Re-sync rooms" if rooms_open else "Open rooms",
                                     style=discord.ButtonStyle.success, emoji="🚪", row=4)
        close_btn = discord.ui.Button(label="Close rooms", style=discord.ButtonStyle.danger, emoji="🧹",
                                      row=4, disabled=not rooms_open)
        clear_btn = discord.ui.Button(label="Clear teams", emoji="♻️", row=4)

        async def _open(interaction: discord.Interaction):
            await interaction.response.defer()
            rooms, err = await open_rooms(interaction.client)
            self._build()
            await interaction.edit_original_response(
                content=(err or "Rooms are open. Each team has been welcomed in theirs.") + "\n\n" + _summary(interaction.guild),
                view=self)

        async def _close(interaction: discord.Interaction):
            await interaction.response.defer()
            n = await close_rooms(interaction.client)
            self._build()
            await interaction.edit_original_response(
                content=f"Closed {n} room(s). Teams are still assigned.\n\n" + _summary(interaction.guild), view=self)

        async def _clear(interaction: discord.Interaction):
            clear_teams()
            bb.log_event("teams_cleared", actor=interaction.user.id)
            self._build()
            await interaction.response.edit_message(content=_summary(interaction.guild), view=self)

        moles_btn = discord.ui.Button(label="Moles", emoji="🕵️", row=4)

        async def _moles(interaction: discord.Interaction):
            on_team = [u for u in bb.housemates() if team_of(u)]
            if not on_team:
                await interaction.response.send_message("Assign some teams first.", ephemeral=True)
                return

            async def toggled(inter: discord.Interaction, uid: int) -> bool:
                now_mole = toggle_mole(uid)
                bb.log_event("mole_toggled", target=uid, mole=now_mole, team=team_of(uid))
                if now_mole:
                    other = "B" if team_of(uid) == "A" else "A"
                    await bb.dm_user(inter.client, uid, ack="mole briefing", embed=bb.bb_embed(
                        "You are the mole",
                        f"You're on {TEAM_LABEL[team_of(uid)]}, but Big Brother is going to show you what "
                        f"{TEAM_LABEL[other]} are saying. Their room chat will arrive here, in your DMs, every "
                        f"few seconds.\n\nYou have no access to their room and you never will, so there is "
                        f"nothing for them to notice.\n\nGetting what you learn back to your own side is your "
                        f"problem: use **The snug** on the house panel and choose who to take in with you. "
                        f"Choose carefully. Say nothing in the house."))
                return now_mole

            await bb._send_toggle(interaction, "🟢 mole · 🔴 not. A mole reads the OTHER team's room in their DMs.",
                                  interaction.guild, on_team, {u: u in set(moles()) for u in on_team}, toggled)

        moles_btn.callback = _moles
        open_btn.callback, close_btn.callback, clear_btn.callback = _open, _close, _clear
        for b in (open_btn, close_btn, clear_btn, moles_btn):
            self.add_item(b)


async def act_teams(interaction: discord.Interaction):
    ins = bb.housemates()
    if not ins:
        await interaction.response.send_message("No housemates yet.", ephemeral=True)
        return
    pages = [ins[i:i + GRID_PAGE] for i in range(0, len(ins), GRID_PAGE)]
    views = [_TeamsPage(interaction.guild, page) for page in pages]
    await bb._GridSet().deliver(interaction, _summary(interaction.guild), views)


def export() -> dict:
    return {"teams": teams(), "rooms": room_ids(), "moles": moles()}
