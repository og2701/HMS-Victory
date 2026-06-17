"""Battleship - a 1v1 player-vs-player wager for UKPence (winner takes the pot).

Flow:
  /battleship @rival <bet> posts a challenge (Accept / Decline / Rules). Stakes are only
  taken once the rival ACCEPTS - then both buy-ins go into the house bank and the winner is
  paid the whole pot back out of it (the bank nets zero; the fixed UKP supply is conserved).
  A void (restart / nobody sets up in time) refunds both.

  On accept the game opens its own LOCKED thread (only the bot posts), so the fog-of-war
  board sits still and each player's ephemeral (firing grid / their own fleet) reads right
  beneath it without chat burying anything. Both players are added to the thread; banter
  goes in the parent channel.

  Board: a 4x6 ocean (mobile-friendly, like Mines - phones wrap ~4 buttons/row). Fleets are
  placed RANDOMLY with a Shuffle/Ready step. Shot results are public so the board shows both
  players' fog grids; only your own *unhit* ship layout is private (a "My fleet" ephemeral).
  Firing is a 4x6 button grid in an ephemeral - tap an enemy square to bomb it. Each turn has
  a forfeit clock; miss it and your opponent takes the pot.

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

UNKNOWN = "⬛"   # ⬛ un-fired square (fog)
HIT = "\U0001f4a5"   # 💥
MISS = "\U0001f30a"  # 🌊
WATER = "\U0001f7e6" # 🟦 own untouched water
SHIP = "\U0001f6a2"  # 🚢 your intact ship


def _forfeit_seconds():
    return getattr(config, "BATTLESHIP_FORFEIT_SECONDS", 600)


def rules_text() -> str:
    mins = _forfeit_seconds() // 60
    return (
        "## \U0001f6a2 Battleship - Rules\n"
        f"Two captains, a **{COLS}x{ROWS}** ocean each. Your fleet ({', '.join(map(str, SHIPS))}-cell "
        "ships) is placed **randomly** - hit **Shuffle** until you like it, then **Ready**.\n\n"
        "- Take turns firing at the enemy grid (tap a square). \U0001f4a5 = hit, \U0001f30a = miss. "
        "Sink every enemy ship to win.\n"
        "- Shot results are public (both fog grids show on the board); only your own *unhit* "
        "ships are hidden - check them with **My fleet**.\n"
        "- Both players stake the same; **winner takes the whole pot** (2x the stake).\n"
        f"- ⏳ **Forfeit timer:** {mins} minutes per move (and to set up your fleet). "
        "Miss it and your opponent takes the pot.\n"
        "- The game runs in its own locked thread - chat in the main channel."
    )


def _random_fleet():
    """Place SHIPS on a COLS x ROWS grid with no overlaps. Returns a list of ships, each
    a dict {size, cells:set[(r,c)]}. Retries the whole layout on the rare dead end."""
    for _ in range(200):
        occupied, ships, ok = set(), [], True
        for size in SHIPS:
            placed = False
            for _ in range(100):
                if random.choice((True, False)):                     # horizontal
                    r = random.randrange(ROWS)
                    c = random.randrange(COLS - size + 1)
                    cells = {(r, c + i) for i in range(size)}
                else:                                                # vertical
                    r = random.randrange(ROWS - size + 1)
                    c = random.randrange(COLS)
                    cells = {(r + i, c) for i in range(size)}
                if cells & occupied:
                    continue
                occupied |= cells
                ships.append({"size": size, "cells": cells})
                placed = True
                break
            if not placed:
                ok = False
                break
        if ok:
            return ships
    raise RuntimeError("could not place battleship fleet")


class BattleshipGame:
    """One game's state + its rendering/flow. Not a View itself - the board's button view is
    rebuilt fresh each render (so it survives the thread 'bump'), mirroring the poker table."""

    def __init__(self, p1_id, p1_name, p2_id, p2_name, stake, channel_id):
        self.p1_id, self.p2_id = int(p1_id), int(p2_id)
        self.p1_name, self.p2_name = p1_name, p2_name
        self.stake = int(stake)
        self.channel_id = channel_id
        self.fleet = {1: _random_fleet(), 2: _random_fleet()}
        self.shots = {1: {}, 2: {}}      # shots[p][(r,c)] = 'hit'|'miss'  (p's shots on the OTHER grid)
        self.ready = {1: False, 2: False}
        self.phase = "placing"           # placing | firing | over
        self.turn = random.choice([1, 2])
        self.last = None                 # (shooter, (r,c), result, sunk_size) for the status line
        self.thread = None
        self.message = None              # board message (in the thread)
        self.client = None
        self.escrow_key = None           # challenge message id - the escrow record's key
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

    # --- rendering ---------------------------------------------------------
    def _fog_grid(self, shooter):
        """shooter's shots on the opponent (public knowledge - no ships shown)."""
        out = []
        for r in range(ROWS):
            row = ""
            for c in range(COLS):
                v = self.shots[shooter].get((r, c))
                row += HIT if v == "hit" else MISS if v == "miss" else UNKNOWN
            out.append(row)
        return "\n".join(out)

    def _fleet_grid(self, p):
        """p's own ocean: their ships + the opponent's hits/misses on them (private)."""
        ship_cells = self._ship_cells(p)
        incoming = self.shots[self._opp(p)]   # opponent's shots on p
        out = []
        for r in range(ROWS):
            row = ""
            for c in range(COLS):
                cell = (r, c)
                if cell in ship_cells:
                    row += HIT if cell in incoming else SHIP
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
                     "-# Tap **Place fleet** below · Shuffle until happy, then Ready."]
            return discord.Embed(title=title, description="\n".join(lines), colour=0x3498DB)

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
            # Reveal both fleets at the end.
            lines += ["", f"**{self.p1_name}'s fleet**", self._fleet_grid(1),
                      "", f"**{self.p2_name}'s fleet**", self._fleet_grid(2)]
            e = discord.Embed(title=title, description="\n".join(lines), colour=colour)
            e.set_footer(text="Battleship · winner takes the pot")
            return e

        # firing
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
                lines.append(f"\U0001f4a5 **{who}** hit at {coord}!")
        cur = self._name(self.turn)
        lines.append(f"➡️  **{cur}'s turn to fire**")
        mins = _forfeit_seconds() // 60
        e = discord.Embed(title=title, description="\n".join(lines), colour=0x3498DB)
        e.set_footer(text=f"⏳ {mins} min to fire, or you forfeit the pot")
        return e

    def _board_view(self):
        v = discord.ui.View(timeout=None)
        if self.phase == "placing":
            b = discord.ui.Button(label="Place fleet", emoji="\U0001f6a2",
                                  style=discord.ButtonStyle.primary)
            b.callback = self._on_place
            v.add_item(b)
        elif self.phase == "firing":
            fire = discord.ui.Button(label="Fire", emoji="\U0001f3af",
                                     style=discord.ButtonStyle.danger)
            fire.callback = self._on_fire_open
            v.add_item(fire)
            fleet = discord.ui.Button(label="My fleet", emoji="\U0001f6a2",
                                      style=discord.ButtonStyle.secondary)
            fleet.callback = self._on_fleet
            v.add_item(fleet)
        return v

    async def render(self, *, bump=False, content=None):
        """Draw the board in the thread. bump=True re-posts at the bottom (with the actor's
        @mention in `content` to ping them) so the board stays put and pings the next player;
        the old board is deleted. Posts the new one BEFORE deleting the old, so a failed send
        (e.g. a locked thread the bot can't post in) falls back to an in-place edit instead of
        losing the board."""
        dest = self.thread
        if dest is None:
            return
        embed = self._embed()
        view = self._board_view()
        am = discord.AllowedMentions(users=True) if content else discord.AllowedMentions.none()
        if bump:
            try:
                new = await dest.send(content=content, embed=embed, view=view, allowed_mentions=am)
            except discord.HTTPException:
                if self.message is not None:
                    try:
                        await self.message.edit(content=content, embed=embed, view=view,
                                                allowed_mentions=am)
                    except discord.HTTPException:
                        logger.debug("battleship bump fallback edit failed", exc_info=True)
                return
            old, self.message = self.message, new
            if old is not None:
                try:
                    await old.delete()
                except discord.HTTPException:
                    pass
            return
        if self.message is not None:
            try:
                await self.message.edit(content=content, embed=embed, view=view, allowed_mentions=am)
                return
            except discord.NotFound:
                self.message = None
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
        await interaction.response.send_message(
            content=self._placement_text(p), view=PlacementView(self, p), ephemeral=True)

    def _placement_text(self, p):
        return (f"## \U0001f6a2 Your fleet\n{self._fleet_grid(p)}\n\n"
                "\U0001f500 **Shuffle** until you're happy, then ✅ **Ready**.")

    async def after_ready(self):
        """Called after a player marks Ready. Starts the battle once both are in."""
        async with self._lock:
            both = self.phase == "placing" and self.ready[1] and self.ready[2]
            if both:
                self.phase = "firing"
                self._restart_timer()
        if both:
            await self.render(bump=True,
                              content=f"\U0001f3af <@{self._uid(self.turn)}> - both fleets ready, "
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
        if p != self.turn:
            await interaction.response.send_message("It's not your turn to fire.", ephemeral=True)
            return
        await interaction.response.send_message(view=self._fire_view(p), ephemeral=True)

    async def _on_fleet(self, interaction: Interaction):
        p = self._pnum(interaction.user.id)
        if p is None:
            await interaction.response.send_message("This isn't your game.", ephemeral=True)
            return
        await interaction.response.send_message(
            content=f"## \U0001f6a2 Your fleet\n{self._fleet_grid(p)}", ephemeral=True)

    def _fire_view(self, shooter):
        view = discord.ui.LayoutView(timeout=_forfeit_seconds())
        box = discord.ui.Container(accent_colour=discord.Colour(0x3498DB))
        box.add_item(discord.ui.TextDisplay("\U0001f3af **Tap an enemy square to fire.**"))
        view.add_item(box)
        shots = self.shots[shooter]
        for r in range(ROWS):
            row = discord.ui.ActionRow()
            for c in range(COLS):
                v = shots.get((r, c))
                if v == "hit":
                    b = discord.ui.Button(emoji=HIT, style=discord.ButtonStyle.danger, disabled=True)
                elif v == "miss":
                    b = discord.ui.Button(emoji=MISS, style=discord.ButtonStyle.secondary, disabled=True)
                else:
                    b = discord.ui.Button(emoji=UNKNOWN, style=discord.ButtonStyle.primary)
                    b.callback = self._make_fire_cb((r, c))
                row.add_item(b)
            view.add_item(row)
        return view

    def _make_fire_cb(self, cell):
        async def cb(interaction: Interaction):
            await self._do_fire(interaction, cell)
        return cb

    async def _do_fire(self, interaction: Interaction, cell):
        p = self._pnum(interaction.user.id)
        if p is None or self.phase != "firing" or self.game_over or p != self.turn:
            try:
                await interaction.response.defer()
            except discord.HTTPException:
                pass
            return
        if cell in self.shots[p]:
            await interaction.response.send_message("You've already fired there.", ephemeral=True)
            return
        try:
            await interaction.response.defer()       # ack fast (the board edit comes via the bot token)
        except discord.NotFound:
            return
        async with self._lock:
            if self.game_over or p != self.turn or cell in self.shots[p]:
                return
            _result, sunk_size, won = self._fire(p, cell)
            self.last = (p, cell, _result, sunk_size)
            if won:
                await self._finish(winner=p)
            else:
                self.turn = self._opp(p)
                self._restart_timer()
        try:
            await interaction.delete_original_response()   # clear the firing grid
        except discord.HTTPException:
            pass
        if not self.game_over:
            await self.render(bump=True,
                              content=f"\U0001f3af <@{self._uid(self.turn)}> - your turn to fire.")

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
            delete_state(self.escrow_key)        # delete-before-credit: never double-pay on reboot
        pot = self.stake * 2
        credit_from_bank(self._uid(winner), pot, "Battleship win")
        await self._render_final(winner=winner, forfeit=forfeit)
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
        """A short result in the parent channel so there's a record outside the (archived) thread."""
        if self.client is None:
            return
        try:
            parent = self.client.get_channel(int(self.channel_id))
            if parent is None:
                return
            pot = self.stake * 2
            wname = self._name(winner)
            verb = "by forfeit" if forfeit else "sinks the fleet and"
            await parent.send(
                f"\U0001f6a2 **Battleship:** {wname} {verb} takes the **{pot:,}** UKP pot "
                f"off {self._name(self._opp(winner))}.",
                allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            logger.debug("battleship parent announce failed", exc_info=True)

    async def _close_thread(self):
        if self.thread is None:
            return
        try:
            await self.thread.edit(archived=True, locked=True)
        except discord.HTTPException:
            try:
                await self.thread.delete()
            except discord.HTTPException:
                pass


class PlacementView(discord.ui.View):
    """The per-player ephemeral: shuffle the random fleet, then lock it in."""

    def __init__(self, game: BattleshipGame, p: int):
        super().__init__(timeout=_forfeit_seconds())
        self.game = game
        self.p = p

    @discord.ui.button(label="Shuffle", emoji="\U0001f500", style=discord.ButtonStyle.secondary)
    async def shuffle(self, interaction: Interaction, button: discord.ui.Button):
        if self.game.ready[self.p] or self.game.game_over:
            await interaction.response.defer()
            return
        self.game.fleet[self.p] = _random_fleet()
        await interaction.response.edit_message(content=self.game._placement_text(self.p), view=self)

    @discord.ui.button(label="Ready", emoji="✅", style=discord.ButtonStyle.success)
    async def ready(self, interaction: Interaction, button: discord.ui.Button):
        if self.game.game_over:
            await interaction.response.defer()
            return
        self.game.ready[self.p] = True
        await interaction.response.edit_message(
            content="✅ Fleet locked in - waiting for your opponent.", view=None)
        await self.game.after_ready()
        self.stop()


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

        # Open the game's own thread, pull both players in, and lock it so only the bot posts
        # (players still interact via buttons - locking blocks chat, not interactions).
        try:
            thread = await interaction.channel.create_thread(
                name=f"\U0001f6a2 Battleship: {self.challenger.display_name} vs {self.opponent.display_name}"[:90],
                type=discord.ChannelType.public_thread, auto_archive_duration=60)
            game.thread = thread
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
        await game.render()                          # post the board in the thread
        try:
            await game.thread.edit(locked=True)      # only the bot posts; banter goes in the parent
        except discord.HTTPException:
            logger.debug("battleship thread lock failed (continuing unlocked)", exc_info=True)
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
# Restart recovery (called from event_handlers.reattach_persistent_views)
# ---------------------------------------------------------------------------
def reattach_battleship_view(client, key, value):
    """A game was mid-match when the bot restarted. The live board/timer can't be rebuilt, so
    VOID it: refund both stakes, prune the record, and clean up the thread."""
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
