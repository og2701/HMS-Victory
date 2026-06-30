"""Darts - a single-player "darts blackjack" for UKPence.

Throw up to 3 darts. Each lands on an AREA-WEIGHTED board region (singles are common, trebles
and doubles rare, bulls rarer, with a small chance to miss the board). Your score accumulates.
**Stand** to bank a multiplier set by how high you got - but if a throw takes your total over
DARTS_BUST you **bust** and lose the stake. So it's blackjack with darts: push for a bigger band
without going over.

Money flow (mirrors the other casino games; the fixed 800k UKP supply is conserved):
    • Stake:  remove_bb(uid, bet)   - to_bank=True, the stake enters the house bank.
    • Win:    credit_from_bank(uid, stake×mult)  - paid out of the bank.
    • Loss:   nothing paid - the staked bet stays in the bank (fell short or bust).

Fairness: with the area-weighted board the round's outcome distribution is fixed, and the
paytable + bust ceiling are tuned (optimal-stopping DP, see scratch) so optimal play - stand at
51+ - still leaves the house a ~7% edge. The first dart is thrown on the deal; the player then
chooses Throw/Stand for darts 2 and 3 (the 3rd auto-stands).

Player-paced clicks (no timer), persisted by message id and resumed on restart, like Mines/Chest.
"""
import io
import os
import math
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


# --- area-weighted dartboard (relative areas from real board geometry) -----------
def _build_regions():
    def ann(r2, r1): return r1 * r1 - r2 * r2          # area ∝ r1²−r2² (π cancels)
    a_in50, a_out25 = 6.35 ** 2, ann(6.35, 15.9)
    a_trbl, a_dbl = ann(99, 107), ann(162, 170)
    a_total = 170 ** 2
    a_singles = a_total - a_in50 - a_out25 - a_trbl - a_dbl
    miss = float(getattr(config, "DARTS_MISS_PROB", 0.05))
    sc = (1.0 - miss) / a_total
    regs = [(0, "missed the board", miss),
            (50, "Bullseye", a_in50 * sc),
            (25, "Bull", a_out25 * sc)]
    for n in range(1, 21):
        regs.append((n,     f"Single {n}",  (a_singles / 20) * sc))
        regs.append((2 * n, f"Double {n}",  (a_dbl / 20) * sc))
        regs.append((3 * n, f"Treble {n}",  (a_trbl / 20) * sc))
    return regs


_REGIONS = _build_regions()
_REG_CHOICES = [(lbl, v) for v, lbl, _ in _REGIONS]   # (label, value) - matches game.throws
_REG_WEIGHTS = [w for *_, w in _REGIONS]


def _throw_one():
    """A single area-weighted dart: (label, value)."""
    return random.choices(_REG_CHOICES, weights=_REG_WEIGHTS, k=1)[0]


def _payout_mult(total: int) -> float:
    if total > int(getattr(config, "DARTS_BUST", 60)):
        return 0.0
    for lo, hi, mu in getattr(config, "DARTS_PAYOUTS", [(34, 43, 1.0), (44, 51, 2.0),
                                                        (52, 58, 4.0), (59, 60, 8.0)]):
        if lo <= total <= hi:
            return float(mu)
    return 0.0


def _paytable_line() -> str:
    parts = []
    for lo, hi, mu in getattr(config, "DARTS_PAYOUTS", []):
        rng = f"{lo}" if lo == hi else f"{lo}-{hi}"
        parts.append(f"{rng}→**{mu:g}×**")
    return " · ".join(parts) + f" · >{int(getattr(config,'DARTS_BUST',60))} bust"


# --- optional art (data/darts/<name>.png; text fallback if absent) ---------------
_ASSET_DIR = os.path.join("data", "darts")
_ASSET_PX = 320
_asset_cache = {}


def _asset_bytes(name: str):
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
        logger.debug("darts asset load failed: %s", name, exc_info=True)
    _asset_cache[name] = data
    return data


# --- live board compositing: stick the thrown darts into board.png -------------
# The dart sprites (data/darts/dart_NN.png) are full darts at assorted angles. We tip-anchor
# each onto the board at its scored region (number → angle, ring → radius), so the board shows
# your actual darts accumulating. Pure-PIL (no numpy); sprites + tips are cached on first use.
_BOARD_PX = 512
_DART_LEN = 178
_NUM_ORDER = [20, 1, 18, 4, 13, 6, 10, 15, 2, 17, 3, 19, 7, 16, 8, 11, 14, 9, 12, 5]
# Ring radius as a fraction of the board IMAGE width (calibrated to data/darts/board.png:
# bull ~0.07, treble ring ~0.25, single beds ~0.32, double ring ~0.37, surround ~0.44).
_RING_FRAC = {"Single": 0.32, "Treble": 0.25, "Double": 0.37}
_BULL_FRAC = 0.07
_MISS_FRAC = 0.44
_render_cache = {}


def _tip_of(sprite):
    """Pixel of the dart's TIP (its narrow point) in `sprite`. The two ends of the dart are the
    farthest-apart opaque pixels; the tip is whichever end has fewer neighbours (the flight end
    is wide). Analysed on a small alpha thumbnail, then scaled to the sprite - pure PIL."""
    W, H = sprite.size
    aw = 100
    ah = max(1, round(H * aw / W))
    a = sprite.getchannel("A").resize((aw, ah))
    px = a.load()
    pts = [(x, y) for y in range(ah) for x in range(aw) if px[x, y] > 40]
    if not pts:
        return (W / 2, H / 2)
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    far = lambda fx, fy: max(pts, key=lambda p: (p[0] - fx) ** 2 + (p[1] - fy) ** 2)
    p1 = far(cx, cy)
    p2 = far(*p1)
    r2 = (aw * 0.13) ** 2
    width = lambda p: sum(1 for q in pts if (q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2 < r2)
    tip = p1 if width(p1) <= width(p2) else p2
    return (tip[0] * W / aw, tip[1] * H / ah)


def _base_board():
    if "base" not in _render_cache:
        img = None
        try:
            path = os.path.join(_ASSET_DIR, "board.png")
            if os.path.exists(path):
                img = Image.open(path).convert("RGBA").resize((_BOARD_PX, _BOARD_PX), Image.LANCZOS)
        except Exception:
            logger.debug("darts base board load failed", exc_info=True)
        _render_cache["base"] = img
    return _render_cache["base"]


def _dart_sprites():
    """List of (scaled sprite, tip_xy), one per data/darts/dart_NN.png (cached)."""
    if "darts" not in _render_cache:
        out = []
        for i in range(1, 13):
            path = os.path.join(_ASSET_DIR, f"dart_{i:02d}.png")
            if not os.path.exists(path):
                continue
            try:
                im = Image.open(path).convert("RGBA")
                bbox = im.getbbox()
                if bbox:
                    im = im.crop(bbox)
                w, h = im.size
                s = _DART_LEN / max(w, h)
                im = im.resize((max(1, round(w * s)), max(1, round(h * s))), Image.LANCZOS)
                out.append((im, _tip_of(im)))
            except Exception:
                logger.debug("darts sprite load failed: %s", path, exc_info=True)
        _render_cache["darts"] = out
    return _render_cache["darts"]


def _dart_xy(label: str, rng) -> tuple:
    """Where on the board a dart with this region label lands (radius by ring, angle by number,
    with a little jitter kept inside the segment). Radii are fractions of the board width."""
    cx = cy = _BOARD_PX / 2.0
    W = _BOARD_PX
    if label.startswith("Bullseye"):
        r, ang = 0.0, rng.uniform(0, 2 * math.pi)
    elif label.startswith("Bull"):
        r, ang = _BULL_FRAC * W, rng.uniform(0, 2 * math.pi)
    elif label.startswith("missed"):
        r, ang = _MISS_FRAC * W, rng.uniform(0, 2 * math.pi)
    else:
        parts = label.split()
        try:
            kind, n = parts[0], int(parts[1])
            idx = _NUM_ORDER.index(n)
        except Exception:
            return cx, cy
        ang = math.radians(idx * 18) + rng.uniform(-0.07, 0.07)
        r = _RING_FRAC.get(kind, 0.32) * W * rng.uniform(0.96, 1.03)
    return cx + r * math.sin(ang), cy - r * math.cos(ang)


def _render_play_board(game):
    """Composite the thrown darts onto the board → PNG bytes. None if the art's missing."""
    base = _base_board()
    sprites = _dart_sprites()
    if base is None or not sprites:
        return None
    img = base.copy()
    try:
        seed0 = int(game.game_id, 16)
    except (ValueError, TypeError):
        seed0 = abs(hash(game.game_id))
    for i, (label, _v) in enumerate(game.throws):
        sp, (tx, ty) = sprites[i % len(sprites)]
        rng = random.Random((seed0 + i * 2654435761) & 0xFFFFFFFF)
        x, y = _dart_xy(label, rng)
        img.alpha_composite(sp, (round(x - tx), round(y - ty)))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class DartsGame:
    """One round of Darts. `throws` is the list of (label, value) thrown so far."""

    def __init__(self, game_id, player_id, player_name, channel_id, bet, *,
                 throws=None, state="playing", result=None, payout=0, message_id=None):
        self.game_id = game_id
        self.player_id = int(player_id)
        self.player_name = player_name
        self.channel_id = channel_id
        self.bet = int(bet)
        self.throws = [tuple(t) for t in (throws or [])]   # [(label, value), ...]
        self.state = state                  # playing | done
        self.result = result                # None | "win" | "short" | "bust"
        self.payout = int(payout)
        self.message_id = message_id
        self.message = None              # transient: the discord.Message (for edit fallbacks)
        self.busy = False
        self.replayed = False

    @classmethod
    def new(cls, player_id, player_name, channel_id, bet):
        return cls(__import__("uuid").uuid4().hex[:12], player_id, player_name, channel_id, bet)

    # --- maths/transitions (sync, await-free) ---
    @property
    def total(self) -> int:
        return sum(v for _, v in self.throws)

    @property
    def darts(self) -> int:
        return len(self.throws)

    def payout_now(self) -> int:
        return int(self.bet * _payout_mult(self.total))

    def can_act(self) -> bool:
        return self.state == "playing"

    def throw_dart(self) -> str:
        """Throw one dart. Returns 'thrown' | 'bust' | 'max' (auto-stood on the last dart)."""
        if self.state != "playing":
            return "noop"
        self.throws.append(_throw_one())
        if self.total > int(getattr(config, "DARTS_BUST", 60)):
            self.state = "done"
            self.result = "bust"
            self.payout = 0
            return "bust"
        if self.darts >= int(getattr(config, "DARTS_DARTS", 3)):
            self.stand()                    # out of darts - forced to stand
            return "max"
        return "thrown"

    def stand(self) -> int:
        if self.state != "playing":
            return self.payout
        mult = _payout_mult(self.total)
        self.payout = int(self.bet * mult)
        self.state = "done"
        self.result = "win" if mult >= 1.0 else "short"
        return self.payout

    # --- serialisation (running games persist + resume across a restart) ---
    def to_dict(self) -> dict:
        return {"type": "darts", "game_id": self.game_id, "player_id": self.player_id,
                "player_name": self.player_name, "channel_id": self.channel_id,
                "message_id": self.message_id, "bet": self.bet, "throws": self.throws,
                "state": self.state, "result": self.result, "payout": self.payout}

    @classmethod
    def from_dict(cls, d: dict):
        return cls(d["game_id"], d["player_id"], d.get("player_name", "Player"),
                   d.get("channel_id"), d["bet"], throws=d.get("throws", []),
                   state=d.get("state", "playing"), result=d.get("result"),
                   payout=d.get("payout", 0), message_id=d.get("message_id"))


def save_game(game: DartsGame):
    if game.message_id is not None and game.state == "playing":
        save_state(game.message_id, game.to_dict())


# ---------------------------------------------------------------------------
# Rendering (Components V2: optional art + status panel + Throw / Stand / Rules)
# ---------------------------------------------------------------------------
def _throws_line(game: DartsGame) -> str:
    return "  ·  ".join(f"🎯 {lbl} (**{v}**)" for lbl, v in game.throws) or "—"


def _status_text(game: DartsGame) -> str:
    bust = int(getattr(config, "DARTS_BUST", 60))
    floor = getattr(config, "DARTS_PAYOUTS", [(34,)])[0][0]
    darts_max = int(getattr(config, "DARTS_DARTS", 3))
    if game.state == "done":
        if game.result == "bust":
            return (f"## 💥 Bust on {game.total}!\n{_throws_line(game)}\n"
                    f"Went over **{bust}** - lost **{game.bet:,} UKPence**.")
        if game.result == "short":
            return (f"## 🎯 Fell short on {game.total}\n{_throws_line(game)}\n"
                    f"Needed **{floor}+** to win - lost **{game.bet:,} UKPence**.")
        mult = _payout_mult(game.total)
        return (f"## 🎯 Stood on {game.total} — {mult:g}× win!\n{_throws_line(game)}\n"
                f"Banked **{game.payout:,} UKPence**. 🎉")
    # playing
    if game.darts == 0:
        return (f"## 🎯 Darts\n"
                f"Stake **{game.bet:,}**. Get as close to **{bust}** as you dare without going "
                f"over - the higher you finish, the bigger the win.\n"
                f"**🎯 Throw** your first dart (up to {darts_max}).\n"
                f"-# pays from {floor}+:  {_paytable_line()}")
    mult = _payout_mult(game.total)
    if mult >= 1.0:
        line = (f"**✋ Stand** to win **{game.payout_now():,}** ({mult:g}×)   or   "
                f"**🎯 Throw** to climb _(over {bust} busts)_")
    else:
        line = f"Need **{floor}+** to win - **🎯 Throw** again _(over {bust} busts)_"
    return (f"## 🎯 Darts — score {game.total}\n"
            f"{_throws_line(game)}  ·  dart {game.darts}/{darts_max}\n"
            f"{line}\n"
            f"-# {_paytable_line()}")


def _board_image(game: DartsGame):
    # Always show the live board with your ACTUAL darts stuck where they landed - including the
    # final frame, so it reflects what you threw. Falls back to static art, then text.
    composite = _render_play_board(game)
    if composite is not None:
        return composite, "darts.png"
    if game.state == "done":
        return (_asset_bytes("win") if game.result == "win" else _asset_bytes("bust")), "darts.png"
    return _asset_bytes("board"), "darts.png"


def _throw_button(game: DartsGame) -> discord.ui.Button:
    btn = discord.ui.Button(style=discord.ButtonStyle.primary, label="Throw Dart", emoji="🎯",
                            custom_id=f"darts:{game.game_id}:throw")
    btn.callback = _make_throw_cb(game)
    return btn


def _stand_button(game: DartsGame) -> discord.ui.Button:
    can_score = _payout_mult(game.total) >= 1.0
    label = (f"Stand  {game.payout_now():,}" if can_score else "Stand")
    btn = discord.ui.Button(style=discord.ButtonStyle.success, label=label, emoji="✋",
                            custom_id=f"darts:{game.game_id}:stand")
    btn.callback = _make_stand_cb(game)
    return btn


def _rules_button(game: DartsGame) -> discord.ui.Button:
    btn = discord.ui.Button(style=discord.ButtonStyle.secondary, label="Rules", emoji="📖",
                            custom_id=f"darts:{game.game_id}:rules")
    btn.callback = _show_rules
    return btn


def _again_button(game: DartsGame) -> discord.ui.Button:
    btn = discord.ui.Button(style=discord.ButtonStyle.primary, label="Play Again", emoji="🔁",
                            custom_id=f"darts:{game.game_id}:again")
    btn.callback = _make_again_cb(game)
    return btn


def _build(game: DartsGame):
    """Return (view, files)."""
    view = discord.ui.LayoutView(timeout=None)
    files = []
    data, fname = _board_image(game)
    if data is not None:
        files = [discord.File(io.BytesIO(data), filename=fname)]
        gallery = discord.ui.MediaGallery()
        gallery.add_item(media=f"attachment://{fname}")
        view.add_item(gallery)
    box = discord.ui.Container(accent_colour=ACCENT)
    box.add_item(discord.ui.TextDisplay(_status_text(game)))
    view.add_item(box)
    controls = discord.ui.ActionRow()
    if game.state == "playing":
        controls.add_item(_throw_button(game))
        if game.darts >= 1:                      # nothing to stand on until you've thrown
            controls.add_item(_stand_button(game))
    else:
        controls.add_item(_again_button(game))
    controls.add_item(_rules_button(game))
    view.add_item(controls)
    return view, files


# ---------------------------------------------------------------------------
# Settlement
# ---------------------------------------------------------------------------
def _settle(game: DartsGame):
    delete_state(game.message_id)
    if game.payout > 0:
        credit_from_bank(game.player_id, game.payout, reason="Darts win")
    outcome = "win" if game.payout > game.bet else ("push" if game.payout == game.bet else "lose")
    record_result(game.player_id, "darts", game.bet, game.bet, game.payout, outcome)


async def _rerender(interaction: Interaction, game: DartsGame):
    view, files = _build(game)
    try:
        await interaction.response.edit_message(view=view, attachments=files)
    except (discord.NotFound, discord.InteractionResponded):
        try:
            target = game.message if getattr(game, "message", None) else interaction.message
            if target is not None:
                await target.edit(view=view, attachments=files)
        except discord.HTTPException:
            logger.debug("darts fallback edit failed", exc_info=True)
    try:
        interaction.client.add_view(view, message_id=game.message_id)
    except Exception:
        logger.debug("darts add_view failed", exc_info=True)


# ---------------------------------------------------------------------------
# Interaction handling
# ---------------------------------------------------------------------------
def _make_throw_cb(game):
    async def _cb(interaction: Interaction):
        with action_in_flight():
            await _handle_throw(interaction, game)
    return _cb


def _make_stand_cb(game):
    async def _cb(interaction: Interaction):
        with action_in_flight():
            await _handle_stand(interaction, game)
    return _cb


def _make_again_cb(game):
    async def _cb(interaction: Interaction):
        with action_in_flight():
            await _handle_again(interaction, game)
    return _cb


def _not_your_game(interaction: Interaction, game: DartsGame) -> bool:
    return interaction.user.id != game.player_id


async def _handle_throw(interaction: Interaction, game: DartsGame):
    if _not_your_game(interaction, game):
        await interaction.response.send_message(
            "This isn't your game - start your own with `/darts`.", ephemeral=True)
        return
    if game.busy or game.state != "playing":
        await interaction.response.defer()
        return
    game.busy = True
    try:
        result = game.throw_dart()
        if result in ("bust", "max"):
            _settle(game)
        else:
            save_game(game)
        await _rerender(interaction, game)
    finally:
        game.busy = False


async def _handle_stand(interaction: Interaction, game: DartsGame):
    if _not_your_game(interaction, game):
        await interaction.response.send_message(
            "This isn't your game - start your own with `/darts`.", ephemeral=True)
        return
    if game.busy or game.state != "playing":
        await interaction.response.defer()
        return
    game.busy = True
    try:
        game.stand()
        _settle(game)
        await _rerender(interaction, game)
    finally:
        game.busy = False


async def _handle_again(interaction: Interaction, old_game: DartsGame):
    if _not_your_game(interaction, old_game):
        await interaction.response.send_message(
            "This isn't your game - start your own with `/darts`.", ephemeral=True)
        return
    if old_game.replayed:
        await interaction.response.defer()
        return
    old_game.replayed = True
    bet = old_game.bet
    min_bet = getattr(config, "DARTS_MIN_BET", 5)
    max_bet = getattr(config, "DARTS_MAX_BET", 1_000)
    if await reject_if_maintenance(interaction):
        old_game.replayed = False
        return
    if not getattr(config, "DARTS_ENABLED", True):
        old_game.replayed = False
        await interaction.response.send_message("The darts board is closed.", ephemeral=True)
        return
    if bet < min_bet or bet > max_bet:
        old_game.replayed = False
        await interaction.response.send_message(
            f"Bets must be between {min_bet:,} and {max_bet:,} UKPence.", ephemeral=True)
        return
    if get_bb(old_game.player_id) < bet or not remove_bb(old_game.player_id, bet, reason="Darts bet"):
        old_game.replayed = False
        await interaction.response.send_message(
            f"You need {bet:,} UKPence to play again.", ephemeral=True)
        return

    game = DartsGame.new(old_game.player_id, old_game.player_name, old_game.channel_id, bet)
    game.message_id = old_game.message_id
    save_game(game)                                    # fresh board; the player throws each dart
    view, files = _build(game)
    try:
        await interaction.response.edit_message(view=view, attachments=files)
    except (discord.NotFound, discord.InteractionResponded):
        try:
            await (old_game.message or interaction.message).edit(view=view, attachments=files)
        except discord.HTTPException:
            logger.error("Darts replay failed; refunding stake.")
            credit_from_bank(game.player_id, bet, "Darts stake refund (replay failed)")
            delete_state(game.message_id)
            old_game.replayed = False
            return
    try:
        interaction.client.add_view(view, message_id=game.message_id)
    except Exception:
        logger.debug("darts replay add_view failed", exc_info=True)


async def _show_rules(interaction: Interaction):
    min_bet = getattr(config, "DARTS_MIN_BET", 5)
    max_bet = getattr(config, "DARTS_MAX_BET", 1_000)
    bust = int(getattr(config, "DARTS_BUST", 60))
    darts = int(getattr(config, "DARTS_DARTS", 3))
    floor = getattr(config, "DARTS_PAYOUTS", [(34,)])[0][0]
    rules = (
        "## 🎯 Darts - House Rules\n"
        f"Like blackjack with darts: **get as close to {bust} as you can without going over.** "
        f"Throw darts to build your score, then **✋ Stand** to bank it - the higher you finish, "
        f"the bigger the payout. Go **over {bust}** and you **bust** (lose the stake).\n\n"
        f"- **🎯 Throw** to add a dart (up to **{darts}**); **✋ Stand** any time to lock your score. "
        f"After the {darts}rd dart you stand automatically.\n"
        f"- **Payout by final score:** {_paytable_line()}.  Finish under **{floor}** and you lose.\n"
        f"- Each dart hits a real, **area-weighted** board - singles are common, **trebles & "
        "doubles rare** (less space) but worth far more, so they're the jackpot *and* the trap.\n"
        f"- **Bets:** {min_bet:,} - {max_bet:,} UKPence. Stakes go to the house bank; wins paid from it.\n\n"
        "-# Tip: a score in the high 50s is the sweet spot - push too hard and you sail past 60. 🇬🇧"
    )
    await interaction.response.send_message(rules, ephemeral=True)


# ---------------------------------------------------------------------------
# Command entry
# ---------------------------------------------------------------------------
@deal_in_flight
async def handle_darts_command(interaction: Interaction, amount: int):
    if await reject_if_maintenance(interaction):
        return
    if not getattr(config, "DARTS_ENABLED", True):
        await interaction.response.send_message("The darts board is closed.", ephemeral=True)
        return
    min_bet = getattr(config, "DARTS_MIN_BET", 5)
    max_bet = getattr(config, "DARTS_MAX_BET", 1_000)
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
    if not remove_bb(interaction.user.id, amount, reason="Darts bet"):
        await interaction.response.send_message(
            f"You don't have enough UKPence. Your balance is {get_bb(interaction.user.id):,}.", ephemeral=True)
        return

    name = discord.utils.escape_markdown(interaction.user.display_name)
    game = DartsGame.new(interaction.user.id, name, interaction.channel_id, amount)
    try:
        await interaction.response.defer(thinking=True)
        view, files = _build(game)
        msg = await interaction.followup.send(view=view, files=files)
    except Exception:
        logger.error("Darts deal failed; refunding stake.", exc_info=True)
        credit_from_bank(interaction.user.id, amount, "Darts stake refund (deal failed)")
        try:
            await interaction.followup.send(
                "Something went wrong at the oche - your stake has been refunded.", ephemeral=True)
        except Exception:
            pass
        return

    game.message_id = msg.id
    try:
        if game.state == "playing":
            save_game(game)
            interaction.client.add_view(view, message_id=msg.id)
        else:
            delete_state(msg.id)
    except Exception:
        logger.error("Darts post-send persistence issue (game is live).", exc_info=True)


# ---------------------------------------------------------------------------
# Restart recovery (called from event_handlers.reattach_persistent_views)
# ---------------------------------------------------------------------------
def reattach_darts_view(client, key, value):
    """Re-register click routing for an in-play game after a restart (fully serialised, so it
    resumes). Terminal/malformed entries are pruned."""
    try:
        game = DartsGame.from_dict(value)
    except Exception as e:
        logger.error(f"Pruning malformed darts entry {key}: {e}", exc_info=True)
        delete_state(key)
        return
    if game.state != "playing":
        delete_state(key)
        return
    try:
        game.message_id = int(key)
        view, _files = _build(game)
        client.add_view(view, message_id=int(key))
    except Exception as e:
        logger.error(f"Failed to reattach darts view {key}: {e}", exc_info=True)
