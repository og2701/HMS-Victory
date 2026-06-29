"""Chest Upgrade - a single-player "press your luck" multiplier ladder for UKPence.

Open the free Wood chest (1.0x your stake), then decide, tier by tier, whether to risk it to
upgrade: Wood → Silver → Gold → Diamond. Each upgrade has a success chance; succeed and you
hold the next chest (a higher cash-out multiplier), fail and the chest shatters - you lose the
whole stake. Cash out any time to take ``stake x multiplier`` from the house bank. Reaching the
top chest (Diamond) auto-cashes-out.

Money flow (mirrors the other casino games; the fixed 800k UKP supply is conserved):
    • Stake:  remove_bb(uid, bet)   - to_bank=True, the stake enters the house bank.
    • Win:    credit_from_bank(uid, stake x mult)  - paid out of the bank.
    • Loss:   nothing paid - the staked bet simply stays in the bank.

Fairness: the success odds are DERIVED from the multipliers and a flat CHEST_HOUSE_EDGE -
    P(success from tier i) = mult[i] * (1 - edge) / mult[i+1]
so every upgrade is EV = (1 - edge) of what you're holding. The house edge is therefore the
same on every push and there is no exploitable stopping point - the only thing the player
controls is variance (cash early for a near-certain small win, or chase the 8x Diamond).

The board is a Components V2 LayoutView: a status panel plus an Upgrade / Cash Out / Rules
control row. In-play games are persisted by message id and re-registered on restart
(reattach_chest_view); terminal boards are dropped.
"""
import uuid
import random
import logging

import discord
from discord import Interaction

import config
from lib.economy.economy_manager import get_bb, remove_bb
from lib.economy.casino_drain import action_in_flight, deal_in_flight
from lib.economy.casino_stats import record_result
from commands.economy.casino_base import (
    credit_from_bank, reject_if_maintenance, save_state, delete_state, ACCENT,
)

logger = logging.getLogger(__name__)


# --- config helpers --------------------------------------------------------
_DEFAULT_TIERS = [("Wood", "🪵", 1.0), ("Silver", "🥈", 1.8), ("Gold", "🥇", 3.5), ("Diamond", "💎", 8.0)]


def _tiers():
    return getattr(config, "CHEST_TIERS", _DEFAULT_TIERS)


def _top_tier() -> int:
    return len(_tiers()) - 1


def _success_prob(from_tier: int) -> float:
    """Chance the upgrade from ``from_tier`` to the next chest succeeds. Derived so every
    upgrade carries the same flat house edge (EV = (1 - edge) of what you hold)."""
    tiers = _tiers()
    if from_tier >= len(tiers) - 1:
        return 0.0
    edge = float(getattr(config, "CHEST_HOUSE_EDGE", 0.05))
    cur, nxt = tiers[from_tier][2], tiers[from_tier + 1][2]
    if nxt <= 0:
        return 0.0
    return max(0.0, min(1.0, cur * (1.0 - edge) / nxt))


class ChestGame:
    """One game of Chest Upgrade. ``tier`` is the chest currently held (0 = Wood). The
    outcome (which upgrade rolls succeed) lives only server-side."""

    def __init__(self, game_id, player_id, player_name, channel_id, bet, *,
                 tier=0, state="playing", outcome=None, payout=0, message_id=None):
        self.game_id = game_id
        self.player_id = int(player_id)
        self.player_name = player_name
        self.channel_id = channel_id
        self.bet = int(bet)
        self.tier = int(tier)            # chest currently held (0..top)
        self.state = state               # "playing" | "over"
        self.outcome = outcome           # None | "win" (cashed) | "lose" (upgrade shattered it)
        self.payout = int(payout)
        self.message_id = message_id
        # transient (never serialised)
        self.busy = False                # drops double-clicks mid-render
        self.replayed = False            # set once Play Again deals a fresh game on this message

    @classmethod
    def new(cls, player_id, player_name, channel_id, bet):
        return cls(uuid.uuid4().hex[:12], player_id, player_name, channel_id, bet)

    # --- maths ---
    def multiplier(self, tier=None) -> float:
        if tier is None:
            tier = self.tier
        return float(_tiers()[tier][2])

    def payout_for(self, tier=None) -> int:
        raw = int(self.bet * self.multiplier(tier))
        cap = getattr(config, "CHEST_MAX_WIN", 0)
        return raw if cap <= 0 else min(raw, cap)

    def current_payout(self) -> int:
        return self.payout_for(self.tier)

    def at_top(self) -> bool:
        return self.tier >= _top_tier()

    # --- transitions ---
    def upgrade(self) -> str:
        """Risk the held chest on the next upgrade. Returns 'up' | 'top' | 'break' | 'ignore'."""
        if self.state != "playing" or self.at_top():
            return "ignore"
        if random.random() < _success_prob(self.tier):
            self.tier += 1
            if self.at_top():
                self.cash_out()          # top chest auto-cashes at its multiplier
                return "top"
            return "up"
        # Failed upgrade: the chest shatters and the stake is lost.
        self.state = "over"
        self.outcome = "lose"
        return "break"

    def cash_out(self) -> int:
        self.payout = self.current_payout()
        self.state = "over"
        self.outcome = "win"
        return self.payout

    # --- serialisation (only in-play games are persisted) ---
    def to_dict(self) -> dict:
        return {
            "type": "chest",
            "game_id": self.game_id,
            "player_id": self.player_id,
            "player_name": self.player_name,
            "channel_id": self.channel_id,
            "message_id": self.message_id,
            "bet": self.bet,
            "tier": self.tier,
            "state": self.state,
            "outcome": self.outcome,
            "payout": self.payout,
        }

    @classmethod
    def from_dict(cls, d: dict):
        return cls(
            game_id=d["game_id"], player_id=d["player_id"],
            player_name=d.get("player_name", "Player"), channel_id=d.get("channel_id"),
            bet=d["bet"], tier=d.get("tier", 0), state=d.get("state", "playing"),
            outcome=d.get("outcome"), payout=d.get("payout", 0),
            message_id=d.get("message_id"),
        )


def save_game(game: ChestGame):
    if game.message_id is not None:
        save_state(game.message_id, game.to_dict())


# ---------------------------------------------------------------------------
# Rendering (Components V2: status panel + Upgrade / Cash Out / Rules)
# ---------------------------------------------------------------------------
def _tier_label(tier: int) -> str:
    name, emoji, _ = _tiers()[tier]
    return f"{emoji} **{name}**"


def _status_text(game: ChestGame) -> str:
    tiers = _tiers()
    held_name, held_emoji, held_mult = tiers[game.tier]
    if game.state == "over":
        if game.outcome == "win":
            head = (f"## 🧰 Chest Upgrade - Cashed Out\n"
                    f"Banked a {held_emoji} **{held_name}** chest for "
                    f"**{game.payout:,} UKPence** ({held_mult:g}×).")
        else:
            attempt = tiers[min(game.tier + 1, _top_tier())]
            head = (f"## 💥 Chest Upgrade - Shattered!\n"
                    f"The {attempt[1]} **{attempt[0]}** upgrade failed - your {held_emoji} "
                    f"**{held_name}** chest shattered and you lost **{game.bet:,} UKPence**.")
        return head
    # Playing (never the top tier - that auto-cashes).
    nxt_name, nxt_emoji, nxt_mult = tiers[game.tier + 1]
    prob = _success_prob(game.tier)
    if game.tier == 0:
        line1 = (f"You've opened a {held_emoji} **{held_name}** chest - your "
                 f"**{game.bet:,}** stake back (1×) if you stop here.")
    else:
        line1 = (f"You hold a {held_emoji} **{held_name}** chest - cash out for "
                 f"**{game.current_payout():,} UKPence** ({held_mult:g}×).")
    return (f"## 🧰 Chest Upgrade\n"
            f"{line1}\n"
            f"Risk it to upgrade to {nxt_emoji} **{nxt_name}** ({nxt_mult:g}× → "
            f"**{game.payout_for(game.tier + 1):,}**)?\n"
            f"-# **{prob*100:.0f}%** chance to succeed - fail and the chest shatters, "
            f"losing your **{game.bet:,}** stake.")


def _upgrade_button(game: ChestGame) -> discord.ui.Button:
    nxt_name = _tiers()[game.tier + 1][0]
    prob = _success_prob(game.tier)
    btn = discord.ui.Button(
        style=discord.ButtonStyle.primary,
        label=f"Upgrade → {nxt_name} ({prob*100:.0f}%)",
        emoji="⬆️",
        custom_id=f"chest:{game.game_id}:up",
    )
    btn.callback = _make_upgrade_cb(game)
    return btn


def _cash_button(game: ChestGame) -> discord.ui.Button:
    # At Wood this returns the stake (break-even exit); at higher tiers it banks the win.
    label = ("Take Stake Back" if game.tier == 0
             else f"Cash Out  {game.current_payout():,}")
    btn = discord.ui.Button(
        style=discord.ButtonStyle.success, label=label, emoji="💰",
        custom_id=f"chest:{game.game_id}:cash",
    )
    btn.callback = _make_cash_cb(game)
    return btn


def _rules_button(game: ChestGame) -> discord.ui.Button:
    btn = discord.ui.Button(
        style=discord.ButtonStyle.secondary, label="Rules", emoji="📖",
        custom_id=f"chest:{game.game_id}:rules",
    )
    btn.callback = _show_rules
    return btn


def _again_button(game: ChestGame) -> discord.ui.Button:
    btn = discord.ui.Button(
        style=discord.ButtonStyle.primary, label="Play Again", emoji="🔁",
        custom_id=f"chest:{game.game_id}:again",
    )
    btn.callback = _make_again_cb(game)
    return btn


def build_chest_layout(game: ChestGame) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=None)
    box = discord.ui.Container(accent_colour=ACCENT)
    box.add_item(discord.ui.TextDisplay(_status_text(game)))
    view.add_item(box)
    controls = discord.ui.ActionRow()
    if game.state == "over":
        controls.add_item(_again_button(game))
    else:
        controls.add_item(_upgrade_button(game))
        controls.add_item(_cash_button(game))
    controls.add_item(_rules_button(game))
    view.add_item(controls)
    return view


# ---------------------------------------------------------------------------
# Interaction handling
# ---------------------------------------------------------------------------
def _make_upgrade_cb(game: ChestGame):
    async def _cb(interaction: Interaction):
        with action_in_flight():
            await _handle_upgrade(interaction, game)
    return _cb


def _make_cash_cb(game: ChestGame):
    async def _cb(interaction: Interaction):
        with action_in_flight():
            await _handle_cashout(interaction, game)
    return _cb


def _make_again_cb(old_game: ChestGame):
    async def _cb(interaction: Interaction):
        with action_in_flight():
            await _handle_again(interaction, old_game)
    return _cb


async def _safe_edit_board(interaction: Interaction, view) -> bool:
    """Refresh the board, surviving a dead interaction token (mirrors mines._safe_edit_board)."""
    try:
        await interaction.response.edit_message(view=view)
        return True
    except (discord.NotFound, discord.InteractionResponded):
        try:
            if interaction.message is not None:
                await interaction.message.edit(view=view)
                return True
        except discord.HTTPException:
            logger.debug("Chest board fallback edit failed", exc_info=True)
    except discord.HTTPException:
        logger.debug("Chest board edit failed", exc_info=True)
    return False


async def _rerender(interaction: Interaction, game: ChestGame):
    view = build_chest_layout(game)
    await _safe_edit_board(interaction, view)
    # Re-register routing for the freshly-built view (keeps Rules / Play Again live once
    # the board is terminal). The view always reflects current state, so never stale.
    if game.message_id is not None:
        try:
            interaction.client.add_view(view, message_id=game.message_id)
        except Exception:
            logger.debug("Chest add_view after refresh failed (non-fatal)", exc_info=True)


def _not_your_game(interaction: Interaction, game: ChestGame) -> bool:
    return interaction.user.id != game.player_id


async def _handle_upgrade(interaction: Interaction, game: ChestGame):
    if _not_your_game(interaction, game):
        await interaction.response.send_message(
            "This isn't your game - start your own with `/chest`.", ephemeral=True)
        return
    # Read-then-set with no await between = atomic on the event loop: exactly one click wins.
    if game.busy or game.state != "playing":
        await interaction.response.defer()
        return
    game.busy = True
    try:
        result = game.upgrade()
        if result == "ignore":
            await interaction.response.defer()
            return
        if result == "top":
            # Reached the top chest - auto-cashed. Delete-before-credit so an interruption
            # can never leave a paid-AND-resumable board (which would mint UKP on reboot).
            delete_state(game.message_id)
            credit_from_bank(game.player_id, game.payout, reason="Chest win (max tier)")
            record_result(game.player_id, "chest", game.bet, game.bet, game.payout, "win")
        elif result == "break":
            delete_state(game.message_id)   # stake already in the bank; nothing to pay
            record_result(game.player_id, "chest", game.bet, game.bet, 0, "lose")
        else:                               # "up" - still playing, persist the new tier
            save_game(game)
        await _rerender(interaction, game)
    finally:
        game.busy = False


async def _handle_cashout(interaction: Interaction, game: ChestGame):
    if _not_your_game(interaction, game):
        await interaction.response.send_message(
            "This isn't your game - start your own with `/chest`.", ephemeral=True)
        return
    if game.busy or game.state != "playing":
        await interaction.response.defer()
        return
    game.busy = True
    try:
        payout = game.cash_out()
        # Delete-before-credit (see _handle_upgrade): never leave a paid, resumable board.
        delete_state(game.message_id)
        credit_from_bank(game.player_id, payout, reason="Chest cashout")
        # tier 0 cash-out is a break-even refund of the stake; still log it for the stats.
        record_result(game.player_id, "chest", game.bet, game.bet, payout, "win")
        await _rerender(interaction, game)
    finally:
        game.busy = False


async def _handle_again(interaction: Interaction, old_game: ChestGame):
    """Play Again: deal a fresh chest on the same message at the previous stake. Only the
    original player can replay (it re-stakes their UKPence)."""
    if interaction.user.id != old_game.player_id:
        await interaction.response.send_message(
            "This isn't your game - start your own with `/chest`.", ephemeral=True)
        return
    if old_game.replayed:               # this board already moved on to a new game
        await interaction.response.defer()
        return
    if await reject_if_maintenance(interaction):
        return
    if not getattr(config, "CHEST_ENABLED", True):
        await interaction.response.send_message("The chest table is closed.", ephemeral=True)
        return
    bet = old_game.bet
    min_bet = getattr(config, "CHEST_MIN_BET", 5)
    max_bet = getattr(config, "CHEST_MAX_BET", 1_000)
    if bet < min_bet or bet > max_bet:
        await interaction.response.send_message(
            f"Bets must be between {min_bet:,} and {max_bet:,} UKPence.", ephemeral=True)
        return
    if get_bb(old_game.player_id) < bet:
        await interaction.response.send_message(
            f"You need {bet:,} UKPence to play again.", ephemeral=True)
        return
    if not remove_bb(old_game.player_id, bet, reason="Chest bet"):
        await interaction.response.send_message(
            "You don't have enough UKPence.", ephemeral=True)
        return
    # Claim the replay before the first awaiting call so two fast clicks can't both deal.
    old_game.replayed = True

    new_game = ChestGame.new(old_game.player_id, old_game.player_name, old_game.channel_id, bet)
    new_game.message_id = old_game.message_id
    view = build_chest_layout(new_game)
    if not await _safe_edit_board(interaction, view):
        logger.error("Chest replay failed before showing the new board; refunding stake.")
        credit_from_bank(old_game.player_id, bet, "Chest stake refund (replay failed)")
        old_game.replayed = False
        return
    try:
        save_game(new_game)
        interaction.client.add_view(view, message_id=new_game.message_id)
    except Exception:
        logger.error("Chest replay post-update issue (board is live).", exc_info=True)


async def _show_rules(interaction: Interaction):
    """Ephemeral house rules. Open to anyone (no owner check) and changes no state."""
    tiers = _tiers()
    min_bet = getattr(config, "CHEST_MIN_BET", 5)
    max_bet = getattr(config, "CHEST_MAX_BET", 1_000)
    max_win = getattr(config, "CHEST_MAX_WIN", 0)
    cap_str = f"; wins are capped at {max_win:,}" if max_win > 0 else ""
    ladder = "\n".join(
        f"- {emoji} **{name}** = **{mult:g}×**"
        + (f"  ·  **{_success_prob(i-1)*100:.0f}%** to reach from {tiers[i-1][1]} {tiers[i-1][0]}"
           if i > 0 else "  ·  your free starting chest")
        for i, (name, emoji, mult) in enumerate(tiers)
    )
    rules = (
        "## 🧰 Chest Upgrade - House Rules\n"
        "Open the free Wood chest, then choose tier by tier whether to **risk it to upgrade**. "
        "Succeed and you hold a richer chest; **fail and it shatters - you lose the lot.**\n\n"
        f"{ladder}\n\n"
        "- **Cash Out** any time to take **stake × multiplier** from the bank (cashing the Wood "
        "chest just returns your stake).\n"
        "- Reach 💎 **Diamond** and it auto-cashes at the top multiplier.\n"
        f"- **Bets:** {min_bet:,} - {max_bet:,} UKPence{cap_str}. Stakes go to the house bank "
        "and wins are paid from it.\n\n"
        "-# Every upgrade carries the same small house edge, so there's no clever stopping "
        "point - just pick your nerve. Good luck. 🇬🇧"
    )
    await interaction.response.send_message(rules, ephemeral=True)


# ---------------------------------------------------------------------------
# Command entry
# ---------------------------------------------------------------------------
@deal_in_flight
async def handle_chest_command(interaction: Interaction, amount: int):
    if await reject_if_maintenance(interaction):
        return
    if not getattr(config, "CHEST_ENABLED", True):
        await interaction.response.send_message("The chest table is closed.", ephemeral=True)
        return

    min_bet = getattr(config, "CHEST_MIN_BET", 5)
    max_bet = getattr(config, "CHEST_MAX_BET", 1_000)
    if amount < min_bet:
        await interaction.response.send_message(
            f"The minimum bet is {min_bet:,} UKPence.", ephemeral=True)
        return
    if amount > max_bet:
        await interaction.response.send_message(
            f"The maximum bet is {max_bet:,} UKPence.", ephemeral=True)
        return

    balance = get_bb(interaction.user.id)
    if balance < amount:
        await interaction.response.send_message(
            f"You don't have enough UKPence. Your balance is {balance:,}.", ephemeral=True)
        return

    # Atomic debit - the stake goes straight into the house bank.
    if not remove_bb(interaction.user.id, amount, reason="Chest bet"):
        await interaction.response.send_message(
            f"You don't have enough UKPence. Your balance is {get_bb(interaction.user.id):,}.",
            ephemeral=True)
        return

    name = discord.utils.escape_markdown(interaction.user.display_name)
    game = None
    view = None
    try:
        await interaction.response.defer(thinking=True)
        game = ChestGame.new(interaction.user.id, name, interaction.channel_id, amount)
        view = build_chest_layout(game)
        msg = await interaction.followup.send(view=view)
    except Exception:
        logger.error("Chest deal failed; refunding stake.", exc_info=True)
        credit_from_bank(interaction.user.id, amount, "Chest stake refund (deal failed)")
        try:
            await interaction.followup.send(
                "Something went wrong starting your game - your stake has been refunded.",
                ephemeral=True)
        except Exception:
            pass
        return

    # Board is live (discord.py registered the view on send). Persist + re-register routing.
    game.message_id = msg.id
    try:
        if game.state == "playing":
            save_game(game)
            interaction.client.add_view(view, message_id=msg.id)
        else:
            delete_state(msg.id)
    except Exception:
        logger.error("Chest post-send persistence issue (game is live).", exc_info=True)


# ---------------------------------------------------------------------------
# Restart recovery (called from event_handlers.reattach_persistent_views)
# ---------------------------------------------------------------------------
def reattach_chest_view(client, key, value):
    """Re-register click routing for an in-play board after a restart. Terminal or malformed
    entries are pruned so they can't wedge future restarts."""
    try:
        game = ChestGame.from_dict(value)
    except Exception as e:
        logger.error(f"Pruning malformed chest entry {key}: {e}", exc_info=True)
        delete_state(key)
        return
    if game.state != "playing":
        delete_state(key)
        return
    try:
        game.message_id = int(key)
        view = build_chest_layout(game)
        client.add_view(view, message_id=int(key))
    except Exception as e:
        logger.error(f"Failed to reattach chest view {key}: {e}", exc_info=True)
