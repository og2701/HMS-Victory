"""Blockade Run - a single-player "push your luck" crash game for UKPence.

Your ship runs the enemy blockade. Each **Sail On** pushes deeper and lifts the multiplier; a
hidden, pre-rolled bust point will sink you, and the moment you pass it the ship goes down with
your whole stake. **Drop Anchor** any time to bank stake×multiplier.

It's player-paced (no timer) - you click to advance - so message-edit latency never costs you a
click. (An earlier real-time version drove the surges off a server clock, but Discord edit lag
meant the value you saw lagged the server and the click window collapsed; clicking to advance
removes the clock from the outcome entirely.)

Money flow (mirrors the other casino games; the fixed 800k UKP supply is conserved):
    • Stake:  remove_bb(uid, bet)   - to_bank=True, the stake enters the house bank.
    • Cash:   credit_from_bank(uid, stake×mult)  - paid out of the bank.
    • Sunk:   nothing paid - the staked bet stays in the bank.

Fairness: the bust multiplier B is drawn so P(B ≥ x) = (1 − edge)/x. Banking at any multiplier T
returns EV = (1 − edge) of the stake, so the house edge is a constant CRASH_HOUSE_EDGE no matter
how far you push. CRASH_MAX_MULT auto-banks at the ceiling, bounding the bank's tail.

The animated, transparent sailing ship (sailing.webp) is uploaded ONCE and then referenced by
its CDN url on each click, so it keeps sailing without re-uploading the ~4MB file. Persisted by
message id and resumed on restart (reattach_crash_view), exactly like Mines/Chest.
"""
import io
import os
import math
import uuid
import random
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


# --- config helpers --------------------------------------------------------
def _edge():   return float(getattr(config, "CRASH_HOUSE_EDGE", 0.03))
def _growth(): return float(getattr(config, "CRASH_GROWTH", 1.15))
def _cap():    return float(getattr(config, "CRASH_MAX_MULT", 25.0))


def _roll_bust() -> float:
    """Pre-roll the bust multiplier with P(B ≥ x) = (1 − edge)/x. A roll below 1.0 means the
    first push already sinks you - that mass is exactly the house edge."""
    edge = _edge()
    u = 1.0 - random.random()              # (0, 1]  (avoids divide-by-zero)
    bust = (1.0 - edge) / u
    return bust if bust >= 1.0 else 1.0


# --- art assets (data/blockade/) ------------------------------------------------
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


def _raw_asset(filename: str):
    """Raw bytes of data/blockade/<filename>, cached and untouched (preserves animation)."""
    key = "_raw_" + filename
    if key in _asset_cache:
        return _asset_cache[key]
    data = None
    path = os.path.join(_ASSET_DIR, filename)
    try:
        if os.path.exists(path):
            with open(path, "rb") as f:
                data = f.read()
    except Exception:
        logger.debug("blockade raw asset load failed: %s", filename, exc_info=True)
    _asset_cache[key] = data
    return data


class CrashGame:
    """One Blockade Run. The bust point is fixed at deal; `mult` is the displayed multiplier,
    advanced one notch per Sail On click."""

    def __init__(self, game_id, player_id, player_name, channel_id, bet, bust, *,
                 ticks=0, mult=1.00, state="running", payout=0, img_url=None, message_id=None):
        self.game_id = game_id
        self.player_id = int(player_id)
        self.player_name = player_name
        self.channel_id = channel_id
        self.bet = int(bet)
        self.bust = float(bust)
        self.ticks = int(ticks)
        self.mult = float(mult)             # displayed (safe) multiplier
        self.state = state                  # running | cashed | busted
        self.payout = int(payout)
        self.img_url = img_url              # CDN url of the uploaded ship (referenced each click)
        self.message_id = message_id
        # transient (never persisted)
        self.message = None
        self.busy = False
        self.replayed = False

    @classmethod
    def new(cls, player_id, player_name, channel_id, bet):
        return cls(uuid.uuid4().hex[:12], player_id, player_name, channel_id, bet, _roll_bust())

    # --- maths/transitions (sync, await-free) ---
    def payout_now(self) -> int:
        return int(self.bet * self.mult)

    def can_cash(self) -> bool:
        return self.state == "running"      # bank any time, incl. 1.00× (a stake-back chicken-out)

    def cash_out(self):
        if not self.can_cash():
            return None
        self.state = "cashed"
        self.payout = self.payout_now()
        return self.payout

    def sail_on(self) -> str:
        """Push one notch deeper. Returns 'sail' | 'bust' | 'cap' | 'noop'."""
        if self.state != "running":
            return "noop"
        self.ticks += 1
        raw = _growth() ** self.ticks
        if raw >= self.bust:                       # the blockade catches you
            self.state = "busted"
            return "bust"
        disp = math.floor(raw * 100) / 100.0       # only ever show a value strictly below bust
        cap = _cap()
        if disp >= cap:                            # ran the whole blockade - forced bank at the ceiling
            self.mult = cap
            self.state = "cashed"
            self.payout = int(self.bet * cap)
            return "cap"
        self.mult = disp
        return "sail"

    def crash_display(self) -> float:
        return math.floor(self.bust * 100) / 100.0

    # --- serialisation (running games are persisted so they resume across a restart) ---
    def to_dict(self) -> dict:
        return {"type": "crash", "game_id": self.game_id, "player_id": self.player_id,
                "player_name": self.player_name, "channel_id": self.channel_id,
                "message_id": self.message_id, "bet": self.bet, "bust": self.bust,
                "ticks": self.ticks, "mult": self.mult, "state": self.state,
                "payout": self.payout, "img_url": self.img_url}

    @classmethod
    def from_dict(cls, d: dict):
        return cls(d["game_id"], d["player_id"], d.get("player_name", "Player"),
                   d.get("channel_id"), d["bet"], d["bust"], ticks=d.get("ticks", 0),
                   mult=d.get("mult", 1.00), state=d.get("state", "running"),
                   payout=d.get("payout", 0), img_url=d.get("img_url"),
                   message_id=d.get("message_id"))


def save_game(game: CrashGame):
    if game.message_id is not None and game.state == "running":
        save_state(game.message_id, game.to_dict())


# ---------------------------------------------------------------------------
# Rendering (Components V2: ship art + status panel + Sail On / Drop Anchor / Rules)
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
    if game.ticks == 0:
        return (f"## 🚢 Blockade Run\n"
                f"Stake **{game.bet:,}** · your ship's at the enemy blockade.\n"
                f"-# **⛵ Sail On** to run it - the multiplier climbs, but a hidden line will sink "
                f"you. **⚓ Drop Anchor** to keep your stake.")
    return (f"## 🚢 Blockade Run\n"
            f"# {game.mult:.2f}×\n"
            f"**⚓ Drop Anchor** to bank **{game.payout_now():,} UKPence**, or **⛵ Sail On** to "
            f"push for more.\n"
            f"-# The deeper you run, the bigger the prize - and the likelier they sink you.")


def _upload_image(game: CrashGame):
    """(bytes, filename) to UPLOAD for the current state. Running prefers the ANIMATED sailing
    webp (uploaded once, then referenced by url - see _build); terminal uses the static PNGs."""
    if game.state == "cashed":
        return _asset_bytes("escaped"), "blockade.png"
    if game.state == "busted":
        return _asset_bytes("sunk"), "blockade.png"
    # running: prefer an animated ship (WebP is transparent; GIF would show an opaque box), else
    # a static PNG ('sailing.png', else the triumphant 'escaped' ship).
    for ext in ("webp", "gif"):
        anim = _raw_asset(f"sailing.{ext}")
        if anim is not None:
            return anim, f"blockade.{ext}"
    return (_asset_bytes("sailing") or _asset_bytes("escaped")), "blockade.png"


def _sail_button(game: CrashGame) -> discord.ui.Button:
    btn = discord.ui.Button(style=discord.ButtonStyle.primary, label="Sail On", emoji="⛵",
                            custom_id=f"blockade:{game.game_id}:sail")
    btn.callback = _make_sail_cb(game)
    return btn


def _anchor_button(game: CrashGame) -> discord.ui.Button:
    label = "Keep Stake" if game.ticks == 0 else f"Drop Anchor  {game.payout_now():,}"
    btn = discord.ui.Button(style=discord.ButtonStyle.success, label=label, emoji="⚓",
                            custom_id=f"blockade:{game.game_id}:anchor")
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
    """Return (view, files). While running, the (animated) ship is referenced by its CDN url once
    uploaded, so `files` is empty and the edit leaves the attachment in place (no re-upload, the
    animation keeps playing). The opening frame and the terminal art are real uploads."""
    view = discord.ui.LayoutView(timeout=None)
    files = []
    if game.state == "running" and game.img_url:
        gallery = discord.ui.MediaGallery()
        gallery.add_item(media=game.img_url)
        view.add_item(gallery)
    else:
        data, fname = _upload_image(game)
        if data is not None:
            files = [discord.File(io.BytesIO(data), filename=fname)]
            gallery = discord.ui.MediaGallery()
            gallery.add_item(media=f"attachment://{fname}")
            view.add_item(gallery)
    box = discord.ui.Container(accent_colour=ACCENT)
    box.add_item(discord.ui.TextDisplay(_status_text(game)))
    view.add_item(box)
    controls = discord.ui.ActionRow()
    if game.state == "running":
        controls.add_item(_sail_button(game))
        controls.add_item(_anchor_button(game))
    else:
        controls.add_item(_again_button(game))
    controls.add_item(_rules_button(game))
    view.add_item(controls)
    return view, files


def _capture_img_url(game: CrashGame, msg):
    """Remember the CDN url of the ship we just uploaded, so later clicks reference it."""
    try:
        atts = getattr(msg, "attachments", None)
        game.img_url = atts[0].url if atts else None
    except Exception:
        game.img_url = None


# ---------------------------------------------------------------------------
# Settlement (sync, await-free)
# ---------------------------------------------------------------------------
def _settle_cash(game: CrashGame, reason: str):
    delete_state(game.message_id)
    credit_from_bank(game.player_id, game.payout, reason=reason)
    record_result(game.player_id, "blockade", game.bet, game.bet, game.payout, "win")


def _settle_bust(game: CrashGame):
    delete_state(game.message_id)                  # stake already in the bank; nothing to pay
    record_result(game.player_id, "blockade", game.bet, game.bet, 0, "lose")


async def _rerender(interaction: Interaction, game: CrashGame):
    """Edit the board for this click and re-register routing for the new view. With files we
    upload/replace the attachment (opening / terminal art); without, we leave the running ship in
    place and only swap text (its url is referenced, so the animation isn't re-sent or reset)."""
    view, files = _build(game)
    try:
        if files:
            await interaction.response.edit_message(view=view, attachments=files)
        else:
            await interaction.response.edit_message(view=view)
    except (discord.NotFound, discord.InteractionResponded):
        try:
            target = game.message or interaction.message
            if target is not None:
                if files:
                    await target.edit(view=view, attachments=files)
                else:
                    await target.edit(view=view)
        except discord.HTTPException:
            logger.debug("blockade fallback edit failed", exc_info=True)
    try:
        interaction.client.add_view(view, message_id=game.message_id)
    except Exception:
        logger.debug("blockade add_view failed", exc_info=True)


# ---------------------------------------------------------------------------
# Interaction handling
# ---------------------------------------------------------------------------
def _make_sail_cb(game: CrashGame):
    async def _cb(interaction: Interaction):
        with action_in_flight():
            await _handle_sail(interaction, game)
    return _cb


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


def _not_your_game(interaction: Interaction, game: CrashGame) -> bool:
    return interaction.user.id != game.player_id


async def _handle_sail(interaction: Interaction, game: CrashGame):
    if _not_your_game(interaction, game):
        await interaction.response.send_message(
            "This isn't your run - start your own with `/blockade`.", ephemeral=True)
        return
    if game.busy or game.state != "running":       # drop double-clicks (atomic: no await between)
        await interaction.response.defer()
        return
    game.busy = True
    try:
        game.message = game.message or interaction.message
        result = game.sail_on()
        if result == "bust":
            _settle_bust(game)
        elif result == "cap":
            _settle_cash(game, "Blockade Run cashout (ceiling)")
        else:                                       # "sail" - still running, persist the new notch
            save_game(game)
        await _rerender(interaction, game)
        if game.state != "running":
            from lib.economy.game_badges import award_blockade_badges
            await award_blockade_badges(interaction.client, game)
    finally:
        game.busy = False


async def _handle_anchor(interaction: Interaction, game: CrashGame):
    if _not_your_game(interaction, game):
        await interaction.response.send_message(
            "This isn't your run - start your own with `/blockade`.", ephemeral=True)
        return
    if game.busy or game.state != "running":
        await interaction.response.defer()
        return
    game.busy = True
    try:
        game.message = game.message or interaction.message
        game.cash_out()
        _settle_cash(game, "Blockade Run cashout")
        await _rerender(interaction, game)
        from lib.economy.game_badges import award_blockade_badges
        await award_blockade_badges(interaction.client, game)
    finally:
        game.busy = False


async def _handle_again(interaction: Interaction, old_game: CrashGame):
    if _not_your_game(interaction, old_game):
        await interaction.response.send_message(
            "This isn't your run - start your own with `/blockade`.", ephemeral=True)
        return
    if old_game.replayed:
        await interaction.response.defer()
        return
    old_game.replayed = True
    bet = old_game.bet
    min_bet = getattr(config, "CRASH_MIN_BET", 5)
    max_bet = getattr(config, "CRASH_MAX_BET", 1_000)
    if await reject_if_maintenance(interaction):
        old_game.replayed = False
        return
    if not getattr(config, "CRASH_ENABLED", True):
        old_game.replayed = False
        await interaction.response.send_message("The blockade is closed.", ephemeral=True)
        return
    if bet < min_bet or bet > max_bet:
        old_game.replayed = False
        await interaction.response.send_message(
            f"Bets must be between {min_bet:,} and {max_bet:,} UKPence.", ephemeral=True)
        return
    if get_bb(old_game.player_id) < bet or not remove_bb(old_game.player_id, bet, reason="Blockade Run bet"):
        old_game.replayed = False
        await interaction.response.send_message(
            f"You need {bet:,} UKPence to sail again.", ephemeral=True)
        return

    game = CrashGame.new(old_game.player_id, old_game.player_name, old_game.channel_id, bet)
    game.message_id = old_game.message_id
    game.message = old_game.message or interaction.message
    view, files = _build(game)                     # opening frame uploads the ship afresh
    try:
        await interaction.response.edit_message(view=view, attachments=files)
        try:
            _capture_img_url(game, await interaction.original_response())
        except Exception:
            game.img_url = None
    except (discord.NotFound, discord.InteractionResponded):
        try:
            msg = await game.message.edit(view=view, attachments=files)
            _capture_img_url(game, msg)
        except discord.HTTPException:
            logger.error("Blockade replay failed to show the new board; refunding stake.")
            credit_from_bank(game.player_id, bet, "Blockade Run stake refund (replay failed)")
            old_game.replayed = False
            return
    save_game(game)
    try:
        interaction.client.add_view(view, message_id=game.message_id)
    except Exception:
        logger.debug("blockade replay add_view failed", exc_info=True)


async def _show_rules(interaction: Interaction):
    min_bet = getattr(config, "CRASH_MIN_BET", 5)
    max_bet = getattr(config, "CRASH_MAX_BET", 1_000)
    cap = _cap()
    rules = (
        "## 🚢 Blockade Run - House Rules\n"
        "Run the enemy blockade. Each **⛵ Sail On** pushes deeper and lifts the multiplier - but "
        "a hidden point along the route will sink you, and once you pass it you **lose the whole "
        "stake**. **⚓ Drop Anchor** any time to bank **stake × multiplier**.\n\n"
        "- Go at your own pace - there's no timer; it's your nerve against the odds.\n"
        f"- The run auto-banks if you reach the **{cap:g}×** ceiling.\n"
        f"- **Bets:** {min_bet:,} - {max_bet:,} UKPence. Stakes go to the house bank and wins are "
        "paid from it.\n\n"
        "-# The catch point is rolled fairly at the start, so the house keeps the same small edge "
        "no matter how far you push. Steady nerves, sailor. 🇬🇧"
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
        view, files = _build(game)
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
    _capture_img_url(game, msg)                    # remember the uploaded ship's url for later clicks
    try:
        save_game(game)
        interaction.client.add_view(view, message_id=msg.id)
    except Exception:
        logger.error("Blockade post-send persistence issue (game is live).", exc_info=True)


# ---------------------------------------------------------------------------
# Restart recovery (called from event_handlers.reattach_persistent_views)
# ---------------------------------------------------------------------------
def reattach_crash_view(client, key, value):
    """Re-register click routing for an in-play run after a restart so it resumes where it left
    off (the game is fully serialised). Terminal/malformed entries are pruned."""
    try:
        game = CrashGame.from_dict(value)
    except Exception as e:
        logger.error(f"Pruning malformed blockade entry {key}: {e}", exc_info=True)
        delete_state(key)
        return
    if game.state != "running":
        delete_state(key)
        return
    try:
        game.message_id = int(key)
        view, _files = _build(game)                # files unused; the message keeps its attachment
        client.add_view(view, message_id=int(key))
    except Exception as e:
        logger.error(f"Failed to reattach blockade view {key}: {e}", exc_info=True)
