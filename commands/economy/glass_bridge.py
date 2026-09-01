"""The Glass Bridge - a coin-flip ladder across a canyon, for UKPence.

Eight pairs of panels. One of each pair is tempered and holds; the other is fragile and
drops you. Pick ⬅️ or ➡️ on every step. Each panel crossed nearly doubles the payout, and
Cash Out banks it any time before the glass goes.

Money flow (as every other casino game; the fixed UKP supply is conserved):
    • Stake:  remove_bb(uid, bet)                  - the stake enters the house bank.
    • Win:    credit_from_bank(uid, stake x mult)  - paid out of the bank.
    • Loss:   nothing paid - the stake simply stays in the bank.

Fairness. The multiplier is DERIVED rather than tabulated: each step is a straight 50/50,
so the fair payout doubles, and the house takes a flat GLASS_HOUSE_EDGE off each doubling:

    multiplier(n) = (2 * (1 - edge)) ** n

At the default 4% that is 1.92, 3.69, 7.08, 13.59, 26.09, 50.10, 96.19, 184.68. Every step
is EV = (1 - edge) of what you are holding, so the edge is identical wherever you stop and
there is no clever place to get off - the only thing you control is variance.

The safe side of all eight pairs is rolled ONCE when the game is dealt and persisted with
the board. Rolling per step would be indistinguishable to the player but means the bridge
is not a fixed thing they are crossing, and a resumed game after a restart could silently
be a different bridge.
"""
import io
import logging
import random
import uuid

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

LEFT, RIGHT = "L", "R"


# --- config helpers --------------------------------------------------------
def _steps() -> int:
    return int(getattr(config, "GLASS_STEPS", 8))


def _edge() -> float:
    return float(getattr(config, "GLASS_HOUSE_EDGE", 0.04))


def multiplier_for(step: int) -> float:
    """Payout multiplier after `step` panels crossed. 0 steps is 0x - stepping onto the
    bridge and stopping is not a cash-out, it is not having played."""
    if step <= 0:
        return 0.0
    return (2.0 * (1.0 - _edge())) ** step


class GlassBridgeGame:
    """One crossing. `bridge` is the safe side of each pair and never leaves the server
    until the panel is stepped on or the game ends."""

    def __init__(self, game_id, player_id, player_name, channel_id, bet, *,
                 bridge=None, step=0, state="playing", outcome=None, payout=0,
                 fell_on=None, message_id=None):
        self.game_id = game_id
        self.player_id = int(player_id)
        self.player_name = player_name
        self.channel_id = channel_id
        self.bet = int(bet)
        self.bridge = list(bridge) if bridge else [
            random.choice((LEFT, RIGHT)) for _ in range(_steps())
        ]
        self.step = int(step)              # panels safely crossed
        self.state = state                 # "playing" | "over"
        self.outcome = outcome             # None | "win" | "lose"
        self.payout = int(payout)
        self.fell_on = fell_on             # the side they picked when it shattered
        self.message_id = message_id
        # transient (never serialised)
        self.busy = False
        self.replayed = False

    @classmethod
    def new(cls, player_id, player_name, channel_id, bet):
        return cls(uuid.uuid4().hex[:12], player_id, player_name, channel_id, bet)

    # --- maths ---
    def multiplier(self, step=None) -> float:
        return multiplier_for(self.step if step is None else step)

    def payout_for(self, step=None) -> int:
        raw = int(self.bet * self.multiplier(step))
        cap = int(getattr(config, "GLASS_MAX_WIN", 0) or 0)
        return raw if cap <= 0 else min(raw, cap)

    def current_payout(self) -> int:
        return self.payout_for(self.step)

    def across(self) -> bool:
        return self.step >= _steps()

    def safe_side(self, step_index: int):
        return self.bridge[step_index] if 0 <= step_index < len(self.bridge) else None

    # --- transitions ---
    def take_step(self, side: str) -> str:
        """Step onto one panel. Returns 'on' | 'across' | 'fell' | 'ignore'."""
        if self.state != "playing" or self.across():
            return "ignore"
        if side != self.safe_side(self.step):
            self.fell_on = side
            self.state = "over"
            self.outcome = "lose"
            return "fell"
        self.step += 1
        if self.across():
            self.cash_out()               # the far side banks automatically
            return "across"
        return "on"

    def cash_out(self) -> int:
        self.payout = self.current_payout()
        self.state = "over"
        self.outcome = "win"
        return self.payout

    # --- serialisation (only in-play games are persisted) ---
    def to_dict(self) -> dict:
        return {
            "type": "glass", "game_id": self.game_id, "player_id": self.player_id,
            "player_name": self.player_name, "channel_id": self.channel_id,
            "message_id": self.message_id, "bet": self.bet, "bridge": self.bridge,
            "step": self.step, "state": self.state, "outcome": self.outcome,
            "payout": self.payout, "fell_on": self.fell_on,
        }

    @classmethod
    def from_dict(cls, d: dict):
        return cls(
            game_id=d["game_id"], player_id=d["player_id"],
            player_name=d.get("player_name", "Player"), channel_id=d.get("channel_id"),
            bet=d["bet"], bridge=d.get("bridge"), step=d.get("step", 0),
            state=d.get("state", "playing"), outcome=d.get("outcome"),
            payout=d.get("payout", 0), fell_on=d.get("fell_on"),
            message_id=d.get("message_id"),
        )


def save_game(game: GlassBridgeGame):
    if game.message_id is not None:
        save_state(game.message_id, game.to_dict())


# ---------------------------------------------------------------------------
# The board picture
# ---------------------------------------------------------------------------
# Drawn with PIL rather than the HTML->Chrome path the felt-table games use. A crossing
# redraws on every panel - up to eight times a game, and again on Play Again - while a
# blackjack table redraws a handful of times. Chrome renders share one global
# Semaphore(1) with slots, rank cards and the summaries, so each redraw would queue
# behind whatever else the casino was doing. Straight PIL takes no lock and starts no
# browser (the crossword board went the same way, for the same reason).
_FONT_CANDIDATES = {
    "bold": (
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ),
    "regular": (
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ),
}
_font_cache = {}

_INK = "#e8edf7"
_DIM = "#7b879c"


def _font(weight: str, size: int):
    """A cached truetype face, falling back to PIL's bitmap default if none is installed -
    the board still draws, it just looks plainer."""
    key = (weight, size)
    if key in _font_cache:
        return _font_cache[key]
    from PIL import ImageFont
    face = None
    for path in _FONT_CANDIDATES[weight]:
        try:
            face = ImageFont.truetype(path, size)
            break
        except Exception:
            continue
    if face is None:
        face = ImageFont.load_default()
    _font_cache[key] = face
    return face


def _pane(dr, box, fill, edge, *, cracked=False, glow=None):
    """One glass panel: a rounded pane with a highlight streak, optionally shattered."""
    x0, y0, x1, y1 = box
    if glow:
        for i, w in ((10, 2), (5, 3)):
            dr.rounded_rectangle([x0 - i, y0 - i, x1 + i, y1 + i], 20, outline=glow, width=w)
    dr.rounded_rectangle(box, 16, fill=fill, outline=edge, width=4)
    # a diagonal streak, so the panes read as glass rather than as tiles
    dr.line([(x0 + 22, y1 - 26), (x1 - 34, y0 + 22)], fill=edge, width=4)
    if cracked:
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        # 0.40 rather than 0.5: at half the pane the lines run past the rounded corners and
        # the break looks like it happened to the board rather than to the panel.
        rx, ry = (x1 - x0) * 0.40, (y1 - y0) * 0.40
        for dx, dy in ((-1, -.7), (1, -.6), (-.9, .8), (1, .9), (.15, -1), (-.2, 1), (-1, .1)):
            dr.line([(cx, cy), (cx + dx * rx, cy + dy * ry)], fill="#ff5b5b", width=4)
        dr.ellipse([cx - 9, cy - 9, cx + 9, cy + 9], fill="#ff5b5b")


def draw_board(game: "GlassBridgeGame"):
    """The crossing as a tall PNG: two columns of panes, one row per pair, the first panel
    at the bottom and the far side at the top.

    Portrait and oversized on purpose. Discord downscales a wide image hard on mobile, and
    this is a board somebody reads mid-decision - so it carries the multipliers and nothing
    else. Everything a number could tell them is in the message text underneath.
    """
    from PIL import Image, ImageDraw

    steps = _steps()
    W, PAD = 820, 40
    GUTTER, COLGAP = 208, 22             # room for the multiplier, gap between the pair
    RH, RGAP = 104, 14                   # row height and the gap between rows
    FINISH, FGAP = 74, 26

    grid_h = steps * RH + (steps - 1) * RGAP
    H = PAD * 2 + FINISH + FGAP + grid_h
    pane_w = (W - PAD * 2 - GUTTER - COLGAP) // 2
    lx = PAD + GUTTER
    rx = lx + pane_w + COLGAP

    img = Image.new("RGB", (W, H), "#070b14")
    dr = ImageDraw.Draw(img)
    f_mult, f_done = _font("bold", 42), _font("bold", 34)

    # the far side, a checked band rather than a caption
    done = game.across()
    fy = PAD
    edge = "#3ddc84" if done else "#1e2b45"
    dr.rounded_rectangle([lx, fy, rx + pane_w, fy + FINISH], 16,
                         fill="#14432c" if done else "#0d1424", outline=edge, width=4)
    sq, inset = FINISH // 3, 8
    band_r, band_b = rx + pane_w - inset, fy + FINISH - inset
    for i in range((band_r - lx - inset) // sq + 1):
        for j in range(3):
            if (i + j) % 2:
                x = lx + inset + i * sq
                y = fy + inset + j * sq
                if x >= band_r or y >= band_b:
                    continue
                dr.rectangle([x, y, min(x + sq, band_r), min(y + sq, band_b)], fill=edge)

    for i in range(steps):
        # bottom-up: the first panel is nearest, the far side is at the top
        y0 = PAD + FINISH + FGAP + (steps - 1 - i) * (RH + RGAP)
        y1 = y0 + RH
        left, right = (lx, y0, lx + pane_w, y1), (rx, y0, rx + pane_w, y1)
        safe_left = game.safe_side(i) == LEFT
        crossed = i < game.step
        fatal = (game.state == "over" and game.outcome == "lose" and i == game.step)
        live = i == game.step and game.state == "playing"

        if crossed:
            # Only the pane actually stood on is revealed - which of the pair the other one
            # was is the one thing the player never found out.
            _pane(dr, left if safe_left else right, "#14432c", "#3ddc84")
            _pane(dr, right if safe_left else left, "#0f1626", "#1e2b45")
        elif fatal:
            broke_left = game.fell_on == LEFT
            _pane(dr, left if broke_left else right, "#3a1420", "#ff5b5b", cracked=True)
            _pane(dr, right if broke_left else left, "#14432c", "#3ddc84")
        elif live:
            _pane(dr, left, "#16233d", "#7fb2ff", glow="#24395f")
            _pane(dr, right, "#16233d", "#7fb2ff", glow="#24395f")
        else:
            _pane(dr, left, "#0f1626", "#1e2b45")
            _pane(dr, right, "#0f1626", "#1e2b45")

        lit = ("#e8edf7" if crossed else "#7fb2ff" if live
               else "#ff5b5b" if fatal else "#4a5670")
        dr.text((PAD + GUTTER - 34, (y0 + y1) / 2), f"{multiplier_for(i + 1):.2f}x",
                font=f_mult, fill=lit, anchor="rm")

    if done:
        dr.text((PAD + GUTTER - 34, fy + FINISH / 2), "ACROSS", font=f_done,
                fill="#3ddc84", anchor="rm")

    buf = io.BytesIO()
    # Palette PNG: a couple of dozen flat colours, so 64 of them is the same picture at a
    # fraction of the bytes - and the bytes are the upload, which is the leg anybody waits
    # on (measured at 700ms to 1.6s on this box).
    img.convert("P", palette=Image.ADAPTIVE, colors=64).save(buf, format="PNG", optimize=False)
    buf.seek(0)
    return buf


def board_file(game: "GlassBridgeGame"):
    """(files, filename) for the board, or ([], None) if pictures are off or the draw fails.
    Never raises - a board that will not draw falls back to the text layout rather than
    costing somebody their crossing."""
    if not getattr(config, "GLASS_IMAGE_ENABLED", True):
        return [], None
    try:
        return [discord.File(draw_board(game), filename="glassbridge.png")], "glassbridge.png"
    except Exception:
        logger.error("Glass Bridge board render failed", exc_info=True)
        return [], None


# ---------------------------------------------------------------------------
# Rendering (Components V2: a walkway strip, a status panel, and the controls)
# ---------------------------------------------------------------------------
def _walkway(game: GlassBridgeGame) -> str:
    """The bridge itself, one cell per pair. Crossed panels are lit, the one under your
    foot is picked out, the rest are unknown - and the panel that dropped you shows where
    it was, because being told which way you should have gone is the whole sting."""
    cells = []
    for i in range(_steps()):
        if i < game.step:
            cells.append("🟩")
        elif game.state == "over" and game.outcome == "lose" and i == game.step:
            cells.append("💥")
        elif i == game.step:
            cells.append("🟦")
        else:
            cells.append("⬛")
    return "🧍 " + " ".join(cells) + " 🏁"


def _status_text(game: GlassBridgeGame, walkway: bool = True) -> str:
    total = _steps()
    if game.state == "over" and game.outcome == "lose":
        should = "left" if game.safe_side(game.step) == LEFT else "right"
        got_to = (f"You had crossed **{game.step}** of {total}"
                  if game.step else "You did not make it off the first pair")
        return (f"## 💥 The Glass Bridge - the glass went\n"
                f"{_walkway(game) + chr(10) + chr(10) if walkway else ''}"
                f"Panel **{game.step + 1}** was tempered on the **{should}**. "
                f"{got_to} and lost **{game.bet:,} UKPence**.\n"
                f"-# You were holding **{game.payout_for(game.step):,}** before that step.")
    if game.state == "over":
        crossed = ("You crossed the whole bridge" if game.across()
                   else f"You stopped on panel **{game.step}** of {total}")
        return (f"## 🪟 The Glass Bridge - {'Across!' if game.across() else 'Cashed Out'}\n"
                f"{_walkway(game) + chr(10) + chr(10) if walkway else ''}"
                f"{crossed} and banked **{game.payout:,} UKPence** "
                f"({game.multiplier():.2f}×).")

    nxt = game.step + 1
    held = (f"Cash out now for **{game.current_payout():,} UKPence** "
            f"({game.multiplier():.2f}×)." if game.step else
            "Nothing banked yet - the first panel is where it starts.")
    return (f"## 🪟 The Glass Bridge\n"
            f"{_walkway(game) + chr(10) + chr(10) if walkway else ''}"
            f"**Panel {nxt} of {total}.** One side is tempered, the other is fragile.\n"
            f"{held}\n"
            f"Cross it and you are holding **{game.payout_for(nxt):,}** "
            f"({multiplier_for(nxt):.2f}×).\n"
            f"-# 50/50. Pick wrong and the stake is gone.")


def _side_button(game: GlassBridgeGame, side: str) -> discord.ui.Button:
    left = side == LEFT
    btn = discord.ui.Button(
        style=discord.ButtonStyle.primary,
        label="Left Panel" if left else "Right Panel",
        emoji="⬅️" if left else "➡️",
        custom_id=f"glass:{game.game_id}:{side}",
    )
    btn.callback = _make_step_cb(game, side)
    return btn


def _cash_button(game: GlassBridgeGame) -> discord.ui.Button:
    btn = discord.ui.Button(
        style=discord.ButtonStyle.success,
        label=f"Cash Out  {game.current_payout():,}", emoji="💰",
        custom_id=f"glass:{game.game_id}:cash",
    )
    btn.callback = _make_cash_cb(game)
    return btn


def _rules_button(game: GlassBridgeGame) -> discord.ui.Button:
    btn = discord.ui.Button(
        style=discord.ButtonStyle.secondary, label="Rules", emoji="📖",
        custom_id=f"glass:{game.game_id}:rules",
    )
    btn.callback = _show_rules
    return btn


def _again_button(game: GlassBridgeGame) -> discord.ui.Button:
    btn = discord.ui.Button(
        style=discord.ButtonStyle.primary, label="Play Again", emoji="🔁",
        custom_id=f"glass:{game.game_id}:again",
    )
    btn.callback = _make_again_cb(game)
    return btn


def build_glass_layout(game: GlassBridgeGame):
    """Return (view, files). Files must be re-sent on every edit (attachments=files) or
    the board loses its picture the first time somebody steps."""
    view = discord.ui.LayoutView(timeout=None)
    files, fname = board_file(game)
    if fname:
        # The picture sits outside the Container - it draws its own frame and an accent rail
        # around it reads as a redundant embed - but the text still wants the box, or it
        # floats loose under the board with nothing holding it together.
        gallery = discord.ui.MediaGallery()
        gallery.add_item(media=f"attachment://{fname}")
        view.add_item(gallery)
    box = discord.ui.Container(accent_colour=ACCENT)
    box.add_item(discord.ui.TextDisplay(_status_text(game, walkway=not fname)))
    view.add_item(box)
    controls = discord.ui.ActionRow()
    if game.state == "over":
        controls.add_item(_again_button(game))
    else:
        controls.add_item(_side_button(game, LEFT))
        controls.add_item(_side_button(game, RIGHT))
        # Nothing is banked until a panel is crossed, so offering Cash Out on step one
        # would just be a button that returns zero.
        if game.step > 0:
            controls.add_item(_cash_button(game))
    controls.add_item(_rules_button(game))
    view.add_item(controls)
    return view, files


# ---------------------------------------------------------------------------
# Interaction handling
# ---------------------------------------------------------------------------
def _make_step_cb(game: GlassBridgeGame, side: str):
    async def _cb(interaction: Interaction):
        with action_in_flight():
            await _handle_step(interaction, game, side)
    return _cb


def _make_cash_cb(game: GlassBridgeGame):
    async def _cb(interaction: Interaction):
        with action_in_flight():
            await _handle_cashout(interaction, game)
    return _cb


def _make_again_cb(old_game: GlassBridgeGame):
    async def _cb(interaction: Interaction):
        with action_in_flight():
            await _handle_again(interaction, old_game)
    return _cb


async def _safe_edit_board(interaction: Interaction, view, files=None) -> bool:
    """Refresh the board, surviving a dead interaction token (mirrors chest/mines)."""
    try:
        await interaction.response.edit_message(view=view, attachments=files or [])
        return True
    except (discord.NotFound, discord.InteractionResponded):
        try:
            if interaction.message is not None:
                await interaction.message.edit(view=view, attachments=files or [])
                return True
        except discord.HTTPException:
            logger.debug("Glass Bridge fallback edit failed", exc_info=True)
    except discord.HTTPException:
        logger.debug("Glass Bridge board edit failed", exc_info=True)
    return False


async def _rerender(interaction: Interaction, game: GlassBridgeGame):
    view, files = build_glass_layout(game)
    await _safe_edit_board(interaction, view, files)
    if game.message_id is not None:
        try:
            interaction.client.add_view(view, message_id=game.message_id)
        except Exception:
            logger.debug("Glass Bridge add_view after refresh failed", exc_info=True)


def _not_your_game(interaction: Interaction, game: GlassBridgeGame) -> bool:
    return interaction.user.id != game.player_id


async def _handle_step(interaction: Interaction, game: GlassBridgeGame, side: str):
    if _not_your_game(interaction, game):
        await interaction.response.send_message(
            "This isn't your crossing - start your own with `/glassbridge`.", ephemeral=True)
        return
    # Read-then-set with no await between = atomic on the event loop: one click wins.
    if game.busy or game.state != "playing":
        await interaction.response.defer()
        return
    game.busy = True
    try:
        result = game.take_step(side)
        if result == "ignore":
            await interaction.response.defer()
            return
        if result == "across":
            # Delete-before-credit so an interruption can never leave a board that is both
            # paid and resumable, which would mint UKP on the next boot.
            delete_state(game.message_id)
            credit_from_bank(game.player_id, game.payout, reason="Glass Bridge win (crossed)")
            record_result(game.player_id, "glass", game.bet, game.bet, game.payout, "win")
        elif result == "fell":
            delete_state(game.message_id)   # stake is already in the bank; nothing to pay
            record_result(game.player_id, "glass", game.bet, game.bet, 0, "lose")
        else:
            save_game(game)
        await _rerender(interaction, game)
    finally:
        game.busy = False


async def _handle_cashout(interaction: Interaction, game: GlassBridgeGame):
    if _not_your_game(interaction, game):
        await interaction.response.send_message(
            "This isn't your crossing - start your own with `/glassbridge`.", ephemeral=True)
        return
    if game.busy or game.state != "playing" or game.step <= 0:
        await interaction.response.defer()
        return
    game.busy = True
    try:
        payout = game.cash_out()
        delete_state(game.message_id)       # delete-before-credit, see _handle_step
        credit_from_bank(game.player_id, payout, reason="Glass Bridge cashout")
        record_result(game.player_id, "glass", game.bet, game.bet, payout, "win")
        await _rerender(interaction, game)
    finally:
        game.busy = False


async def _handle_again(interaction: Interaction, old_game: GlassBridgeGame):
    """Play Again: a fresh bridge on the same message at the previous stake."""
    if interaction.user.id != old_game.player_id:
        await interaction.response.send_message(
            "This isn't your crossing - start your own with `/glassbridge`.", ephemeral=True)
        return
    if old_game.replayed:
        await interaction.response.defer()
        return
    if await reject_if_maintenance(interaction):
        return
    if not getattr(config, "GLASS_ENABLED", True):
        await interaction.response.send_message("The bridge is closed.", ephemeral=True)
        return
    bet = old_game.bet
    min_bet = int(getattr(config, "GLASS_MIN_BET", 5))
    max_bet = int(getattr(config, "GLASS_MAX_BET", 500))
    if bet < min_bet or bet > max_bet:
        await interaction.response.send_message(
            f"Bets must be between {min_bet:,} and {max_bet:,} UKPence.", ephemeral=True)
        return
    if get_bb(old_game.player_id) < bet:
        await interaction.response.send_message(
            f"You need {bet:,} UKPence to play again.", ephemeral=True)
        return
    if not remove_bb(old_game.player_id, bet, reason="Glass Bridge bet"):
        await interaction.response.send_message(
            "You don't have enough UKPence.", ephemeral=True)
        return
    # Claim the replay before the first await so two fast clicks can't both deal.
    old_game.replayed = True

    new_game = GlassBridgeGame.new(old_game.player_id, old_game.player_name,
                                   old_game.channel_id, bet)
    new_game.message_id = old_game.message_id
    view, files = build_glass_layout(new_game)
    if not await _safe_edit_board(interaction, view, files):
        logger.error("Glass Bridge replay failed before showing the board; refunding.")
        credit_from_bank(old_game.player_id, bet, "Glass Bridge stake refund (replay failed)")
        old_game.replayed = False
        return
    try:
        save_game(new_game)
        interaction.client.add_view(view, message_id=new_game.message_id)
    except Exception:
        logger.error("Glass Bridge replay post-update issue (board is live).", exc_info=True)


async def _show_rules(interaction: Interaction):
    """Ephemeral house rules. Open to anyone and changes no state."""
    total = _steps()
    min_bet = int(getattr(config, "GLASS_MIN_BET", 5))
    max_bet = int(getattr(config, "GLASS_MAX_BET", 500))
    max_win = int(getattr(config, "GLASS_MAX_WIN", 0) or 0)
    ladder = "\n".join(
        f"- Panel **{n}** = **{multiplier_for(n):.2f}×**" for n in range(1, total + 1))
    cap = (f"\n- Wins are capped at **{max_win:,} UKPence**, which only bites on a large "
           f"stake reaching the far side." if max_win > 0 else "")
    await interaction.response.send_message(
        "## 🪟 The Glass Bridge - House Rules\n"
        f"{total} pairs of panels span the canyon. One of each pair is tempered and holds "
        "your weight; the other is fragile. Pick a side, step, and hope.\n\n"
        f"{ladder}\n\n"
        "- **Cash Out** any time after the first panel to take **stake × multiplier** from "
        "the bank.\n"
        f"- Cross all {total} and it banks automatically at the top multiplier.\n"
        "- Pick the fragile panel and the stake is gone.\n"
        f"- **Bets:** {min_bet:,} - {max_bet:,} UKPence.{cap}\n\n"
        f"-# Every panel is a straight 50/50 and the house takes the same "
        f"{_edge()*100:.0f}% off each one, so there is no clever place to stop - only how "
        "much nerve you have. Good luck. 🇬🇧",
        ephemeral=True)


# ---------------------------------------------------------------------------
# Command entry
# ---------------------------------------------------------------------------
@deal_in_flight
async def handle_glass_command(interaction: Interaction, amount: int):
    if await reject_if_maintenance(interaction):
        return
    if not getattr(config, "GLASS_ENABLED", True):
        await interaction.response.send_message("The bridge is closed.", ephemeral=True)
        return

    min_bet = int(getattr(config, "GLASS_MIN_BET", 5))
    max_bet = int(getattr(config, "GLASS_MAX_BET", 500))
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
    if not remove_bb(interaction.user.id, amount, reason="Glass Bridge bet"):
        await interaction.response.send_message(
            f"You don't have enough UKPence. Your balance is {get_bb(interaction.user.id):,}.",
            ephemeral=True)
        return

    name = discord.utils.escape_markdown(interaction.user.display_name)
    try:
        await interaction.response.defer(thinking=True)
        game = GlassBridgeGame.new(interaction.user.id, name, interaction.channel_id, amount)
        view, files = build_glass_layout(game)
        msg = await interaction.followup.send(view=view, files=files)
    except Exception:
        logger.error("Glass Bridge deal failed; refunding stake.", exc_info=True)
        credit_from_bank(interaction.user.id, amount, "Glass Bridge stake refund (deal failed)")
        try:
            await interaction.followup.send(
                "Something went wrong starting your game - your stake has been refunded.",
                ephemeral=True)
        except Exception:
            pass
        return

    game.message_id = msg.id
    try:
        save_game(game)
        interaction.client.add_view(view, message_id=msg.id)
    except Exception:
        logger.error("Glass Bridge post-send persistence issue (game is live).", exc_info=True)


# ---------------------------------------------------------------------------
# Restart recovery (called from event_handlers.reattach_persistent_views)
# ---------------------------------------------------------------------------
def reattach_glass_view(client, key, value):
    """Re-register click routing for an in-play board after a restart. The bridge is stored
    with the board, so the crossing resumes as the same bridge rather than a new one."""
    try:
        game = GlassBridgeGame.from_dict(value)
    except Exception as e:
        logger.error(f"Pruning malformed glass entry {key}: {e}", exc_info=True)
        delete_state(key)
        return
    if game.state != "playing":
        delete_state(key)
        return
    try:
        game.message_id = int(key)
        view, _files = build_glass_layout(game)   # the message keeps the image it has
        client.add_view(view, message_id=int(key))
    except Exception as e:
        logger.error(f"Failed to reattach glass view {key}: {e}", exc_info=True)
