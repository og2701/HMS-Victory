"""Battleship - a 1v1 player-vs-player wager for UKPence (winner takes the pot).

Flow:
  /battleship @rival <bet> posts a challenge (Accept / Decline / Rules). Stakes are only
  taken once the rival ACCEPTS - then both buy-ins go into the house bank and the winner is
  paid the whole pot back out of it (the bank nets zero; the fixed UKP supply is conserved).
  A void (restart / nobody sets up in time) refunds both.

  On accept the game opens its own thread and adds both players. The board is a single
  message edited IN PLACE each turn, with the current player @mentioned. Banter goes in the
  parent channel.

  Board: a 4x6 ocean (mobile-friendly, like Mines - phones wrap ~4 buttons/row). Each player
  places their fleet by tapping cells on a 4x6 grid (Rotate / Undo / Auto / Ready). Shot
  results are public so the board shows both players' fog grids; only your own *unhit* ship
  layout is private. Firing is a 4x6 button grid in an ephemeral that stays open and updates
  as the game goes - tap an enemy square to bomb it. Each turn has a forfeit clock.

  Persistence: a tiny escrow record (both players + stake + thread) is keyed by the CHALLENGE
  message id. The live board/timer don't survive a restart, so on boot an in-progress game is
  VOIDED and both stakes refunded (reattach_battleship_view). The record is deleted the instant
  a game settles, so a leftover entry always means "still owed".
"""

import asyncio
import logging
import random

import discord
from discord import Interaction, Member

import config
from lib.economy.economy_manager import get_bb, remove_bb
from commands.economy.casino_base import (
    credit_from_bank, save_state, delete_state, reject_if_maintenance,
)

logger = logging.getLogger(__name__)

COLS = 4
ROWS = 6
SHIPS = [3, 2, 2]                      # ship sizes; 7 cells of 24 (~29% density)
SHIP_NAMES = {3: "Cruiser", 2: "Patrol Boat"}

UNKNOWN = "⬛"        # un-fired square (fog) / empty placement square
HIT = "\U0001f525"   # 🔥 a hit on a ship that's still afloat
SUNK = "\U0001f4a5"  # 💥 every cell of a fully-sunk ship
MISS = "\U0001f30a"  # 🌊
WATER = "\U0001f7e6" # 🟦 own untouched water
SHIP = "\U0001f6a2"  # 🚢 your intact ship
ACCENT = discord.Colour(0x3498DB)

# Thread ids of in-progress games. Real thread-locking blocks button interactions, so the
# board's thread is "pseudo-locked" instead: on_message deletes anything posted there by
# anyone other than the bot (see event_handlers.on_message), keeping the board clean.
ACTIVE_GAME_THREADS = set()


def _forfeit_seconds():
    return getattr(config, "BATTLESHIP_FORFEIT_SECONDS", 600)


def rules_text() -> str:
    mins = _forfeit_seconds() // 60
    return (
        "## \U0001f6a2 Battleship - Rules\n"
        f"Two captains, a **{COLS}x{ROWS}** ocean each. Place your fleet "
        f"({', '.join(map(str, SHIPS))}-cell ships) by tapping cells - **Rotate** to flip a "
        "ship, **Auto** to fill the rest, then **Ready**.\n\n"
        "- Take turns firing at the enemy grid (tap a square). \U0001f4a5 = hit, \U0001f30a = miss. "
        "Sink every enemy ship to win.\n"
        "- Shot results are public (both fog grids show on the board); only your own *unhit* "
        "ships are hidden - check them with **My fleet**.\n"
        "- Both players stake the same; **winner takes the whole pot** (2x the stake).\n"
        f"- ⏳ **Forfeit timer:** {mins} minutes per move (and to set up your fleet). "
        "Miss it and your opponent takes the pot.\n"
        "- The game runs in its own thread; the board updates in place each turn."
    )


class BattleshipGame:
    """One game's state + rendering/flow. Not a View itself - the board's button view is
    rebuilt fresh each render, mirroring the poker table."""

    def __init__(self, p1_id, p1_name, p2_id, p2_name, stake, channel_id):
        self.p1_id, self.p2_id = int(p1_id), int(p2_id)
        self.p1_name, self.p2_name = p1_name, p2_name
        self.stake = int(stake)
        self.channel_id = channel_id
        self.fleet = {1: [], 2: []}        # placed ships, built up during placement
        self.place_orient = {1: "h", 2: "h"}
        self.shots = {1: {}, 2: {}}        # shots[p][(r,c)] = 'hit'|'miss'  (p's shots on the OTHER grid)
        self.ready = {1: False, 2: False}
        self.fire_iaction = {1: None, 2: None}   # latest interaction touching each firing panel
        self.phase = "placing"             # placing | firing | over
        self.turn = random.choice([1, 2])
        self.last = None                   # (shooter, (r,c), result, sunk_size) for the status line
        self.thread = None
        self.message = None                # board message (in the thread)
        self.client = None
        self.escrow_key = None             # challenge message id - the escrow record's key
        self.game_over = False
        self._lock = asyncio.Lock()
        self._timer_task = None

    # --- helpers -----------------------------------------------------------
    def _pnum(self, uid):
        return 1 if uid == self.p1_id else 2 if uid == self.p2_id else None

    def _uid(self, p):
        return self.p1_id if p == 1 else self.p2_id

    def _name(self, p):
        return self.p1_name if p == 1 else self.p2_name

    @staticmethod
    def _opp(p):
        return 2 if p == 1 else 1

    def _ship_cells(self, p):
        cells = set()
        for s in self.fleet[p]:
            cells |= s["cells"]
        return cells

    def _next_ship(self, p):
        placed = len(self.fleet[p])
        return SHIPS[placed] if placed < len(SHIPS) else None

    def _try_place(self, p, r, c):
        """Place the next ship with its bow at (r,c). Returns False if it won't fit / overlaps."""
        size = self._next_ship(p)
        if size is None:
            return False
        if self.place_orient[p] == "h":
            if c + size > COLS:
                return False
            cells = {(r, c + i) for i in range(size)}
        else:
            if r + size > ROWS:
                return False
            cells = {(r + i, c) for i in range(size)}
        if cells & self._ship_cells(p):
            return False
        self.fleet[p].append({"size": size, "cells": cells})
        return True

    def _auto_place_rest(self, p):
        """Randomly place whatever ships are left, avoiding the already-placed ones."""
        occupied = set(self._ship_cells(p))
        for size in SHIPS[len(self.fleet[p]):]:
            for _ in range(300):
                if random.choice((True, False)):
                    r, c = random.randrange(ROWS), random.randrange(COLS - size + 1)
                    cells = {(r, c + i) for i in range(size)}
                else:
                    r, c = random.randrange(ROWS - size + 1), random.randrange(COLS)
                    cells = {(r + i, c) for i in range(size)}
                if cells & occupied:
                    continue
                occupied |= cells
                self.fleet[p].append({"size": size, "cells": cells})
                break

    def _fire(self, shooter, cell):
        """Resolve shooter firing at cell. Returns (result, sunk_size, won)."""
        target = self._opp(shooter)
        ship = next((s for s in self.fleet[target] if cell in s["cells"]), None)
        if ship is None:
            self.shots[shooter][cell] = "miss"
            return "miss", None, False
        self.shots[shooter][cell] = "hit"
        sunk = all(c in self.shots[shooter] for c in ship["cells"])
        won = all(c in self.shots[shooter] for c in self._ship_cells(target))
        return ("sunk" if sunk else "hit"), (ship["size"] if sunk else None), won

    # --- grids -------------------------------------------------------------
    @staticmethod
    def _sunk_in(fleet, shots):
        """Cells of ships in `fleet` that are fully hit by `shots` (i.e. sunk)."""
        cells = set()
        for s in fleet:
            if all(c in shots for c in s["cells"]):
                cells |= s["cells"]
        return cells

    def _fog_grid(self, shooter):
        sunk = self._sunk_in(self.fleet[self._opp(shooter)], self.shots[shooter])
        out = []
        for r in range(ROWS):
            row = ""
            for c in range(COLS):
                cell = (r, c)
                v = self.shots[shooter].get(cell)
                if v == "hit":
                    row += SUNK if cell in sunk else HIT      # 🔥 while afloat, 💥 once sunk
                elif v == "miss":
                    row += MISS
                else:
                    row += UNKNOWN
            out.append(row)
        return "\n".join(out)

    def _fleet_grid(self, p):
        ship_cells = self._ship_cells(p)
        incoming = self.shots[self._opp(p)]
        sunk = self._sunk_in(self.fleet[p], incoming)
        out = []
        for r in range(ROWS):
            row = ""
            for c in range(COLS):
                cell = (r, c)
                if cell in ship_cells:
                    if cell in incoming:
                        row += SUNK if cell in sunk else HIT
                    else:
                        row += SHIP
                else:
                    row += MISS if cell in incoming else WATER
            out.append(row)
        return "\n".join(out)

    def _embed(self, *, final=False, winner=None, forfeit=False):
        title = "\U0001f6a2 Battleship"
        if self.phase == "placing" and not final:
            lines = [f"\U0001f6a2 **{self.p1_name}**  vs  **{self.p2_name}**", "",
                     "Set up your fleets, then the shooting starts.", "",
                     f"{'✅' if self.ready[1] else '⏳'} **{self.p1_name}**",
                     f"{'✅' if self.ready[2] else '⏳'} **{self.p2_name}**", "",
                     "-# Tap **Place fleet** below to position your ships."]
            return discord.Embed(title=title, description="\n".join(lines), colour=ACCENT)

        if final:
            pot = self.stake * 2
            lines = [f"\U0001f6a2 **{self.p1_name}**  vs  **{self.p2_name}**", ""]
            if winner is None:
                lines.append(f"\U0001f91d Voided - both **{self.stake:,}** UKP stakes refunded.")
                colour = 0x95A5A6
            else:
                wname, lname = self._name(winner), self._name(self._opp(winner))
                if forfeit:
                    lines.append(f"⏳ **{lname}** ran out of time - **{wname}** takes the "
                                 f"**{pot:,}** UKP pot by forfeit!")
                else:
                    lines.append(f"\U0001f3c6 **{wname}** sank the fleet and wins the "
                                 f"**{pot:,}** UKP pot!")
                colour = 0x2ECC71
            lines += ["", f"**{self.p1_name}'s fleet**", self._fleet_grid(1),
                      "", f"**{self.p2_name}'s fleet**", self._fleet_grid(2)]
            e = discord.Embed(title=title, description="\n".join(lines), colour=colour)
            e.set_footer(text="Battleship · winner takes the pot")
            return e

        lines = [f"\U0001f6a2 **{self.p1_name}**  vs  **{self.p2_name}**", "",
                 f"\U0001f3af **{self.p1_name}** → {self.p2_name}", self._fog_grid(1), "",
                 f"\U0001f3af **{self.p2_name}** → {self.p1_name}", self._fog_grid(2), ""]
        if self.last:
            shooter, (r, c), result, sunk_size = self.last
            coord = f"{chr(ord('A') + r)}{c + 1}"
            who = self._name(shooter)
            if result == "miss":
                lines.append(f"\U0001f30a **{who}** fired at {coord} - miss.")
            elif result == "sunk":
                lines.append(f"\U0001f4a5 **{who}** sank a **{SHIP_NAMES.get(sunk_size, 'ship')}** at {coord}!")
            else:
                lines.append(f"\U0001f525 **{who}** hit at {coord}!")
        lines.append(f"➡️  **{self._name(self.turn)}'s turn to fire**")
        e = discord.Embed(title=title, description="\n".join(lines), colour=ACCENT)
        e.set_footer(text=f"⏳ {_forfeit_seconds() // 60} min to fire, or you forfeit the pot")
        return e

    def _board_view(self):
        v = discord.ui.View(timeout=None)
        if self.phase == "placing":
            b = discord.ui.Button(label="Place fleet", emoji=SHIP, style=discord.ButtonStyle.primary)
            b.callback = self._on_place
            v.add_item(b)
        elif self.phase == "firing":
            fire = discord.ui.Button(label="Fire", emoji="\U0001f3af", style=discord.ButtonStyle.danger)
            fire.callback = self._on_fire_open
            v.add_item(fire)
            fleet = discord.ui.Button(label="My fleet", emoji=SHIP, style=discord.ButtonStyle.secondary)
            fleet.callback = self._on_fleet
            v.add_item(fleet)
        return v

    async def render(self, *, content=None):
        """Update the board in place - one message, edited each turn (no re-posting), with the
        current player @mentioned in the content. Sends it the first time."""
        dest = self.thread
        if dest is None:
            return
        embed, view = self._embed(), self._board_view()
        am = discord.AllowedMentions(users=True) if content else discord.AllowedMentions.none()
        if self.message is not None:
            try:
                await self.message.edit(content=content, embed=embed, view=view, allowed_mentions=am)
            except discord.NotFound:
                self.message = None
            except discord.HTTPException:
                logger.debug("battleship board edit failed", exc_info=True)
            else:
                return
        try:
            self.message = await dest.send(content=content, embed=embed, view=view, allowed_mentions=am)
        except discord.HTTPException:
            logger.debug("battleship board send failed", exc_info=True)

    # --- placement ---------------------------------------------------------
    async def _on_place(self, interaction: Interaction):
        p = self._pnum(interaction.user.id)
        if p is None:
            await interaction.response.send_message("This isn't your game.", ephemeral=True)
            return
        if self.ready[p]:
            await interaction.response.send_message(
                "Your fleet's locked in - waiting for your opponent.", ephemeral=True)
            return
        await interaction.response.send_message(view=self._placement_layout(p), ephemeral=True)

    def _placement_layout(self, p):
        view = discord.ui.LayoutView(timeout=_forfeit_seconds())
        box = discord.ui.Container(accent_colour=ACCENT)
        nxt = self._next_ship(p)
        orient = "Horizontal ↔️" if self.place_orient[p] == "h" else "Vertical ↕️"
        if nxt is not None:
            box.add_item(discord.ui.TextDisplay(
                f"## \U0001f6a2 Place your fleet\nNext: **{SHIP_NAMES.get(nxt, 'ship')} ({nxt})** "
                f"— **{orient}**\n-# Tap a square for the bow; it extends right (↔️) or down (↕️)."))
        else:
            box.add_item(discord.ui.TextDisplay(
                "## \U0001f6a2 Fleet ready\nAll ships placed - hit **Ready**, or **Undo** to adjust."))
        view.add_item(box)
        occupied = self._ship_cells(p)
        for r in range(ROWS):
            row = discord.ui.ActionRow()
            for c in range(COLS):
                if (r, c) in occupied:
                    b = discord.ui.Button(emoji=SHIP, style=discord.ButtonStyle.success, disabled=True)
                elif nxt is not None:
                    b = discord.ui.Button(emoji=UNKNOWN, style=discord.ButtonStyle.secondary)
                    b.callback = self._make_place_cb(p, (r, c))
                else:
                    b = discord.ui.Button(emoji=WATER, style=discord.ButtonStyle.secondary, disabled=True)
                row.add_item(b)
            view.add_item(row)
        controls = discord.ui.ActionRow()
        rotate = discord.ui.Button(label="Rotate", emoji="\U0001f504", style=discord.ButtonStyle.primary)
        rotate.callback = self._make_orient_cb(p)
        controls.add_item(rotate)
        undo = discord.ui.Button(label="Undo", emoji="↩️", style=discord.ButtonStyle.secondary,
                                 disabled=not self.fleet[p])
        undo.callback = self._make_undo_cb(p)
        controls.add_item(undo)
        auto = discord.ui.Button(label="Auto", emoji="\U0001f500", style=discord.ButtonStyle.secondary,
                                 disabled=nxt is None)
        auto.callback = self._make_auto_cb(p)
        controls.add_item(auto)
        ready = discord.ui.Button(label="Ready", emoji="✅", style=discord.ButtonStyle.success,
                                  disabled=nxt is not None)
        ready.callback = self._make_ready_cb(p)
        controls.add_item(ready)
        view.add_item(controls)
        return view

    def _make_place_cb(self, p, cell):
        async def cb(interaction: Interaction):
            if self._pnum(interaction.user.id) != p or self.ready[p] or self.game_over:
                await interaction.response.defer()
                return
            if self._try_place(p, *cell):
                await interaction.response.edit_message(view=self._placement_layout(p))
            else:
                await interaction.response.send_message(
                    "That ship won't fit there - try another square or **Rotate**.", ephemeral=True)
        return cb

    def _make_orient_cb(self, p):
        async def cb(interaction: Interaction):
            if self._pnum(interaction.user.id) != p or self.ready[p]:
                await interaction.response.defer()
                return
            self.place_orient[p] = "v" if self.place_orient[p] == "h" else "h"
            await interaction.response.edit_message(view=self._placement_layout(p))
        return cb

    def _make_undo_cb(self, p):
        async def cb(interaction: Interaction):
            if self._pnum(interaction.user.id) != p or self.ready[p]:
                await interaction.response.defer()
                return
            if self.fleet[p]:
                self.fleet[p].pop()
            await interaction.response.edit_message(view=self._placement_layout(p))
        return cb

    def _make_auto_cb(self, p):
        async def cb(interaction: Interaction):
            if self._pnum(interaction.user.id) != p or self.ready[p]:
                await interaction.response.defer()
                return
            self._auto_place_rest(p)
            await interaction.response.edit_message(view=self._placement_layout(p))
        return cb

    def _make_ready_cb(self, p):
        async def cb(interaction: Interaction):
            if self._pnum(interaction.user.id) != p or self.ready[p] or self.game_over:
                await interaction.response.defer()
                return
            if self._next_ship(p) is not None:
                await interaction.response.send_message(
                    "Place all your ships first (or tap **Auto**).", ephemeral=True)
                return
            self.ready[p] = True
            done = discord.ui.LayoutView(timeout=180)
            dbox = discord.ui.Container(accent_colour=discord.Colour(0x2ECC71))
            dbox.add_item(discord.ui.TextDisplay("✅ **Fleet locked in** - waiting for your opponent."))
            done.add_item(dbox)
            await interaction.response.edit_message(view=done)
            await self.after_ready()
        return cb

    async def after_ready(self):
        async with self._lock:
            both = self.phase == "placing" and self.ready[1] and self.ready[2]
            if both:
                self.phase = "firing"
                self._restart_timer()
        if both:
            await self.render(content=f"\U0001f3af <@{self._uid(self.turn)}> - both fleets ready, "
                                      "you fire first!")
        else:
            await self.render()

    # --- firing ------------------------------------------------------------
    async def _on_fire_open(self, interaction: Interaction):
        p = self._pnum(interaction.user.id)
        if p is None:
            await interaction.response.send_message("This isn't your game.", ephemeral=True)
            return
        if self.phase != "firing" or self.game_over:
            await interaction.response.defer()
            return
        await interaction.response.send_message(view=self._fire_view(p), ephemeral=True)
        self.fire_iaction[p] = interaction

    async def _on_fleet(self, interaction: Interaction):
        p = self._pnum(interaction.user.id)
        if p is None:
            await interaction.response.send_message("This isn't your game.", ephemeral=True)
            return
        await interaction.response.send_message(
            content=f"## \U0001f6a2 Your fleet\n{self._fleet_grid(p)}", ephemeral=True)

    def _fire_view(self, shooter):
        active = (self.phase == "firing" and not self.game_over and self.turn == shooter)
        view = discord.ui.LayoutView(timeout=None)
        box = discord.ui.Container(accent_colour=ACCENT)
        if self.game_over:
            header = "\U0001f6a2 **Game over.**"
        elif active:
            header = "\U0001f3af **Your turn - tap an enemy square to fire.**"
        else:
            header = f"⏳ **Waiting for {self._name(self._opp(shooter))} to fire...**"
        box.add_item(discord.ui.TextDisplay(header))
        view.add_item(box)
        shots = self.shots[shooter]
        sunk = self._sunk_in(self.fleet[self._opp(shooter)], shots)
        for r in range(ROWS):
            row = discord.ui.ActionRow()
            for c in range(COLS):
                v = shots.get((r, c))
                if v == "hit":
                    b = discord.ui.Button(emoji=(SUNK if (r, c) in sunk else HIT),
                                          style=discord.ButtonStyle.danger, disabled=True)
                elif v == "miss":
                    b = discord.ui.Button(emoji=MISS, style=discord.ButtonStyle.secondary, disabled=True)
                elif active:
                    b = discord.ui.Button(emoji=UNKNOWN, style=discord.ButtonStyle.primary)
                    b.callback = self._make_fire_cb((r, c))
                else:
                    b = discord.ui.Button(emoji=UNKNOWN, style=discord.ButtonStyle.secondary, disabled=True)
                row.add_item(b)
            view.add_item(row)
        return view

    def _make_fire_cb(self, cell):
        async def cb(interaction: Interaction):
            await self._do_fire(interaction, cell)
        return cb

    async def _do_fire(self, interaction: Interaction, cell):
        p = self._pnum(interaction.user.id)
        if (p is None or self.phase != "firing" or self.game_over or p != self.turn
                or cell in self.shots.get(p, {})):
            try:
                await interaction.response.defer()
            except discord.HTTPException:
                pass
            return
        try:
            await interaction.response.defer()      # ack fast; panels/board update via the token below
        except discord.NotFound:
            return
        self.fire_iaction[p] = interaction
        async with self._lock:
            if self.game_over or p != self.turn or cell in self.shots[p]:
                return
            _result, sunk, won = self._fire(p, cell)
            self.last = (p, cell, _result, sunk)
            if won:
                await self._finish(winner=p)            # also refreshes both panels + board
            else:
                self.turn = self._opp(p)
                self._restart_timer()
        if not self.game_over:
            await self.render(content=f"\U0001f3af <@{self._uid(self.turn)}> - your turn to fire.")
            await self._refresh_fire_panel(p)            # shooter -> "waiting"
            await self._refresh_fire_panel(self.turn)    # next player -> active (if their panel is open)

    async def _refresh_fire_panel(self, p):
        """Edit player p's open firing ephemeral to match the current state (best-effort - if
        the interaction token has lapsed it's dropped and they just reopen via the board)."""
        ia = self.fire_iaction.get(p)
        if ia is None:
            return
        try:
            await ia.edit_original_response(view=self._fire_view(p))
        except discord.HTTPException:
            self.fire_iaction[p] = None

    # --- forfeit clock -----------------------------------------------------
    def start(self):
        self._restart_timer()

    def _restart_timer(self):
        self._cancel_timer()
        self._timer_task = asyncio.create_task(self._forfeit_after())

    def _cancel_timer(self):
        t, self._timer_task = self._timer_task, None
        if t is not None and not t.done():
            t.cancel()

    async def _forfeit_after(self):
        try:
            await asyncio.sleep(_forfeit_seconds())
        except asyncio.CancelledError:
            return
        async with self._lock:
            if self.game_over:
                return
            self._timer_task = None
            if self.phase == "placing":
                if self.ready[1] and not self.ready[2]:
                    await self._finish(winner=1, forfeit=True)
                elif self.ready[2] and not self.ready[1]:
                    await self._finish(winner=2, forfeit=True)
                else:
                    await self._void("Neither captain set up their fleet in time")
            else:
                await self._finish(winner=self._opp(self.turn), forfeit=True)

    # --- settlement --------------------------------------------------------
    async def _finish(self, *, winner, forfeit=False):
        if self.game_over:
            return
        self.game_over = True
        self.phase = "over"
        self._cancel_timer()
        if self.escrow_key is not None:
            delete_state(self.escrow_key)            # delete-before-credit: never double-pay on reboot
        credit_from_bank(self._uid(winner), self.stake * 2, "Battleship win")
        try:
            from lib.economy import pvp_stats
            pvp_stats.record_result("battleship", self._uid(winner), self._uid(self._opp(winner)),
                                    self.stake, "forfeit" if forfeit else "win")
        except Exception:
            logger.error("battleship stats hook failed", exc_info=True)
        await self._render_final(winner=winner, forfeit=forfeit)
        for pl in (1, 2):
            await self._refresh_fire_panel(pl)
        await self._announce_parent(winner=winner, forfeit=forfeit)
        await self._close_thread()

    async def _void(self, reason):
        if self.game_over:
            return
        self.game_over = True
        self.phase = "over"
        self._cancel_timer()
        if self.escrow_key is not None:
            delete_state(self.escrow_key)
        credit_from_bank(self.p1_id, self.stake, "Battleship void refund")
        credit_from_bank(self.p2_id, self.stake, "Battleship void refund")
        try:
            e = discord.Embed(
                title="\U0001f6a2 Battleship - Voided",
                description=f"{reason}. Both **{self.stake:,}** UKP stakes have been refunded.",
                colour=0x95A5A6)
            if self.message is not None:
                await self.message.edit(content=None, embed=e, view=None)
        except discord.HTTPException:
            pass
        for pl in (1, 2):
            await self._refresh_fire_panel(pl)
        await self._close_thread()

    async def _render_final(self, *, winner, forfeit):
        try:
            e = self._embed(final=True, winner=winner, forfeit=forfeit)
            if self.message is not None:
                await self.message.edit(content=None, embed=e, view=None)
            elif self.thread is not None:
                await self.thread.send(embed=e)
        except discord.HTTPException:
            logger.debug("battleship final render failed", exc_info=True)

    async def _announce_parent(self, *, winner, forfeit):
        """Post the final board (both fleets revealed + result) to the parent channel, so
        there's a full record outside the (archived) thread."""
        if self.client is None:
            return
        try:
            parent = self.client.get_channel(int(self.channel_id))
            if parent is not None:
                await parent.send(embed=self._embed(final=True, winner=winner, forfeit=forfeit),
                                  allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            logger.debug("battleship parent announce failed", exc_info=True)

    async def _close_thread(self):
        if self.thread is None:
            return
        ACTIVE_GAME_THREADS.discard(self.thread.id)
        try:
            await self.thread.edit(archived=True, locked=True)
        except discord.HTTPException:
            try:
                await self.thread.delete()
            except discord.HTTPException:
                pass


# ---------------------------------------------------------------------------
# Challenge (pre-accept)
# ---------------------------------------------------------------------------
class BattleshipChallengeView(discord.ui.View):
    def __init__(self, challenger: Member, opponent: Member, amount: int):
        super().__init__(timeout=getattr(config, "BATTLESHIP_ACCEPT_SECONDS", 300))
        self.challenger = challenger
        self.opponent = opponent
        self.amount = amount
        self.resolved = False
        self.message = None

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept(self, interaction: Interaction, button: discord.ui.Button):
        if interaction.user.id != self.opponent.id:
            await interaction.response.send_message(
                "Only the challenged player can accept this.", ephemeral=True)
            return
        if self.resolved:
            await interaction.response.defer()
            return
        if getattr(interaction.client, "maintenance_mode", False):
            await interaction.response.send_message(
                "\U0001f527 The bot's restarting for an update - try again in a minute.", ephemeral=True)
            return
        if get_bb(self.challenger.id) < self.amount:
            await interaction.response.send_message(
                f"{self.challenger.display_name} no longer has {self.amount:,} UKPence.", ephemeral=True)
            return
        if get_bb(self.opponent.id) < self.amount:
            await interaction.response.send_message(
                f"You no longer have {self.amount:,} UKPence.", ephemeral=True)
            return
        self.resolved = True
        if not remove_bb(self.challenger.id, self.amount, reason="Battleship stake"):
            self.resolved = False
            await interaction.response.send_message(
                f"{self.challenger.display_name} can't cover the stake.", ephemeral=True)
            return
        if not remove_bb(self.opponent.id, self.amount, reason="Battleship stake"):
            credit_from_bank(self.challenger.id, self.amount, "Battleship stake refund")
            self.resolved = False
            await interaction.response.send_message("You can't cover the stake.", ephemeral=True)
            return

        await interaction.response.defer()
        p1_name = discord.utils.escape_markdown(self.challenger.display_name)
        p2_name = discord.utils.escape_markdown(self.opponent.display_name)
        game = BattleshipGame(self.challenger.id, p1_name, self.opponent.id, p2_name,
                              self.amount, interaction.channel_id)
        game.client = interaction.client
        game.escrow_key = interaction.message.id

        try:
            thread = await interaction.channel.create_thread(
                name=f"\U0001f6a2 Battleship: {self.challenger.display_name} vs {self.opponent.display_name}"[:90],
                type=discord.ChannelType.public_thread, auto_archive_duration=60)
            game.thread = thread
            ACTIVE_GAME_THREADS.add(thread.id)       # pseudo-lock: bin anyone else's messages here
            for m in (self.challenger, self.opponent):
                try:
                    await thread.add_user(m)
                except discord.HTTPException:
                    pass
        except discord.HTTPException:
            credit_from_bank(self.challenger.id, self.amount, "Battleship void refund (no thread)")
            credit_from_bank(self.opponent.id, self.amount, "Battleship void refund (no thread)")
            self.resolved = False
            await interaction.followup.send(
                "Couldn't open a game thread here (I need the **Create Public Threads** permission).",
                ephemeral=True)
            return

        save_state(game.escrow_key, {
            "type": "battleship",
            "p1_id": self.challenger.id, "p2_id": self.opponent.id,
            "stake": self.amount, "channel_id": interaction.channel_id,
            "thread_id": game.thread.id,
        })
        await game.render()
        game.start()
        try:
            e = interaction.message.embeds[0]
            e.colour = 0x2ECC71
            e.title = "\U0001f6a2 Battleship - Game On"
            e.description = (f"{self.challenger.mention} vs {self.opponent.mention} - "
                             f"play in {game.thread.mention}!")
            await interaction.message.edit(embed=e, view=None)
        except discord.HTTPException:
            pass
        self.stop()

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: Interaction, button: discord.ui.Button):
        if interaction.user.id not in (self.opponent.id, self.challenger.id):
            await interaction.response.send_message(
                "Only the two players can decline this.", ephemeral=True)
            return
        if self.resolved:
            await interaction.response.defer()
            return
        self.resolved = True
        for child in self.children:
            child.disabled = True
        e = interaction.message.embeds[0]
        e.colour = 0xE74C3C
        if interaction.user.id == self.challenger.id:
            e.title = "Battleship - Challenge Retracted"
            e.description = f"{self.challenger.mention} retracted their challenge."
        else:
            e.title = "Battleship - Challenge Declined"
            e.description = f"{self.opponent.mention} declined {self.challenger.mention}'s challenge."
        await interaction.response.edit_message(embed=e, view=self)
        self.stop()

    @discord.ui.button(label="\U0001f4dc Rules", style=discord.ButtonStyle.secondary)
    async def rules(self, interaction: Interaction, button: discord.ui.Button):
        await interaction.response.send_message(rules_text(), ephemeral=True)

    async def on_timeout(self):
        if self.resolved or self.message is None:
            return
        for child in self.children:
            child.disabled = True
        try:
            e = self.message.embeds[0]
            e.colour = 0x95A5A6
            e.title = "Battleship - Challenge Expired"
            e.description = (f"{self.challenger.mention}'s challenge to {self.opponent.mention} "
                             "expired. No UKPence was staked.")
            await self.message.edit(embed=e, view=self)
        except Exception:
            logger.debug("battleship challenge timeout edit failed", exc_info=True)


# ---------------------------------------------------------------------------
# Command entry
# ---------------------------------------------------------------------------
async def handle_battleship_command(interaction: Interaction, opponent: Member, bet: int):
    if await reject_if_maintenance(interaction):
        return
    if not getattr(config, "BATTLESHIP_ENABLED", True):
        await interaction.response.send_message("Battleship is currently disabled.", ephemeral=True)
        return
    if opponent.bot or opponent.id == interaction.user.id:
        await interaction.response.send_message(
            "Pick a real opponent - you can't play yourself or a bot.", ephemeral=True)
        return
    min_bet = getattr(config, "BATTLESHIP_MIN_BET", 5)
    max_bet = getattr(config, "BATTLESHIP_MAX_BET", 5000)
    if bet < min_bet or bet > max_bet:
        await interaction.response.send_message(
            f"The stake must be between {min_bet:,} and {max_bet:,} UKPence.", ephemeral=True)
        return
    if get_bb(interaction.user.id) < bet:
        await interaction.response.send_message(
            f"You don't have {bet:,} UKPence to stake.", ephemeral=True)
        return
    if get_bb(opponent.id) < bet:
        await interaction.response.send_message(
            f"{opponent.display_name} doesn't have {bet:,} UKPence to match your stake.", ephemeral=True)
        return

    accept_min = getattr(config, "BATTLESHIP_ACCEPT_SECONDS", 300) // 60
    embed = discord.Embed(
        title="\U0001f6a2 Battleship Challenge",
        description=(f"{interaction.user.mention} has challenged {opponent.mention} to "
                     "**Battleship**!\n\nSink the enemy fleet to take the pot. Winner takes all."),
        colour=0xF1C40F)
    embed.add_field(name="Stake", value=f"{bet:,} UKPence each", inline=True)
    embed.add_field(name="Pot", value=f"{bet * 2:,} UKPence", inline=True)
    embed.set_footer(text=f"{opponent.display_name} has {accept_min} min to accept · "
                          "tap Rules for how it works")

    view = BattleshipChallengeView(interaction.user, opponent, bet)
    await interaction.response.send_message(
        content=opponent.mention, embed=embed, view=view,
        allowed_mentions=discord.AllowedMentions(users=[opponent]))
    view.message = await interaction.original_response()


# ---------------------------------------------------------------------------
# Restart recovery
# ---------------------------------------------------------------------------
def reattach_battleship_view(client, key, value):
    """A game was mid-match when the bot restarted. The live board/timer can't be rebuilt, so
    VOID it: refund both stakes, prune the record, clean up the thread."""
    try:
        p1 = int(value["p1_id"])
        p2 = int(value["p2_id"])
        stake = int(value["stake"])
    except Exception as e:
        logger.error(f"Pruning malformed battleship entry {key}: {e}")
        delete_state(key)
        return
    delete_state(key)
    credit_from_bank(p1, stake, "Battleship void refund (bot restart)")
    credit_from_bank(p2, stake, "Battleship void refund (bot restart)")
    thread_id = value.get("thread_id")

    async def _cleanup():
        try:
            if thread_id:
                thread = client.get_channel(int(thread_id))
                if thread is not None:
                    try:
                        await thread.send(
                            f"\U0001f6a2 This game was voided (the bot restarted mid-match). "
                            f"Both **{stake:,}** UKP stakes were refunded.")
                    except discord.HTTPException:
                        pass
                    try:
                        await thread.edit(archived=True, locked=True)
                    except discord.HTTPException:
                        pass
        except Exception:
            logger.debug("battleship void cleanup failed", exc_info=True)

    try:
        asyncio.create_task(_cleanup())
    except RuntimeError:
        pass
