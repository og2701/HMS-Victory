"""Penalty Shootout - a single-player "score, build your multiplier, cash out" game for UKPence.

You take up to five penalties against a Queen's Guard keeper. Pick a corner each shot: if the
keeper dives the wrong way it's a GOAL and your multiplier climbs; if he guesses your corner he
SAVES it and the stake is lost. Cash out after any goal to bank ``stake x multiplier``. Five from
five is a clean sheet at the top multiplier.

The keeper is honest - he dives to a random one of the five corners, so he saves a true 1/5 (20%)
and you score 80% of the time. That fixes the ladder: 1.25x a goal, up to ~2.99x for a clean
sheet. It's a high-hit-rate, low-variance game by design (the counterweight to Mines).

Money flow (mirrors the other casino games; the fixed 800k UKP supply is conserved):
    • Stake:  remove_bb(uid, bet)   - to_bank=True, the stake enters the house bank.
    • Win:    credit_from_bank(uid, stake x mult)  - paid out of the bank.
    • Loss:   nothing paid - the staked bet stays in the bank.

Fairness: multiplier after k goals is (1 - edge) / P(score)^k, so EV is a constant (1 - edge) of
the stake whatever you do - cashing out at one goal or chasing all five has the same expected
return. The house keeps a flat ~2% edge.

The board is a Components V2 LayoutView: a rendered scene (keeper + ball composited by Pillow) plus
a row of aim buttons and a Cash Out button. In-play games are persisted by message id and their
click routing is re-registered on restart (reattach_penalty_view); terminal boards are dropped.
"""
import io
import os
import uuid
import random
import logging

import discord
from discord import Interaction

from lib.economy.economy_manager import get_bb, remove_bb
from lib.economy.casino_drain import action_in_flight, deal_in_flight
from lib.economy.casino_stats import record_result
from commands.economy.casino_base import (
    build_layout, credit_from_bank, reject_if_maintenance, save_state, delete_state,
)

logger = logging.getLogger(__name__)

# --- spots (the 5 aim targets) -------------------------------------------------
SPOTS = ["tl", "tr", "c", "bl", "br"]
SPOT_LABEL = {"tl": "Top L", "tr": "Top R", "c": "Centre", "bl": "Bottom L", "br": "Bottom R"}
SPOT_EMOJI = {"tl": "↖️", "tr": "↗️", "c": "🎯", "bl": "↙️", "br": "↘️"}
MAX_GOALS = 5

# --- image compositing ---------------------------------------------------------
_ASSET_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                          "data", "penalty")
_WORK_W = 1000                       # working/output width (downscaled from the 1672px source)
_BALL_FRACTION = 0.099               # ball width as a fraction of the canvas width
# ball CENTRE as a fraction of (width, height) - calibrated against the dive art
_ANCHORS = {
    "tl": (0.20, 0.26), "tr": (0.80, 0.26), "c": (0.50, 0.47),
    "bl": (0.17, 0.69), "br": (0.83, 0.69),
}
_GUARD = {}          # spot/"ready" -> pre-scaled RGBA scene
_BALL = None         # pre-scaled RGBA ball
_assets_loaded = False


def _ensure_assets():
    """Load + pre-scale the art once. On any failure the game still runs with the
    native-text fallback (build_layout handles image=None)."""
    global _assets_loaded, _BALL
    if _assets_loaded:
        return
    _assets_loaded = True
    try:
        from PIL import Image
        for name in ["ready", *SPOTS]:
            src = Image.open(os.path.join(_ASSET_DIR, f"guard_{name}.png")).convert("RGBA")
            h = round(src.height * _WORK_W / src.width)
            _GUARD[name] = src.resize((_WORK_W, h), Image.LANCZOS)
        ball = Image.open(os.path.join(_ASSET_DIR, "ball.png")).convert("RGBA")
        ball = ball.crop(ball.getbbox())          # strip transparent padding
        bw = int(_BALL_FRACTION * _WORK_W)
        _BALL = ball.resize((bw, round(ball.height * bw / ball.width)), Image.LANCZOS)
    except Exception:
        logger.warning("Penalty art failed to load; falling back to native text.", exc_info=True)
        _GUARD.clear()
        _BALL = None


def _render(game) -> io.BytesIO:
    """Compose the scene for the game's current visual state, or None if art is unavailable.

    No shot yet -> the ready keeper. After a shot -> the keeper at the corner he dived to
    (on a save that's your corner, so it reads as a catch) with the ball in your corner.
    """
    _ensure_assets()
    if not _GUARD:
        return None
    try:
        if game.last_kick is None:
            img = _GUARD["ready"].copy()
        else:
            guard = game.last_kick if game.last_result == "save" else game.last_dove
            img = _GUARD.get(guard, _GUARD["ready"]).copy()
            cx, cy = _ANCHORS[game.last_kick]
            x = int(cx * img.width - _BALL.width / 2)
            y = int(cy * img.height - _BALL.height / 2)
            img.alpha_composite(_BALL, (x, y))
        buf = io.BytesIO()
        # JPEG q88: the flat-vector art keeps crisp white/edges while the file stays ~5x
        # smaller than PNG, which matters when the scene re-renders on every shot.
        img.convert("RGB").save(buf, format="JPEG", quality=88, optimize=True)
        buf.seek(0)
        return buf
    except Exception:
        logger.warning("Penalty render failed; falling back to native text.", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Game model
# ---------------------------------------------------------------------------
class PenaltyGame:
    def __init__(self, game_id, player_id, player_name, channel_id, bet,
                 *, goals=0, state="aiming", outcome=None, payout=0, message_id=None,
                 last_kick=None, last_dove=None, last_result=None):
        self.game_id = game_id
        self.player_id = int(player_id)
        self.player_name = player_name
        self.channel_id = channel_id
        self.bet = int(bet)
        self.goals = int(goals)
        self.state = state                     # "aiming" | "over"
        self.outcome = outcome                 # None | "win" | "lose"
        self.payout = int(payout)
        self.message_id = message_id
        self.last_kick = last_kick             # spot you aimed at last shot
        self.last_dove = last_dove             # spot the keeper dived to last shot
        self.last_result = last_result         # "goal" | "save"
        self.busy = False                      # drops double-clicks mid-render
        self.replayed = False

    @classmethod
    def new(cls, player_id, player_name, channel_id, bet):
        return cls(uuid.uuid4().hex[:12], player_id, player_name, channel_id, bet)

    # --- maths ---
    def _score_prob(self) -> float:
        import config
        return getattr(config, "PENALTY_SCORE_PROB", 0.80)

    def multiplier(self, k=None) -> float:
        import config
        if k is None:
            k = self.goals
        edge = getattr(config, "PENALTY_HOUSE_EDGE", 0.02)
        return (1.0 - edge) * (1.0 / self._score_prob()) ** k

    def payout_for(self, k=None) -> int:
        import config
        raw = int(self.bet * self.multiplier(k))
        cap = getattr(config, "PENALTY_MAX_WIN", 0)
        return raw if cap <= 0 else min(raw, cap)

    def current_payout(self) -> int:
        return self.payout_for(self.goals)

    # --- transitions ---
    def kick(self, spot) -> str:
        """Take a penalty at `spot`. Returns 'goal' | 'save' | 'cleansheet' | 'ignore'."""
        if self.state != "aiming" or spot not in SPOTS:
            return "ignore"
        self.last_kick = spot
        if random.random() < self._score_prob():
            self.last_result = "goal"
            self.last_dove = random.choice([s for s in SPOTS if s != spot])
            self.goals += 1
            if self.goals >= MAX_GOALS:
                self.cash_out()
                return "cleansheet"
            return "goal"
        # keeper guessed the corner
        self.last_result = "save"
        self.last_dove = spot
        self.state = "over"
        self.outcome = "lose"
        return "save"

    def cash_out(self) -> int:
        self.payout = self.current_payout()
        self.state = "over"
        self.outcome = "win"
        return self.payout

    # --- serialisation (only in-play games are persisted) ---
    def to_dict(self) -> dict:
        return {
            "type": "penalty", "game_id": self.game_id, "player_id": self.player_id,
            "player_name": self.player_name, "channel_id": self.channel_id,
            "message_id": self.message_id, "bet": self.bet, "goals": self.goals,
            "state": self.state, "outcome": self.outcome, "payout": self.payout,
            "last_kick": self.last_kick, "last_dove": self.last_dove,
            "last_result": self.last_result,
        }

    @classmethod
    def from_dict(cls, d: dict):
        return cls(
            game_id=d["game_id"], player_id=d["player_id"],
            player_name=d.get("player_name", "Player"), channel_id=d.get("channel_id"),
            bet=d["bet"], goals=d.get("goals", 0), state=d.get("state", "aiming"),
            outcome=d.get("outcome"), payout=d.get("payout", 0), message_id=d.get("message_id"),
            last_kick=d.get("last_kick"), last_dove=d.get("last_dove"),
            last_result=d.get("last_result"),
        )


def save_game(game: PenaltyGame):
    if game.message_id is not None:
        save_state(game.message_id, game.to_dict())


# ---------------------------------------------------------------------------
# Rendering (Components V2: scene image + aim row + controls)
# ---------------------------------------------------------------------------
def _native_text(game: PenaltyGame) -> str:
    """Text-only fallback (used if the art can't render)."""
    return _status_text(game)


def _status_text(game: PenaltyGame) -> str:
    if game.state == "over":
        if game.outcome == "lose":
            return (f"## 🧤 Saved!\n"
                    f"The keeper guessed **{SPOT_LABEL[game.last_kick]}** and palmed it away — "
                    f"you lost **{game.bet:,} UKPence**.\n"
                    f"-# Scored {game.goals} before the save. Better luck next spot-kick.")
        if game.goals >= MAX_GOALS:
            return (f"## 🏆 Clean Sheet!\n"
                    f"Five from five — won **{game.payout:,} UKPence** at **{game.multiplier():.2f}×**!\n"
                    f"-# The perfect shootout. 🇬🇧")
        return (f"## 💰 Cashed Out\n"
                f"Banked **{game.payout:,} UKPence** at **{game.multiplier():.2f}×** after "
                f"**{game.goals}** goal(s).\n-# Knowing when to stop is the whole game.")

    # aiming
    if game.goals == 0:
        return (f"## ⚽ Penalty Shootout\n"
                f"Stake **{game.bet:,} UKPence**. Pick a corner and beat the keeper.\n"
                f"-# Each goal lifts your multiplier (×1.25). If the keeper guesses your corner, "
                f"you lose the lot — so cash out while you're ahead.")
    nxt = game.multiplier(game.goals + 1)
    return (f"## ⚽ GOAL!  ({game.goals} scored)\n"
            f"Now **{game.multiplier():.2f}×** → cash out **{game.current_payout():,} UKPence**.\n"
            f"-# Next goal → {nxt:.2f}× ({game.payout_for(game.goals + 1):,}). "
            f"Shoot again, or bank it while you're ahead.")


def _aim_row(game: PenaltyGame) -> discord.ui.ActionRow:
    row = discord.ui.ActionRow()
    for spot in SPOTS:
        btn = discord.ui.Button(
            style=discord.ButtonStyle.secondary, label=SPOT_LABEL[spot], emoji=SPOT_EMOJI[spot],
            custom_id=f"penalty:{game.game_id}:kick:{spot}")
        btn.callback = _make_kick_cb(game, spot)
        row.add_item(btn)
    return row


def _controls_row(game: PenaltyGame) -> discord.ui.ActionRow:
    row = discord.ui.ActionRow()
    if game.state == "over":
        again = discord.ui.Button(
            style=discord.ButtonStyle.primary, label="Play Again", emoji="🔁",
            custom_id=f"penalty:{game.game_id}:again")
        again.callback = _make_again_cb(game)
        row.add_item(again)
    else:                                       # aiming
        ready = game.goals >= 1
        cash = discord.ui.Button(
            style=discord.ButtonStyle.success, emoji="💰",
            label=(f"Cash Out  {game.current_payout():,}" if ready else "Cash Out"),
            custom_id=f"penalty:{game.game_id}:cash", disabled=not ready)
        if ready:
            cash.callback = _make_cash_cb(game)
        row.add_item(cash)
    rules = discord.ui.Button(
        style=discord.ButtonStyle.secondary, label="Rules", emoji="📖",
        custom_id=f"penalty:{game.game_id}:rules")
    rules.callback = _show_rules
    row.add_item(rules)
    return row


def build_penalty_layout(game: PenaltyGame):
    """Return (view, files) for the current game state."""
    image = _render(game)
    first_row = _aim_row(game) if game.state == "aiming" else _controls_row(game)
    view, files = build_layout(image, f"penalty_{game.game_id}.jpg", first_row,
                               native_text=_native_text(game))
    if game.state == "aiming":
        view.add_item(_controls_row(game))
    return view, files


# ---------------------------------------------------------------------------
# Interaction handling
# ---------------------------------------------------------------------------
def _make_kick_cb(game: PenaltyGame, spot: str):
    async def _cb(interaction: Interaction):
        with action_in_flight():
            await _handle_kick(interaction, game, spot)
    return _cb


def _make_cash_cb(game: PenaltyGame):
    async def _cb(interaction: Interaction):
        with action_in_flight():
            await _handle_cashout(interaction, game)
    return _cb


def _make_again_cb(old_game: PenaltyGame):
    async def _cb(interaction: Interaction):
        with action_in_flight():
            await _handle_again(interaction, old_game)
    return _cb


def _not_your_game(interaction: Interaction, game: PenaltyGame) -> bool:
    return interaction.user.id != game.player_id


async def _safe_edit(interaction: Interaction, view, files) -> bool:
    """Refresh the board, surviving a lapsed interaction token (see mines._safe_edit_board)."""
    try:
        await interaction.response.edit_message(view=view, attachments=files)
        return True
    except (discord.NotFound, discord.InteractionResponded):
        try:
            if interaction.message is not None:
                await interaction.message.edit(view=view, attachments=files)
                return True
        except discord.HTTPException:
            logger.debug("Penalty board fallback edit failed", exc_info=True)
    except discord.HTTPException:
        logger.debug("Penalty board edit failed", exc_info=True)
    return False


async def _rerender(interaction: Interaction, game: PenaltyGame):
    view, files = build_penalty_layout(game)
    await _safe_edit(interaction, view, files)
    if game.message_id is not None:
        try:
            interaction.client.add_view(view, message_id=game.message_id)
        except Exception:
            logger.debug("Penalty add_view after refresh failed (non-fatal)", exc_info=True)


async def _handle_kick(interaction: Interaction, game: PenaltyGame, spot: str):
    if _not_your_game(interaction, game):
        await interaction.response.send_message(
            "This isn't your game - start your own with `/penalty`.", ephemeral=True)
        return
    if game.busy or game.state != "aiming":
        await interaction.response.defer()
        return
    game.busy = True
    try:
        result = game.kick(spot)
        if result == "ignore":
            await interaction.response.defer()
            return
        if result in ("save", "cleansheet"):
            # Terminal. Drop the persisted board BEFORE paying so we never leave a paid,
            # resumable game that could re-mint on the next boot.
            delete_state(game.message_id)
            if result == "cleansheet":
                credit_from_bank(game.player_id, game.payout, reason="Penalty clean sheet")
                record_result(game.player_id, "penalty", game.bet, game.bet, game.payout, "win")
            else:
                record_result(game.player_id, "penalty", game.bet, game.bet, 0, "lose")
        else:                                   # "goal" - still in play
            save_game(game)
        await _rerender(interaction, game)
    finally:
        game.busy = False


async def _handle_cashout(interaction: Interaction, game: PenaltyGame):
    if _not_your_game(interaction, game):
        await interaction.response.send_message(
            "This isn't your game - start your own with `/penalty`.", ephemeral=True)
        return
    if game.busy or game.state != "aiming":
        await interaction.response.defer()
        return
    if game.goals < 1:
        await interaction.response.send_message(
            "Score at least one penalty before cashing out.", ephemeral=True)
        return
    game.busy = True
    try:
        payout = game.cash_out()
        delete_state(game.message_id)
        credit_from_bank(game.player_id, payout, reason="Penalty cashout")
        record_result(game.player_id, "penalty", game.bet, game.bet, payout, "win")
        await _rerender(interaction, game)
    finally:
        game.busy = False


async def _handle_again(interaction: Interaction, old_game: PenaltyGame):
    """Play Again: a fresh shootout on the same message at the previous stake."""
    import config
    if interaction.user.id != old_game.player_id:
        await interaction.response.send_message(
            "This isn't your game - start your own with `/penalty`.", ephemeral=True)
        return
    if old_game.replayed:
        await interaction.response.defer()
        return
    if await reject_if_maintenance(interaction):
        return
    if not getattr(config, "PENALTY_ENABLED", True):
        await interaction.response.send_message("The penalty spot is closed.", ephemeral=True)
        return
    bet = old_game.bet
    min_bet = getattr(config, "PENALTY_MIN_BET", 5)
    max_bet = getattr(config, "PENALTY_MAX_BET", 5_000)
    if bet < min_bet or bet > max_bet:
        await interaction.response.send_message(
            f"Bets must be between {min_bet:,} and {max_bet:,} UKPence.", ephemeral=True)
        return
    if get_bb(old_game.player_id) < bet:
        await interaction.response.send_message(
            f"You need {bet:,} UKPence to play again.", ephemeral=True)
        return
    if not remove_bb(old_game.player_id, bet, reason="Penalty bet"):
        await interaction.response.send_message(
            "You don't have enough UKPence.", ephemeral=True)
        return
    old_game.replayed = True            # claim before the first await so two clicks can't double-deal

    new_game = PenaltyGame.new(old_game.player_id, old_game.player_name, old_game.channel_id, bet)
    new_game.message_id = old_game.message_id
    view, files = build_penalty_layout(new_game)
    if not await _safe_edit(interaction, view, files):
        logger.error("Penalty replay failed before showing the new board; refunding stake.")
        credit_from_bank(old_game.player_id, bet, "Penalty stake refund (replay failed)")
        old_game.replayed = False
        return
    try:
        save_game(new_game)
        interaction.client.add_view(view, message_id=new_game.message_id)
    except Exception:
        logger.error("Penalty replay post-update issue (board is live).", exc_info=True)


async def _show_rules(interaction: Interaction):
    import config
    min_bet = getattr(config, "PENALTY_MIN_BET", 5)
    max_bet = getattr(config, "PENALTY_MAX_BET", 5_000)
    max_win = getattr(config, "PENALTY_MAX_WIN", 0)
    cap_str = f"; wins are capped at {max_win:,}" if max_win > 0 else ""
    rules = (
        "## ⚽ Penalty Shootout - House Rules\n"
        "Take up to **five** penalties against the Queen's Guard keeper. Each shot, pick a "
        "corner:\n\n"
        "- The keeper dives to a **random** corner — he saves a fair **1 in 5** (20%), so you "
        "score **80%** of the time.\n"
        "- **GOAL** → your multiplier climbs **×1.25** a goal: 1.25× → 1.53× → 1.91× → 2.39× → "
        "**2.99×** for a clean sheet.\n"
        "- **SAVED** (keeper guesses your corner) → you lose the stake.\n"
        "- **Cash Out** after any goal to take **stake × multiplier**; **five from five** is a "
        "clean sheet at the top multiplier.\n\n"
        f"- **Bets:** {min_bet:,} - {max_bet:,} UKPence{cap_str}. Stakes go to the house bank; "
        "wins are paid from it.\n\n"
        "-# The house keeps a flat ~2% edge whatever you do — cashing out early or chasing the "
        "clean sheet has the same expected return. Good luck. 🇬🇧"
    )
    await interaction.response.send_message(rules, ephemeral=True)


# ---------------------------------------------------------------------------
# Command entry
# ---------------------------------------------------------------------------
@deal_in_flight
async def handle_penalty_command(interaction: Interaction, amount: int):
    import config
    if await reject_if_maintenance(interaction):
        return
    if not getattr(config, "PENALTY_ENABLED", True):
        await interaction.response.send_message("The penalty spot is closed.", ephemeral=True)
        return

    min_bet = getattr(config, "PENALTY_MIN_BET", 5)
    max_bet = getattr(config, "PENALTY_MAX_BET", 5_000)
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

    if not remove_bb(interaction.user.id, amount, reason="Penalty bet"):
        await interaction.response.send_message(
            f"You don't have enough UKPence. Your balance is {get_bb(interaction.user.id):,}.",
            ephemeral=True)
        return

    name = discord.utils.escape_markdown(interaction.user.display_name)
    game = None
    try:
        await interaction.response.defer(thinking=True)
        game = PenaltyGame.new(interaction.user.id, name, interaction.channel_id, amount)
        view, files = build_penalty_layout(game)
        msg = await interaction.followup.send(view=view, files=files)
    except Exception:
        logger.error("Penalty deal failed; refunding stake.", exc_info=True)
        credit_from_bank(interaction.user.id, amount, "Penalty stake refund (deal failed)")
        try:
            await interaction.followup.send(
                "Something went wrong starting your game - your stake has been refunded.",
                ephemeral=True)
        except Exception:
            pass
        return

    game.message_id = msg.id
    try:
        if game.state != "over":
            save_game(game)
            interaction.client.add_view(view, message_id=msg.id)
        else:
            delete_state(msg.id)
    except Exception:
        logger.error("Penalty post-send persistence issue (game is live).", exc_info=True)


# ---------------------------------------------------------------------------
# Restart recovery (called from event_handlers.reattach_persistent_views)
# ---------------------------------------------------------------------------
def reattach_penalty_view(client, key, value):
    """Re-register click routing for an in-play game after a restart. Terminal or
    malformed entries are pruned so they can't wedge future restarts."""
    try:
        game = PenaltyGame.from_dict(value)
    except Exception as e:
        logger.error(f"Pruning malformed penalty entry {key}: {e}", exc_info=True)
        delete_state(key)
        return
    if game.state != "aiming":
        delete_state(key)
        return
    try:
        game.message_id = int(key)
        view, _ = build_penalty_layout(game)
        client.add_view(view, message_id=int(key))
    except Exception as e:
        logger.error(f"Failed to reattach penalty view {key}: {e}", exc_info=True)
