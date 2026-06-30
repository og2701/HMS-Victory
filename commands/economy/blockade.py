"""Blockade Run - a single-player "crash" game for UKPence.

Your ship runs the enemy blockade and a multiplier climbs each tick (the message live-updates).
Hit Drop Anchor to bank stake×multiplier - but a hidden, pre-rolled bust point will sink you, and
if you haven't anchored by then you lose the whole stake.

Money flow (mirrors the other casino games; the fixed 800k UKP supply is conserved):
    • Stake:  remove_bb(uid, bet)   - to_bank=True, the stake enters the house bank.
    • Cash:   credit_from_bank(uid, stake×mult)  - paid out of the bank.
    • Sunk:   nothing paid - the staked bet stays in the bank.

Fairness: the bust multiplier B is drawn so P(B ≥ x) = (1 − edge)/x. A player who decides to
cash at target T reaches it with probability (1 − edge)/T and wins T, so EV = (1 − edge) of the
stake for EVERY target - the house edge is a constant CRASH_HOUSE_EDGE no matter how brave you
are. CRASH_MAX_MULT caps the round (auto-cash at the ceiling), bounding length + bank tail.

Live ticking: the board updates via an asyncio loop editing the message every CRASH_TICK_SECS.
The loop and the Drop Anchor click both transition state through guarded, await-free methods, so
exactly one of "cashed" / "busted" wins. In-flight rounds can't survive a restart (the loop is
in-memory), so a persisted escrow record is refunded + voided on startup (reattach_crash_view).
"""
import io
import os
import math
import time
import uuid
import random
import asyncio
import logging

import discord
from discord import Interaction
from PIL import Image

import config
from lib.economy.economy_manager import get_bb, remove_bb
from lib.economy.casino_drain import action_in_flight, deal_in_flight
from lib.economy.casino_stats import record_result
from commands.economy.casino_base import (
    credit_from_bank, reject_if_maintenance, save_state, delete_state, ACCENT,
)

logger = logging.getLogger(__name__)

# Live games by message id (the ticking loop + the buttons both look the game up here).
_GAMES = {}


# --- config helpers --------------------------------------------------------
def _edge():   return float(getattr(config, "CRASH_HOUSE_EDGE", 0.03))
def _growth(): return float(getattr(config, "CRASH_GROWTH", 1.07))
def _cap():    return float(getattr(config, "CRASH_MAX_MULT", 25.0))
def _tick():   return float(getattr(config, "CRASH_TICK_SECS", 1.3))


def _roll_bust() -> float:
    """Pre-roll the bust multiplier with P(B ≥ x) = (1 − edge)/x. A roll below 1.0 is an
    instant bust (you never get to anchor) - that mass is exactly the house edge."""
    edge = _edge()
    u = 1.0 - random.random()              # (0, 1]  (avoids divide-by-zero)
    bust = (1.0 - edge) / u
    return bust if bust >= 1.0 else 1.0


# --- chest-style art assets (data/blockade/escaped.png, sunk.png) ----------------
_ASSET_DIR = os.path.join("data", "blockade")
_ASSET_PX = 320
_asset_cache = {}


def _asset_bytes(name: str):
    """Load + downscale + cache data/blockade/<name>.png. None (cached) if missing/unreadable."""
    if name in _asset_cache:
        return _asset_cache[name]
    data = None
    path = os.path.join(_ASSET_DIR, f"{name}.png")
    try:
        if os.path.exists(path):
            with Image.open(path) as im:
                im = im.convert("RGBA")
                im.thumbnail((_ASSET_PX, _ASSET_PX), Image.LANCZOS)
                buf = io.BytesIO()
                im.save(buf, format="PNG")
                data = buf.getvalue()
    except Exception:
        logger.debug("blockade asset load failed: %s", name, exc_info=True)
    _asset_cache[name] = data
    return data


class CrashGame:
    """One Blockade Run. The bust point is fixed at deal; `mult` is the displayed multiplier,
    advanced one tick at a time by the ticker loop."""

    def __init__(self, game_id, player_id, player_name, channel_id, bet, bust, message_id=None):
        self.game_id = game_id
        self.player_id = int(player_id)
        self.player_name = player_name
        self.channel_id = channel_id
        self.bet = int(bet)
        self.bust = float(bust)
        self.message_id = message_id
        self.ticks = 0
        self.mult = 1.00              # displayed (safe) multiplier
        self.state = "running"        # running | cashed | busted | voided
        self.payout = 0
        # transient (never persisted)
        self.message = None           # discord.Message the loop edits
        self.client = None
        self.task = None              # the ticker asyncio.Task

    @classmethod
    def new(cls, player_id, player_name, channel_id, bet):
        return cls(uuid.uuid4().hex[:12], player_id, player_name, channel_id, bet, _roll_bust())

    # --- maths/transitions (sync + await-free => atomic on the event loop) ---
    def payout_now(self) -> int:
        return int(self.bet * self.mult)

    def can_cash(self) -> bool:
        # The button only goes live after the first rising tick, so an instant/early bust (bust
        # below the first tick value) resolves before the player could ever anchor at 1.00×.
        return self.state == "running" and self.ticks >= 1

    def cash_out(self):
        """Anchor at the current displayed multiplier. Returns payout, or None if not cashable."""
        if not self.can_cash():
            return None
        self.state = "cashed"
        self.payout = int(self.bet * self.mult)
        return self.payout

    def advance(self) -> str:
        """One tick. Returns 'tick' | 'bust' | 'cap' | 'noop'."""
        if self.state != "running":
            return "noop"
        self.ticks += 1
        raw = _growth() ** self.ticks
        if raw >= self.bust:                       # the blockade catches you
            self.state = "busted"
            return "bust"
        cap = _cap()
        disp = math.floor(raw * 100) / 100.0       # only ever show a value strictly below bust
        if disp >= cap:                            # ran the whole blockade - forced cash at the ceiling
            self.mult = cap
            self.state = "cashed"
            self.payout = int(self.bet * cap)
            return "cap"
        self.mult = disp
        return "tick"

    def crash_display(self) -> float:
        return math.floor(self.bust * 100) / 100.0

    # --- escrow (persisted only while running, for restart refunds) ---
    def escrow(self) -> dict:
        return {"type": "crash", "game_id": self.game_id, "player_id": self.player_id,
                "channel_id": self.channel_id, "bet": self.bet}


# ---------------------------------------------------------------------------
# Rendering (Components V2: text panel while running; art on the terminal states)
# ---------------------------------------------------------------------------
def _status_text(game: CrashGame) -> str:
    if game.state == "cashed":
        return (f"## ⚓ Made Port!\n"
                f"You anchored at **{game.mult:.2f}×** and banked **{game.payout:,} UKPence**. "
                f"The blockade never caught you. \U0001F389")
    if game.state == "busted":
        return (f"## 💥 Sunk!\n"
                f"The blockade caught you at **{game.crash_display():.2f}×** - your ship went down "
                f"with **{game.bet:,} UKPence** aboard. Should've anchored sooner.")
    if game.state == "voided":
        return ("## ⚓ Run Aborted\n"
                "The bot restarted mid-run, so this round was voided and your stake refunded.")
    # Next surge time as a Discord relative timestamp - the client animates the countdown
    # ("in 5s… 4… 3") between the bot's edits, so the round feels live without per-second edits.
    next_ts = int(time.time()) + max(1, round(_tick()))
    if game.ticks == 0:
        return (f"## 🚢 Blockade Run\n"
                f"Stake **{game.bet:,}** · setting sail into the enemy blockade...\n"
                f"⏳ First surge <t:{next_ts}:R>\n"
                f"-# The multiplier climbs with each surge. **⚓ Drop Anchor** to bank it before "
                f"they catch you.")
    return (f"## 🚢 Blockade Run\n"
            f"# {game.mult:.2f}×\n"
            f"Anchor now to bank **{game.payout_now():,} UKPence**  ·  ⏳ next surge <t:{next_ts}:R>\n"
            f"-# The longer you sail the bigger the prize - and the likelier they sink you. "
            f"**⚓ Drop Anchor** while you're ahead.")


def _board_image(game: CrashGame):
    if game.state == "cashed":
        return _asset_bytes("escaped"), "blockade.png"
    if game.state in ("busted", "voided"):
        return _asset_bytes("sunk"), "blockade.png"
    # running: a dedicated 'sailing' ship if present, else reuse the triumphant 'escaped' one.
    # At a 5s surge cadence re-uploading the ship each tick is cheap (was text-only at 1.3s).
    return (_asset_bytes("sailing") or _asset_bytes("escaped")), "blockade.png"


def _anchor_button(game: CrashGame) -> discord.ui.Button:
    btn = discord.ui.Button(
        style=discord.ButtonStyle.success,
        label=(f"Drop Anchor  {game.payout_now():,}" if game.can_cash() else "Drop Anchor"),
        emoji="⚓",
        custom_id=f"blockade:{game.game_id}:anchor",
        disabled=not game.can_cash(),
    )
    if game.can_cash():
        btn.callback = _make_anchor_cb(game)
    return btn


def _rules_button(game: CrashGame) -> discord.ui.Button:
    btn = discord.ui.Button(style=discord.ButtonStyle.secondary, label="Rules", emoji="📖",
                            custom_id=f"blockade:{game.game_id}:rules")
    btn.callback = _show_rules
    return btn


def _again_button(game: CrashGame) -> discord.ui.Button:
    btn = discord.ui.Button(style=discord.ButtonStyle.primary, label="Sail Again", emoji="🔁",
                            custom_id=f"blockade:{game.game_id}:again")
    btn.callback = _make_again_cb(game)
    return btn


def _build(game: CrashGame):
    """Return (view, files)."""
    view = discord.ui.LayoutView(timeout=None)
    files = []
    img_bytes, fname = _board_image(game)
    if img_bytes is not None:
        files = [discord.File(io.BytesIO(img_bytes), filename=fname)]
        gallery = discord.ui.MediaGallery()
        gallery.add_item(media=f"attachment://{fname}")
        view.add_item(gallery)
    box = discord.ui.Container(accent_colour=ACCENT)
    box.add_item(discord.ui.TextDisplay(_status_text(game)))
    view.add_item(box)
    controls = discord.ui.ActionRow()
    if game.state == "running":
        controls.add_item(_anchor_button(game))
    else:
        controls.add_item(_again_button(game))
    controls.add_item(_rules_button(game))
    view.add_item(controls)
    return view, files


# payout at the current displayed multiplier (used in the live text)
CrashGame.payout_now = lambda self: int(self.bet * self.mult)


# ---------------------------------------------------------------------------
# Settlement (sync, await-free => the loop and the click can't double-settle)
# ---------------------------------------------------------------------------
def _settle_cash(game: CrashGame, reason: str):
    delete_state(game.message_id)
    credit_from_bank(game.player_id, game.payout, reason=reason)
    record_result(game.player_id, "blockade", game.bet, game.bet, game.payout, "win")


def _settle_bust(game: CrashGame):
    delete_state(game.message_id)                  # stake already in the bank; nothing to pay
    record_result(game.player_id, "blockade", game.bet, game.bet, 0, "lose")


async def _refresh(game: CrashGame):
    """Re-render the live message and re-register button routing for the new view."""
    view, files = _build(game)
    try:
        await game.message.edit(view=view, attachments=files)
        if game.client is not None and game.message_id is not None:
            game.client.add_view(view, message_id=game.message_id)
    except discord.HTTPException:
        logger.debug("blockade refresh edit failed", exc_info=True)


async def _ticker(game: CrashGame):
    """Climb the multiplier until the player anchors, the blockade catches them, or the ceiling
    forces a cash-out."""
    try:
        while True:
            await asyncio.sleep(_tick())
            if game.state != "running":
                return
            result = game.advance()
            if result == "tick":
                await _refresh(game)
                continue
            if result == "cap":
                _settle_cash(game, "Blockade Run cashout (ceiling)")
            elif result == "bust":
                _settle_bust(game)
            else:
                return
            _GAMES.pop(game.message_id, None)
            await _refresh(game)
            return
    except asyncio.CancelledError:
        return
    except Exception:
        logger.error("blockade ticker crashed", exc_info=True)
        _GAMES.pop(game.message_id, None)


# ---------------------------------------------------------------------------
# Interaction handling
# ---------------------------------------------------------------------------
def _make_anchor_cb(game: CrashGame):
    async def _cb(interaction: Interaction):
        with action_in_flight():
            await _handle_anchor(interaction, game)
    return _cb


def _make_again_cb(game: CrashGame):
    async def _cb(interaction: Interaction):
        with action_in_flight():
            await _handle_again(interaction, game)
    return _cb


async def _handle_anchor(interaction: Interaction, game: CrashGame):
    if interaction.user.id != game.player_id:
        await interaction.response.send_message(
            "This isn't your run - start your own with `/blockade`.", ephemeral=True)
        return
    payout = game.cash_out()                       # atomic: None if already over / not yet live
    if payout is None:
        await interaction.response.defer()
        return
    if game.task is not None:
        game.task.cancel()
    _settle_cash(game, "Blockade Run cashout")
    _GAMES.pop(game.message_id, None)
    view, files = _build(game)
    try:
        await interaction.response.edit_message(view=view, attachments=files)
    except (discord.NotFound, discord.InteractionResponded):
        try:
            await game.message.edit(view=view, attachments=files)
        except discord.HTTPException:
            logger.debug("blockade anchor fallback edit failed", exc_info=True)


async def _handle_again(interaction: Interaction, old_game: CrashGame):
    if interaction.user.id != old_game.player_id:
        await interaction.response.send_message(
            "This isn't your run - start your own with `/blockade`.", ephemeral=True)
        return
    if await reject_if_maintenance(interaction):
        return
    if not getattr(config, "CRASH_ENABLED", True):
        await interaction.response.send_message("The blockade is closed.", ephemeral=True)
        return
    bet = old_game.bet
    min_bet = getattr(config, "CRASH_MIN_BET", 5)
    max_bet = getattr(config, "CRASH_MAX_BET", 1_000)
    if bet < min_bet or bet > max_bet:
        await interaction.response.send_message(
            f"Bets must be between {min_bet:,} and {max_bet:,} UKPence.", ephemeral=True)
        return
    # One live game per message: if a new run is already on this message, bail.
    if _GAMES.get(old_game.message_id) is not old_game:
        await interaction.response.defer()
        return
    if get_bb(old_game.player_id) < bet or not remove_bb(old_game.player_id, bet, reason="Blockade Run bet"):
        await interaction.response.send_message(
            f"You need {bet:,} UKPence to sail again.", ephemeral=True)
        return

    game = CrashGame.new(old_game.player_id, old_game.player_name, old_game.channel_id, bet)
    game.message_id = old_game.message_id
    game.message = old_game.message
    game.client = old_game.client
    _GAMES[game.message_id] = game                 # replace the old (terminal) game
    save_state(game.message_id, game.escrow())
    view, files = _build(game)
    try:
        await interaction.response.edit_message(view=view, attachments=files)
    except (discord.NotFound, discord.InteractionResponded):
        try:
            await game.message.edit(view=view, attachments=files)
        except discord.HTTPException:
            logger.error("Blockade replay failed to show the new board; refunding stake.")
            credit_from_bank(game.player_id, bet, "Blockade Run stake refund (replay failed)")
            delete_state(game.message_id)
            _GAMES.pop(game.message_id, None)
            return
    game.task = asyncio.create_task(_ticker(game))


async def _show_rules(interaction: Interaction):
    min_bet = getattr(config, "CRASH_MIN_BET", 5)
    max_bet = getattr(config, "CRASH_MAX_BET", 1_000)
    cap = _cap()
    rules = (
        "## 🚢 Blockade Run - House Rules\n"
        "Your ship runs the enemy blockade and the **multiplier climbs every second**. "
        "**⚓ Drop Anchor** to bank **stake × multiplier** - but a hidden point along the route "
        "will sink you, and if you haven't anchored by then you **lose the whole stake**.\n\n"
        "- The longer you sail, the bigger the prize - and the likelier you're caught.\n"
        f"- You can anchor from the first tick onward; the run auto-banks if you reach the "
        f"**{cap:g}×** ceiling.\n"
        f"- **Bets:** {min_bet:,} - {max_bet:,} UKPence. Stakes go to the house bank and wins are "
        "paid from it.\n\n"
        "-# The catch point is rolled fairly at the start, so the house keeps the same small edge "
        "no matter when you anchor. Steady nerves, sailor. 🇬🇧"
    )
    await interaction.response.send_message(rules, ephemeral=True)


# ---------------------------------------------------------------------------
# Command entry
# ---------------------------------------------------------------------------
@deal_in_flight
async def handle_blockade_command(interaction: Interaction, amount: int):
    if await reject_if_maintenance(interaction):
        return
    if not getattr(config, "CRASH_ENABLED", True):
        await interaction.response.send_message("The blockade is closed.", ephemeral=True)
        return
    min_bet = getattr(config, "CRASH_MIN_BET", 5)
    max_bet = getattr(config, "CRASH_MAX_BET", 1_000)
    if amount < min_bet:
        await interaction.response.send_message(f"The minimum bet is {min_bet:,} UKPence.", ephemeral=True)
        return
    if amount > max_bet:
        await interaction.response.send_message(f"The maximum bet is {max_bet:,} UKPence.", ephemeral=True)
        return
    if get_bb(interaction.user.id) < amount:
        await interaction.response.send_message(
            f"You don't have enough UKPence. Your balance is {get_bb(interaction.user.id):,}.", ephemeral=True)
        return
    if not remove_bb(interaction.user.id, amount, reason="Blockade Run bet"):
        await interaction.response.send_message(
            f"You don't have enough UKPence. Your balance is {get_bb(interaction.user.id):,}.", ephemeral=True)
        return

    name = discord.utils.escape_markdown(interaction.user.display_name)
    game = CrashGame.new(interaction.user.id, name, interaction.channel_id, amount)
    try:
        await interaction.response.defer(thinking=True)
        view, files = _build(game)               # opening frame: anchor disabled (ticks == 0)
        msg = await interaction.followup.send(view=view, files=files)
    except Exception:
        logger.error("Blockade deal failed; refunding stake.", exc_info=True)
        credit_from_bank(interaction.user.id, amount, "Blockade Run stake refund (deal failed)")
        try:
            await interaction.followup.send(
                "Something went wrong leaving port - your stake has been refunded.", ephemeral=True)
        except Exception:
            pass
        return

    game.message = msg
    game.message_id = msg.id
    game.client = interaction.client
    _GAMES[msg.id] = game
    try:
        save_state(msg.id, game.escrow())        # so a restart mid-run refunds the stake
        game.task = asyncio.create_task(_ticker(game))
    except Exception:
        logger.error("Blockade post-send start failed; refunding.", exc_info=True)
        credit_from_bank(interaction.user.id, amount, "Blockade Run stake refund (start failed)")
        delete_state(msg.id)
        _GAMES.pop(msg.id, None)


# ---------------------------------------------------------------------------
# Restart recovery (called from event_handlers.reattach_persistent_views)
# ---------------------------------------------------------------------------
def reattach_crash_view(client, key, value):
    """A live crash round can't survive a restart (the ticker is in-memory), so refund the stake
    and void the message. Always prunes the record so it can't wedge future restarts."""
    try:
        player_id = int(value["player_id"]); bet = int(value["bet"])
        channel_id = value.get("channel_id")
    except Exception as e:
        logger.error(f"Pruning malformed blockade entry {key}: {e}", exc_info=True)
        delete_state(key)
        return
    try:
        credit_from_bank(player_id, bet, "Blockade Run void refund (bot restart)")
    except Exception:
        logger.error("blockade restart refund failed for %s", player_id, exc_info=True)
    delete_state(key)

    async def _void():
        try:
            ch = client.get_channel(int(channel_id)) if channel_id else None
            if ch is None:
                return
            msg = await ch.fetch_message(int(key))
            g = CrashGame(value.get("game_id", "x"), player_id, "Player", channel_id, bet, 1.0,
                          message_id=int(key))
            g.state = "voided"
            view, files = _build(g)
            await msg.edit(view=view, attachments=files)
        except Exception:
            logger.debug("blockade void edit failed for %s", key, exc_info=True)

    try:
        asyncio.get_running_loop().create_task(_void())
    except RuntimeError:
        pass  # no loop (offline script) - the refund already happened; the message stays as-is
