"""Mines - a single-player "reveal gems, dodge mines, cash out" game for UKPence.

A 5x5 grid (25 tiles) hides ``mines`` bombs. The player taps tiles to reveal gems;
each gem lifts the multiplier. Cash out any time to take ``stake x multiplier`` from
the house bank - but tap a mine and the whole stake is lost. Clearing every safe tile
auto-cashes-out at the top multiplier.

Money flow (mirrors the other casino games; the fixed 800k UKP supply is conserved):
    • Stake:  remove_bb(uid, bet)   - to_bank=True, the stake enters the house bank.
    • Win:    credit_from_bank(uid, stake x mult)  - paid out of the bank.
    • Loss:   nothing paid - the staked bet simply stays in the bank.

Fairness: the multiplier after k gems is (1 - edge) / P(survive k reveals), so the
expected return is a constant (1 - edge) of the stake no matter when you cash out -
the house edge is the same whatever you do. Payouts are capped (MINES_MAX_WIN) so a
lucky low-tile-count board can never demand more than the bank can safely pay.

The board is a Components V2 LayoutView: a status panel plus 25 tile buttons (5 rows)
and a Cash Out button. In-play games are persisted by message id and their click
routing is re-registered on restart (reattach_mines_view); terminal boards are dropped.
"""
import uuid
import random
import logging

import discord
from discord import Interaction

from lib.economy.economy_manager import get_bb, remove_bb
from lib.economy.casino_drain import action_in_flight, deal_in_flight
from commands.economy.casino_base import (
    credit_from_bank, reject_if_maintenance, save_state, delete_state, ACCENT,
)

logger = logging.getLogger(__name__)

GRID = 5
TILES = GRID * GRID            # 25


class MinesGame:
    """One game of Mines. Mutated in place across reveals within a single game; the
    mine layout lives only server-side (never sent to the client)."""

    def __init__(self, game_id, player_id, player_name, channel_id, bet, mines,
                 mine_positions, *, revealed=None, state="playing", outcome=None,
                 hit_mine=None, payout=0, message_id=None):
        self.game_id = game_id
        self.player_id = int(player_id)
        self.player_name = player_name
        self.channel_id = channel_id
        self.bet = int(bet)
        self.mines = int(mines)
        self.mine_positions = list(mine_positions)
        self.revealed = list(revealed or [])
        self.state = state              # "playing" | "over"
        self.outcome = outcome          # None | "win" | "lose"
        self.hit_mine = hit_mine        # tile index of the mine that ended it, if any
        self.payout = int(payout)
        self.message_id = message_id
        # transient (never serialised)
        self.busy = False               # True while a click is mid-render - drops double-clicks

    @classmethod
    def new(cls, player_id, player_name, channel_id, bet, mines):
        positions = random.sample(range(TILES), mines)
        return cls(uuid.uuid4().hex[:12], player_id, player_name, channel_id, bet, mines,
                   positions)

    # --- maths ---
    @property
    def revealed_count(self) -> int:
        return len(self.revealed)

    @property
    def safe_tiles(self) -> int:
        return TILES - len(self.mine_positions)

    def multiplier(self, k=None) -> float:
        """Cash-out multiplier after k gems (default: current). Fair odds scaled by the
        house edge, so EV is a constant (1 - edge) of the stake regardless of strategy."""
        import config
        if k is None:
            k = self.revealed_count
        k = min(k, self.safe_tiles)
        edge = getattr(config, "MINES_HOUSE_EDGE", 0.02)
        m = len(self.mine_positions)
        mult = 1.0
        for i in range(k):
            mult *= (TILES - i) / (TILES - m - i)
        return (1.0 - edge) * mult

    def payout_for(self, k=None) -> int:
        import config
        cap = getattr(config, "MINES_MAX_WIN", 100_000)
        return min(int(self.bet * self.multiplier(k)), cap)

    def current_payout(self) -> int:
        return self.payout_for(self.revealed_count)

    # --- transitions ---
    def reveal(self, idx) -> str:
        """Reveal a tile. Returns 'mine' | 'gem' | 'win' (board cleared) | 'ignore'."""
        if self.state != "playing" or idx in self.revealed or not (0 <= idx < TILES):
            return "ignore"
        if idx in self.mine_positions:
            self.hit_mine = idx
            self.state = "over"
            self.outcome = "lose"
            return "mine"
        self.revealed.append(idx)
        if self.revealed_count >= self.safe_tiles:
            self.cash_out()
            return "win"
        return "gem"

    def cash_out(self) -> int:
        self.payout = self.current_payout()
        self.state = "over"
        self.outcome = "win"
        return self.payout

    # --- serialisation (only in-play games are persisted) ---
    def to_dict(self) -> dict:
        return {
            "type": "mines",
            "game_id": self.game_id,
            "player_id": self.player_id,
            "player_name": self.player_name,
            "channel_id": self.channel_id,
            "message_id": self.message_id,
            "bet": self.bet,
            "mines": self.mines,
            "mine_positions": self.mine_positions,
            "revealed": self.revealed,
            "state": self.state,
            "outcome": self.outcome,
            "hit_mine": self.hit_mine,
            "payout": self.payout,
        }

    @classmethod
    def from_dict(cls, d: dict):
        return cls(
            game_id=d["game_id"], player_id=d["player_id"],
            player_name=d.get("player_name", "Player"), channel_id=d.get("channel_id"),
            bet=d["bet"], mines=d["mines"], mine_positions=d["mine_positions"],
            revealed=d.get("revealed", []), state=d.get("state", "playing"),
            outcome=d.get("outcome"), hit_mine=d.get("hit_mine"),
            payout=d.get("payout", 0), message_id=d.get("message_id"),
        )


def save_game(game: MinesGame):
    if game.message_id is not None:
        save_state(game.message_id, game.to_dict())


# ---------------------------------------------------------------------------
# Rendering (Components V2: status panel + 5x5 tile grid + Cash Out)
# ---------------------------------------------------------------------------
def _status_text(game: MinesGame) -> str:
    mult = game.multiplier()
    if game.state == "over":
        if game.outcome == "win":
            head = (f"## 💎 Mines - Cashed Out\n"
                    f"Won **{game.payout:,} UKPence** at **{mult:.2f}×**.")
        else:
            head = (f"## 💥 Mines - Boom!\n"
                    f"You hit a mine and lost **{game.bet:,} UKPence**.")
        return f"{head}\n-# {game.mines} mines · {game.revealed_count} gem(s) found"
    if game.revealed_count == 0:
        return (f"## 💎 Mines\n"
                f"Stake **{game.bet:,}** · **{game.mines}** mines hidden in {TILES} tiles.\n"
                f"-# Reveal a gem to build your multiplier. Hit a mine and you lose the lot.")
    nxt = game.multiplier(game.revealed_count + 1)
    return (f"## 💎 Mines\n"
            f"Stake **{game.bet:,}** · **{game.mines}** mines · **{game.revealed_count}** gem(s)\n"
            f"Current **{mult:.2f}×** → cash out **{game.current_payout():,} UKPence**\n"
            f"-# Next gem → {nxt:.2f}× ({game.payout_for(game.revealed_count + 1):,}). "
            f"Tap a tile, or cash out while you're ahead.")


def _tile_button(game: MinesGame, idx: int) -> discord.ui.Button:
    cid = f"mines:{game.game_id}:t:{idx}"
    revealed = idx in game.revealed
    is_mine = idx in game.mine_positions
    if game.state == "over":
        # Reveal the whole board: bombs where mines were, gems everywhere else.
        if is_mine:
            emoji = "💥" if idx == game.hit_mine else "💣"
            style = discord.ButtonStyle.danger
        else:
            emoji = "💎"
            style = discord.ButtonStyle.success if revealed else discord.ButtonStyle.secondary
        return discord.ui.Button(style=style, emoji=emoji, disabled=True, custom_id=cid)
    if revealed:
        return discord.ui.Button(style=discord.ButtonStyle.success, emoji="💎",
                                 disabled=True, custom_id=cid)
    btn = discord.ui.Button(style=discord.ButtonStyle.secondary, emoji="🟦", custom_id=cid)
    btn.callback = _make_tile_cb(game, idx)
    return btn


def _cash_button(game: MinesGame):
    if game.state == "over":
        return None
    ready = game.revealed_count >= 1
    btn = discord.ui.Button(
        style=discord.ButtonStyle.success,
        label=(f"Cash Out  {game.current_payout():,}" if ready else "Cash Out"),
        emoji="💰",
        custom_id=f"mines:{game.game_id}:cash",
        disabled=not ready,
    )
    if ready:
        btn.callback = _make_cash_cb(game)
    return btn


def build_mines_layout(game: MinesGame) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=None)
    box = discord.ui.Container(accent_colour=ACCENT)
    box.add_item(discord.ui.TextDisplay(_status_text(game)))
    view.add_item(box)
    for r in range(GRID):
        row = discord.ui.ActionRow()
        for c in range(GRID):
            row.add_item(_tile_button(game, r * GRID + c))
        view.add_item(row)
    cash = _cash_button(game)
    if cash is not None:
        cash_row = discord.ui.ActionRow()
        cash_row.add_item(cash)
        view.add_item(cash_row)
    return view


# ---------------------------------------------------------------------------
# Interaction handling
# ---------------------------------------------------------------------------
def _make_tile_cb(game: MinesGame, idx: int):
    async def _cb(interaction: Interaction):
        # Keep the shutdown drain waiting while a click settles (credit + delete).
        with action_in_flight():
            await _handle_reveal(interaction, game, idx)
    return _cb


def _make_cash_cb(game: MinesGame):
    async def _cb(interaction: Interaction):
        with action_in_flight():
            await _handle_cashout(interaction, game)
    return _cb


async def _rerender(interaction: Interaction, game: MinesGame):
    view = build_mines_layout(game)
    await interaction.response.edit_message(view=view)
    if game.state == "playing" and game.message_id is not None:
        try:
            interaction.client.add_view(view, message_id=game.message_id)
        except Exception:
            logger.debug("Mines add_view after refresh failed (non-fatal)", exc_info=True)


def _not_your_game(interaction: Interaction, game: MinesGame) -> bool:
    return interaction.user.id != game.player_id


async def _handle_reveal(interaction: Interaction, game: MinesGame, idx: int):
    if _not_your_game(interaction, game):
        await interaction.response.send_message(
            "This isn't your game - start your own with `/mines`.", ephemeral=True)
        return
    # Drop clicks that land while a previous one is mid-render. Read-then-set has no
    # await between, so it's atomic on the event loop: exactly one click is processed.
    if game.busy or game.state != "playing":
        await interaction.response.defer()
        return
    game.busy = True
    try:
        result = game.reveal(idx)
        if result == "ignore":
            await interaction.response.defer()
            return
        if result == "win":
            # Drop the persisted board BEFORE paying. If we're interrupted between the
            # two, the worst case is an unpaid (but un-resumable) board - never a
            # paid-AND-resumable one, which would mint UKP on the next boot.
            delete_state(game.message_id)
            credit_from_bank(game.player_id, game.payout, reason="Mines win (board cleared)")
        elif result == "mine":
            delete_state(game.message_id)   # stake already in the bank; nothing to pay
        else:                               # "gem"
            save_game(game)
        await _rerender(interaction, game)
    finally:
        game.busy = False


async def _handle_cashout(interaction: Interaction, game: MinesGame):
    if _not_your_game(interaction, game):
        await interaction.response.send_message(
            "This isn't your game - start your own with `/mines`.", ephemeral=True)
        return
    if game.busy or game.state != "playing":
        await interaction.response.defer()
        return
    if game.revealed_count < 1:
        await interaction.response.send_message(
            "Reveal at least one gem before cashing out.", ephemeral=True)
        return
    game.busy = True
    try:
        payout = game.cash_out()
        # Delete-before-credit (see _handle_reveal): never leave a paid, resumable board.
        delete_state(game.message_id)
        credit_from_bank(game.player_id, payout, reason="Mines cashout")
        await _rerender(interaction, game)
    finally:
        game.busy = False


# ---------------------------------------------------------------------------
# Command entry
# ---------------------------------------------------------------------------
@deal_in_flight
async def handle_mines_command(interaction: Interaction, amount: int, mines: int):
    import config
    if await reject_if_maintenance(interaction):
        return
    if not getattr(config, "MINES_ENABLED", True):
        await interaction.response.send_message("The mines table is closed.", ephemeral=True)
        return

    min_bet = getattr(config, "MINES_MIN_BET", 5)
    max_bet = getattr(config, "MINES_MAX_BET", 10_000)
    if amount < min_bet:
        await interaction.response.send_message(
            f"The minimum bet is {min_bet:,} UKPence.", ephemeral=True)
        return
    if amount > max_bet:
        await interaction.response.send_message(
            f"The maximum bet is {max_bet:,} UKPence.", ephemeral=True)
        return
    if not (1 <= mines <= TILES - 1):
        await interaction.response.send_message(
            f"Mines must be between 1 and {TILES - 1}.", ephemeral=True)
        return

    balance = get_bb(interaction.user.id)
    if balance < amount:
        await interaction.response.send_message(
            f"You don't have enough UKPence. Your balance is {balance:,}.", ephemeral=True)
        return

    # Atomic debit - the stake goes straight into the house bank.
    if not remove_bb(interaction.user.id, amount, reason="Mines bet"):
        await interaction.response.send_message(
            f"You don't have enough UKPence. Your balance is {get_bb(interaction.user.id):,}.",
            ephemeral=True)
        return

    name = discord.utils.escape_markdown(interaction.user.display_name)
    game = None
    view = None
    try:
        await interaction.response.defer(thinking=True)
        game = MinesGame.new(interaction.user.id, name, interaction.channel_id, amount, mines)
        view = build_mines_layout(game)
        msg = await interaction.followup.send(view=view)
    except Exception:
        # The board never made it onto the table - refund the stake (it's in the bank).
        logger.error("Mines deal failed; refunding stake.", exc_info=True)
        credit_from_bank(interaction.user.id, amount, "Mines stake refund (deal failed)")
        try:
            await interaction.followup.send(
                "Something went wrong starting your game - your stake has been refunded.",
                ephemeral=True)
        except Exception:
            pass
        return

    # Board is live (discord.py registered the view on send). Persist + re-register
    # routing; a failure here is logged but must NOT refund - the game is playable.
    game.message_id = msg.id
    try:
        # A click can land between send and here (buttons are live the instant the
        # message posts). If that click already ended the game, don't persist a
        # terminal board or re-register the now-stale playing view - just clear it.
        if game.state == "playing":
            save_game(game)
            interaction.client.add_view(view, message_id=msg.id)
        else:
            delete_state(msg.id)
    except Exception:
        logger.error("Mines post-send persistence issue (game is live).", exc_info=True)


# ---------------------------------------------------------------------------
# Restart recovery (called from event_handlers.reattach_persistent_views)
# ---------------------------------------------------------------------------
def reattach_mines_view(client, key, value):
    """Re-register click routing for an in-play board after a restart. Terminal or
    malformed entries are pruned so they can't wedge future restarts."""
    try:
        game = MinesGame.from_dict(value)
    except Exception as e:
        logger.error(f"Pruning malformed mines entry {key}: {e}", exc_info=True)
        delete_state(key)
        return
    if game.state != "playing":
        delete_state(key)
        return
    try:
        game.message_id = int(key)
        view = build_mines_layout(game)
        client.add_view(view, message_id=int(key))
    except Exception as e:
        logger.error(f"Failed to reattach mines view {key}: {e}", exc_info=True)
