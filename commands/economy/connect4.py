"""Connect 4 - a 1v1 player-vs-player wager for UKPence (winner takes the pot).

Flow:
  /connect4 @rival <bet>  posts a challenge (Accept / Decline / Rules). Stakes are only
  taken once the rival ACCEPTS - then both buy-ins go into the house bank and the winner
  is paid the whole pot back out of it (the bank nets zero; the fixed UKP supply is
  conserved). A draw refunds both.

  Board: 7 columns x 6 rows rendered as an emoji grid in an embed (no image render, so
  it never blocks the event loop). Players drop discs via column buttons split 4 + 3
  across two rows (Discord caps a button row at 5, and phones wrap ~4). Whose turn it is
  is shown clearly, and each move has a 10-minute forfeit clock - miss it and your
  opponent wins the pot automatically.

  Persistence: only a tiny escrow record (both players + stake) is persisted, keyed by the
  board message id. The live board/timer don't survive a restart, so on boot an in-progress
  game is VOIDED and both stakes refunded (see reattach_connect4_view). The record is
  deleted the instant a game settles, so a leftover entry always means "still owed".
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

COLS = 7
ROWS = 6
NUM_EMOJI = ["1️⃣", "2️⃣", "3️⃣", "4️⃣",
             "5️⃣", "6️⃣", "7️⃣"]
CELL = {0: "⚪", 1: "\U0001F534", 2: "\U0001F7E1"}  # empty / red (P1) / yellow (P2)
P1_DISC, P2_DISC = "\U0001F534", "\U0001F7E1"


def rules_text() -> str:
    forfeit_min = getattr(config, "CONNECT4_FORFEIT_SECONDS", 600) // 60
    return (
        "## \U0001F534\U0001F7E1 Connect 4 - Rules\n"
        "Two players, one board (**7 columns x 6 rows**). Take turns dropping a disc into a "
        "column; it falls to the lowest free slot. First to line up **four in a row** - "
        "horizontally, vertically or diagonally - wins.\n\n"
        "- Both players stake the same amount; **winner takes the whole pot** (2x the stake).\n"
        f"- \U0001F534 the challenger and \U0001F7E1 the accepter - **who moves first is random**.\n"
        "- A full board with no four-in-a-row is a **draw**, and both stakes are refunded.\n"
        f"- ⏳ **Forfeit timer:** you have **{forfeit_min} minutes** to make each move. "
        "Miss it and your opponent wins the pot automatically.\n"
        "- Stakes are only taken **once the challenge is accepted** - decline or let it "
        "expire and nobody pays a thing."
    )


# ---------------------------------------------------------------------------
# The live board
# ---------------------------------------------------------------------------
class Connect4View(discord.ui.View):
    def __init__(self, p1_id, p1_name, p2_id, p2_name, stake, channel_id):
        super().__init__(timeout=None)  # the forfeit clock is managed ourselves, not by the View
        self.p1_id = int(p1_id)
        self.p2_id = int(p2_id)
        self.p1_name = p1_name
        self.p2_name = p2_name
        self.stake = int(stake)
        self.channel_id = channel_id
        self.board = [[0] * COLS for _ in range(ROWS)]
        self.turn = random.choice([1, 2])   # who moves first is random (first-move edge is real)
        self.game_over = False
        self.message = None
        self.client = None              # set on accept; needed to award badges on forfeit
        self._lock = asyncio.Lock()
        self._timer_task = None
        self._sync_buttons()

    # --- board logic -------------------------------------------------------
    def _drop(self, col, player) -> int | None:
        for r in range(ROWS - 1, -1, -1):
            if self.board[r][col] == 0:
                self.board[r][col] = player
                return r
        return None

    def _check_win(self, row, col, player) -> bool:
        for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
            count = 1
            for sign in (1, -1):
                r, c = row + dr * sign, col + dc * sign
                while 0 <= r < ROWS and 0 <= c < COLS and self.board[r][c] == player:
                    count += 1
                    r += dr * sign
                    c += dc * sign
            if count >= 4:
                return True
        return False

    def _is_full(self) -> bool:
        return all(self.board[0][c] != 0 for c in range(COLS))

    def _current_id(self) -> int:
        return self.p1_id if self.turn == 1 else self.p2_id

    # --- rendering ---------------------------------------------------------
    def _board_str(self) -> str:
        header = "".join(NUM_EMOJI)
        grid = "\n".join("".join(CELL[self.board[r][c]] for c in range(COLS)) for r in range(ROWS))
        return f"{header}\n{grid}"

    def _embed(self, *, final=False, winner=None, forfeit=False) -> discord.Embed:
        lines = [f"\U0001F534 **{self.p1_name}**   vs   \U0001F7E1 **{self.p2_name}**",
                 "", self._board_str(), ""]
        if not final:
            cur = self.p1_name if self.turn == 1 else self.p2_name
            disc = P1_DISC if self.turn == 1 else P2_DISC
            lines.append(f"➡️  **{cur}'s turn**  {disc}")
            colour = 0xE74C3C if self.turn == 1 else 0xF1C40F
            mins = getattr(config, "CONNECT4_FORFEIT_SECONDS", 600) // 60
            footer = f"⏳ {mins} min to move, or you forfeit the pot"
        elif winner is None:
            lines.append(f"\U0001F91D **Draw!** The board's full - both **{self.stake:,}** "
                         "UKP stakes have been refunded.")
            colour = 0x95A5A6
            footer = "Connect 4 · winner takes the pot"
        else:
            wname = self.p1_name if winner == 1 else self.p2_name
            wdisc = P1_DISC if winner == 1 else P2_DISC
            pot = self.stake * 2
            if forfeit:
                lname = self.p2_name if winner == 1 else self.p1_name
                lines.append(f"⏳ **{lname}** ran out of time - {wdisc} **{wname}** takes "
                             f"the **{pot:,}** UKP pot by forfeit!")
            else:
                lines.append(f"\U0001F3C6 {wdisc} **{wname}** connects four and wins the "
                             f"**{pot:,}** UKP pot!")
            colour = 0x2ECC71
            footer = "Connect 4 · winner takes the pot"
        e = discord.Embed(title="\U0001F534\U0001F7E1 Connect 4", description="\n".join(lines),
                          colour=colour)
        e.set_footer(text=footer)
        return e

    def _sync_buttons(self):
        """Rebuild the column buttons: 1-4 on the top row, 5-7 below. Full columns are
        disabled. No buttons once the game is over."""
        self.clear_items()
        if self.game_over:
            return
        for col in range(COLS):
            full = self.board[0][col] != 0
            btn = discord.ui.Button(label=str(col + 1), style=discord.ButtonStyle.primary,
                                    row=0 if col < 4 else 1, disabled=full)
            btn.callback = self._column_cb(col)
            self.add_item(btn)

    def _column_cb(self, col):
        async def cb(interaction: Interaction):
            await self._on_column(interaction, col)
        return cb

    async def _safe_edit(self, interaction, *, embed, view):
        """Edit the board, surviving an expired interaction token (fall back to a direct
        message edit via the bot token)."""
        try:
            await interaction.response.edit_message(embed=embed, view=view)
        except (discord.NotFound, discord.InteractionResponded):
            try:
                if self.message is not None:
                    await self.message.edit(embed=embed, view=view)
            except discord.HTTPException:
                logger.debug("connect4 fallback edit failed", exc_info=True)
        except discord.HTTPException:
            logger.debug("connect4 board edit failed", exc_info=True)

    # --- forfeit clock -----------------------------------------------------
    def start(self):
        self._restart_timer()

    def _restart_timer(self):
        self._cancel_timer()
        self._timer_task = asyncio.create_task(self._forfeit_after())

    def _cancel_timer(self):
        t = self._timer_task
        self._timer_task = None
        if t is not None and not t.done():
            t.cancel()

    async def _forfeit_after(self):
        try:
            await asyncio.sleep(getattr(config, "CONNECT4_FORFEIT_SECONDS", 600))
        except asyncio.CancelledError:
            return
        async with self._lock:
            if self.game_over:
                return
            # We're past the sleep - clear the handle so _finish's _cancel_timer doesn't
            # cancel this very task mid-settlement.
            self._timer_task = None
            winner = 2 if self.turn == 1 else 1  # the player to move ran down the clock
            await self._finish(None, winner=winner, forfeit=True)

    # --- moves + settlement ------------------------------------------------
    async def _on_column(self, interaction: Interaction, col: int):
        if self.game_over:
            await interaction.response.defer()
            return
        uid = interaction.user.id
        if uid not in (self.p1_id, self.p2_id):
            await interaction.response.send_message(
                "This isn't your game - start your own with `/connect4`.", ephemeral=True)
            return
        if uid != self._current_id():
            await interaction.response.send_message("It's not your turn yet.", ephemeral=True)
            return
        async with self._lock:
            if self.game_over or uid != self._current_id():
                await interaction.response.defer()
                return
            row = self._drop(col, self.turn)
            if row is None:
                await interaction.response.send_message(
                    "That column's full - pick another.", ephemeral=True)
                return
            if self._check_win(row, col, self.turn):
                await self._finish(interaction, winner=self.turn)
                return
            if self._is_full():
                await self._finish(interaction, winner=None)
                return
            self.turn = 2 if self.turn == 1 else 1
            self._restart_timer()
            self._sync_buttons()
            await self._safe_edit(interaction, embed=self._embed(), view=self)

    async def _finish(self, interaction, *, winner, forfeit=False):
        """Settle the game. winner: 1 | 2 | None(draw). Pays out of the bank after dropping
        the escrow record (delete-before-credit, so a crash can never double-pay on reboot)."""
        self.game_over = True
        self._cancel_timer()
        pot = self.stake * 2
        if self.message is not None:
            delete_state(self.message.id)
        if winner is None:
            credit_from_bank(self.p1_id, self.stake, "Connect 4 draw refund")
            credit_from_bank(self.p2_id, self.stake, "Connect 4 draw refund")
        else:
            wid = self.p1_id if winner == 1 else self.p2_id
            lid = self.p2_id if winner == 1 else self.p1_id
            credit_from_bank(wid, pot, "Connect 4 win")
        # Log the match (unified PvP stats) and award the winner their badges (best-effort).
        try:
            from lib.economy import pvp_stats
            from lib.economy.game_badges import award_connect4_badges
            if winner is None:
                pvp_stats.record_result("connect4", None, None, self.stake, "draw")
            else:
                pvp_stats.record_result("connect4", wid, lid, self.stake,
                                        "forfeit" if forfeit else "win")
                if self.client is not None:
                    await award_connect4_badges(self.client, wid, self.stake)
        except Exception:
            logger.error("connect4 stats/badge hook failed", exc_info=True)
        embed = self._embed(final=True, winner=winner, forfeit=forfeit)
        if interaction is not None:
            await self._safe_edit(interaction, embed=embed, view=None)
        elif self.message is not None:
            try:
                await self.message.edit(embed=embed, view=None)
            except discord.HTTPException:
                logger.debug("connect4 final edit failed", exc_info=True)


# ---------------------------------------------------------------------------
# The challenge (pre-accept) view
# ---------------------------------------------------------------------------
class Connect4ChallengeView(discord.ui.View):
    def __init__(self, challenger: Member, opponent: Member, amount: int):
        super().__init__(timeout=getattr(config, "CONNECT4_ACCEPT_SECONDS", 300))
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
                "\U0001F527 The bot's restarting for an update - try again in a minute.",
                ephemeral=True)
            return
        if get_bb(self.challenger.id) < self.amount:
            await interaction.response.send_message(
                f"{self.challenger.display_name} no longer has {self.amount:,} UKPence.",
                ephemeral=True)
            return
        if get_bb(self.opponent.id) < self.amount:
            await interaction.response.send_message(
                f"You no longer have {self.amount:,} UKPence.", ephemeral=True)
            return
        # Claim before any await/payout so two fast clicks can't both stake the pot.
        self.resolved = True
        # Pull both stakes into the bank. If the second fails, refund the first and reopen.
        if not remove_bb(self.challenger.id, self.amount, reason="Connect 4 stake"):
            self.resolved = False
            await interaction.response.send_message(
                f"{self.challenger.display_name} can't cover the stake.", ephemeral=True)
            return
        if not remove_bb(self.opponent.id, self.amount, reason="Connect 4 stake"):
            credit_from_bank(self.challenger.id, self.amount, "Connect 4 stake refund")
            self.resolved = False
            await interaction.response.send_message(
                "You can't cover the stake.", ephemeral=True)
            return

        p1_name = discord.utils.escape_markdown(self.challenger.display_name)
        p2_name = discord.utils.escape_markdown(self.opponent.display_name)
        game = Connect4View(self.challenger.id, p1_name, self.opponent.id, p2_name,
                            self.amount, interaction.channel_id)
        game.message = interaction.message
        game.client = interaction.client
        # Persist the escrow record (keyed by the board message) BEFORE showing the board,
        # so a crash right after staking is still recoverable on boot.
        save_state(interaction.message.id, {
            "type": "connect4",
            "p1_id": self.challenger.id, "p2_id": self.opponent.id,
            "p1_name": p1_name, "p2_name": p2_name,
            "stake": self.amount, "channel_id": interaction.channel_id,
        })
        first = self.challenger if game.turn == 1 else self.opponent
        try:
            await interaction.response.edit_message(
                content=f"{self.challenger.mention} \U0001F534 vs \U0001F7E1 {self.opponent.mention} "
                        f"- game on! {first.mention} moves first.",
                embed=game._embed(), view=game,
                allowed_mentions=discord.AllowedMentions(users=[self.challenger, self.opponent]))
        except Exception:
            # The board never reached the screen - void the game and refund both stakes
            # rather than strand them in escrow until a restart.
            logger.error("connect4 failed to show the board after accept; refunding.",
                         exc_info=True)
            delete_state(interaction.message.id)
            credit_from_bank(self.challenger.id, self.amount, "Connect 4 void refund (start failed)")
            credit_from_bank(self.opponent.id, self.amount, "Connect 4 void refund (start failed)")
            try:
                await interaction.followup.send(
                    "Something went wrong starting the game - both stakes were refunded.",
                    ephemeral=True)
            except Exception:
                pass
            return
        game.start()
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
            e.title = "Connect 4 - Challenge Retracted"
            e.description = (f"{self.challenger.mention} retracted their challenge to "
                             f"{self.opponent.mention}.")
        else:
            e.title = "Connect 4 - Challenge Declined"
            e.description = (f"{self.opponent.mention} declined {self.challenger.mention}'s "
                             "challenge.")
        await interaction.response.edit_message(embed=e, view=self)
        self.stop()

    @discord.ui.button(label="\U0001F4DC Rules", style=discord.ButtonStyle.secondary)
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
            e.title = "Connect 4 - Challenge Expired"
            e.description = (f"{self.challenger.mention}'s challenge to {self.opponent.mention} "
                             "expired (not accepted in time). No UKPence was staked.")
            await self.message.edit(embed=e, view=self)
        except Exception:
            logger.debug("connect4 challenge timeout edit failed", exc_info=True)


# ---------------------------------------------------------------------------
# Command entry
# ---------------------------------------------------------------------------
async def handle_connect4_command(interaction: Interaction, opponent: Member, bet: int):
    if await reject_if_maintenance(interaction):
        return
    if not getattr(config, "CONNECT4_ENABLED", True):
        await interaction.response.send_message("Connect 4 is currently disabled.", ephemeral=True)
        return
    if opponent.bot or opponent.id == interaction.user.id:
        await interaction.response.send_message(
            "Pick a real opponent - you can't play yourself or a bot.", ephemeral=True)
        return

    min_bet = getattr(config, "CONNECT4_MIN_BET", 5)
    max_bet = getattr(config, "CONNECT4_MAX_BET", 5000)
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
            f"{opponent.display_name} doesn't have {bet:,} UKPence to match your stake.",
            ephemeral=True)
        return

    accept_min = getattr(config, "CONNECT4_ACCEPT_SECONDS", 300) // 60
    embed = discord.Embed(
        title="\U0001F534\U0001F7E1 Connect 4 Challenge",
        description=(f"{interaction.user.mention} has challenged {opponent.mention} to "
                     "**Connect 4**!\n\nFour in a row takes the pot. Winner takes all."),
        colour=0xF1C40F,
    )
    embed.add_field(name="Stake", value=f"{bet:,} UKPence each", inline=True)
    embed.add_field(name="Pot", value=f"{bet * 2:,} UKPence", inline=True)
    embed.set_footer(text=f"{opponent.display_name} has {accept_min} min to accept · "
                          "tap Rules for how it works")

    view = Connect4ChallengeView(interaction.user, opponent, bet)
    await interaction.response.send_message(
        content=opponent.mention, embed=embed, view=view,
        allowed_mentions=discord.AllowedMentions(users=[opponent]))
    view.message = await interaction.original_response()


# ---------------------------------------------------------------------------
# Restart recovery (called from event_handlers.reattach_persistent_views)
# ---------------------------------------------------------------------------
def reattach_connect4_view(client, key, value):
    """A Connect 4 game was mid-match when the bot restarted. The live board and forfeit
    clock can't be rebuilt, so VOID the game: refund both stakes and prune the record."""
    try:
        p1 = int(value["p1_id"])
        p2 = int(value["p2_id"])
        stake = int(value["stake"])
    except Exception as e:
        logger.error(f"Pruning malformed connect4 entry {key}: {e}")
        delete_state(key)
        return
    delete_state(key)  # drop first so a re-run can't double-refund
    credit_from_bank(p1, stake, "Connect 4 void refund (bot restart)")
    credit_from_bank(p2, stake, "Connect 4 void refund (bot restart)")

    channel_id = value.get("channel_id")

    async def _notify():
        try:
            channel = client.get_channel(int(channel_id)) if channel_id else None
            if channel is None:
                return
            msg = await channel.fetch_message(int(key))
            e = discord.Embed(
                title="\U0001F534\U0001F7E1 Connect 4 - Voided",
                description=("This game was voided because the bot restarted mid-match. "
                             f"Both players have had their **{stake:,}** UKP stake refunded."),
                colour=0x95A5A6)
            await msg.edit(embed=e, view=None)
        except Exception:
            logger.debug("connect4 void notify failed", exc_info=True)

    try:
        asyncio.create_task(_notify())
    except RuntimeError:
        pass  # no running loop at recovery time - the refund above is what matters
