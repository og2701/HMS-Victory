"""HMS Victory - European Roulette (single-zero, vs the house).

Place chips on the felt (red/black, odd/even, low/high, dozens, columns, or straight-up
numbers), then spin. A single green zero gives the house its 2.7% edge; every bet is
otherwise paid at true odds.

UX (Discord-friendly): the betting phase is a fast text "slip" (chip buttons + bet-spot
buttons) - every tap just re-renders text, no Chrome render, so it stays snappy even on
the small server. Only the spin itself renders: a pre-baked horizontal-ticker GIF (one
per outcome, 0-36) plays the wheel landing on the result, then a felt result image with
the per-bet breakdown swaps in.

Economy mirrors the other house games: stakes enter the bank (remove_bb "Roulette bet"),
wins are paid from it (credit_from_bank "Roulette win"); a loss simply leaves the stake
in the bank as the edge. Stats go through casino_stats.record_result (game="roulette").
"""

import io
import logging
import os
import random
import uuid

import discord
from discord import Interaction

from lib.economy.economy_manager import get_bb, remove_bb
from lib.economy.casino_stats import record_result, session_footer_html
from lib.core.file_operations import read_html_template
import commands.economy.casino_base as cb

logger = logging.getLogger(__name__)

KEY = "roulette"
ACCENT = discord.Colour(0x1C6B46)  # felt green

# ---------------------------------------------------------------------------
# Wheel model (European single-zero)
# ---------------------------------------------------------------------------
# Clockwise pocket order on a real European wheel - used so the spin ticker scrolls
# through genuine neighbours rather than 0..36 in sequence.
WHEEL_ORDER = [0, 32, 15, 19, 4, 21, 2, 25, 17, 34, 6, 27, 13, 36, 11, 30, 8, 23, 10,
               5, 24, 16, 33, 1, 20, 14, 31, 9, 22, 18, 29, 7, 28, 12, 35, 3, 26]
RED = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}

CHIP_SIZES = [10, 50, 100, 500, 1000]

# Outside/inside bet payouts (X:1). Straight-up is handled separately (35:1).
_OUTSIDE_PAYOUT = {
    "red": 1, "black": 1, "even": 1, "odd": 1, "low": 1, "high": 1,
    "dozen1": 2, "dozen2": 2, "dozen3": 2, "col1": 2, "col2": 2, "col3": 2,
}
_BET_LABELS = {
    "red": "Red", "black": "Black", "even": "Even", "odd": "Odd",
    "low": "1–18", "high": "19–36",
    "dozen1": "1st 12", "dozen2": "2nd 12", "dozen3": "3rd 12",
    "col1": "Col 1", "col2": "Col 2", "col3": "Col 3",
}


def color(n: int) -> str:
    return "green" if n == 0 else ("red" if n in RED else "black")


def bet_payout(key: str) -> int:
    return 35 if key.startswith("straight:") else _OUTSIDE_PAYOUT[key]


def bet_label(key: str) -> str:
    if key.startswith("straight:"):
        return f"#{key.split(':')[1]}"
    return _BET_LABELS[key]


def bet_wins(key: str, n: int) -> bool:
    if key.startswith("straight:"):
        return int(key.split(":")[1]) == n
    if n == 0:
        return False  # zero loses every outside, dozen and column bet
    return {
        "red": n in RED, "black": n not in RED,
        "even": n % 2 == 0, "odd": n % 2 == 1,
        "low": 1 <= n <= 18, "high": 19 <= n <= 36,
        "dozen1": 1 <= n <= 12, "dozen2": 13 <= n <= 24, "dozen3": 25 <= n <= 36,
        "col1": n % 3 == 1, "col2": n % 3 == 2, "col3": n % 3 == 0,
    }[key]


def _fmt_chip(c: int) -> str:
    return f"{c // 1000}K" if c >= 1000 and c % 1000 == 0 else str(c)


# ---------------------------------------------------------------------------
# Game state (in-memory; the bet slip is lost on a bot restart, like slots)
# ---------------------------------------------------------------------------
class RouletteGame:
    def __init__(self, player_id, player_name, channel_id):
        self.game_id = uuid.uuid4().hex[:12]
        self.player_id = player_id
        self.player_name = player_name
        self.channel_id = channel_id
        self.message_id = None
        self.bets = {}            # bet_key -> staked amount
        self.history = []         # [(bet_key, amount)] for Undo
        self.chip = 50
        self.result = None
        self.returned = 0
        self.net = 0
        self.session_count = 0
        self.session_net = 0
        self.busy = False

    @property
    def total(self) -> int:
        return sum(self.bets.values())

    def place(self, key: str, amount: int = None):
        amt = self.chip if amount is None else amount
        self.bets[key] = self.bets.get(key, 0) + amt
        self.history.append((key, amt))

    def undo(self):
        if not self.history:
            return
        key, amt = self.history.pop()
        self.bets[key] = self.bets.get(key, 0) - amt
        if self.bets[key] <= 0:
            self.bets.pop(key, None)

    def clear(self):
        self.bets.clear()
        self.history.clear()

    def spin(self):
        self.result = random.randint(0, 36)
        self.returned = sum(amt * (bet_payout(k) + 1)
                            for k, amt in self.bets.items() if bet_wins(k, self.result))
        self.net = self.returned - self.total


# ---------------------------------------------------------------------------
# Spin ticker animation (pre-generated; one GIF per outcome 0..36)
# ---------------------------------------------------------------------------
# A horizontal strip of pockets (in wheel order) slides left under a fixed centre pointer
# and eases to a stop with the winning pocket centred. Motion is expressed with `left:`
# (a layout reflow), NOT a CSS transform - the fast frame-sequence renderer swaps frames
# via innerHTML and a transform would screenshot stale, exactly as in the slots slide.
SPIN_TEMPLATE = "templates/roulette_spin.html"
CELL_W = 130
VIEW_W = 910
VIEW_H = 300
CENTER_X = VIEW_W / 2
LEADIN = 22                  # pockets scrolled through before the target lands
HALF = 5                    # pockets either side of centre kept filled
TARGET_IDX = LEADIN + HALF  # the target's index within the built track
SPIN_STOP = 26              # frames of deceleration
SPIN_HOLD = 3               # held frames after landing
SPIN_FRAME_MS = 55
SPIN_FINAL_MS = 1900


def _ease_out_cubic(p: float) -> float:
    return 1 - (1 - p) ** 3


def _pocket_html(n: int, *, win: bool = False) -> str:
    cls = f"pocket {color(n)}" + (" win" if win else "")
    return f'<div class="{cls}">{n}</div>'


def _track_numbers(target: int) -> list:
    w = WHEEL_ORDER.index(target)
    length = LEADIN + 2 * HALF + 1
    return [WHEEL_ORDER[(w + (i - TARGET_IDX)) % 37] for i in range(length)]


def build_spin_frames(target: int) -> list:
    """Frames for the ticker decelerating onto ``target`` (centred under the pointer)."""
    nums = _track_numbers(target)
    template = read_html_template(SPIN_TEMPLATE)
    final_left = CENTER_X - (TARGET_IDX * CELL_W + CELL_W / 2)
    start_left = final_left + LEADIN * CELL_W
    n = SPIN_STOP + SPIN_HOLD
    frames = []
    for f in range(n + 1):
        landed = f >= SPIN_STOP
        p = 1.0 if landed else _ease_out_cubic(f / SPIN_STOP)
        left = start_left + (final_left - start_left) * p
        cells = "".join(_pocket_html(x, win=(landed and i == TARGET_IDX))
                        for i, x in enumerate(nums))
        frames.append(template.replace("{{LEFT}}", f"{left:.1f}").replace("{{POCKETS}}", cells))
    return frames


def build_spinner_frames() -> list:
    """A generic, seamlessly-looping constant-speed scroll (the loader / fallback GIF)."""
    template = read_html_template(SPIN_TEMPLATE)
    nums = WHEEL_ORDER * 2
    cells = "".join(_pocket_html(x) for x in nums)
    span = 37 * CELL_W
    start = CENTER_X - CELL_W / 2
    n = 30
    return [template.replace("{{LEFT}}", f"{start - span * (f / n):.1f}").replace("{{POCKETS}}", cells)
            for f in range(n)]


async def render_result_gif(target: int) -> io.BytesIO:
    from lib.core.image_processing import screenshot_html_sequence
    frames = build_spin_frames(target)
    durations = [SPIN_FRAME_MS] * (len(frames) - 1) + [SPIN_FINAL_MS]
    return await screenshot_html_sequence(
        frames, size=(VIEW_W, VIEW_H), element_selector=".viewport",
        durations=durations, loop=None,
    )


async def render_spinner_gif() -> io.BytesIO:
    from lib.core.image_processing import screenshot_html_sequence
    frames = build_spinner_frames()
    return await screenshot_html_sequence(
        frames, size=(VIEW_W, VIEW_H), element_selector=".viewport",
        durations=[SPIN_FRAME_MS] * len(frames), loop=0,
    )


def results_dir() -> str:
    import config
    return os.path.join(config.DATA_DIR, "roulette_results")


def _result_gif_path(n: int) -> str:
    return os.path.join(results_dir(), f"{n}.gif")


def get_spinner_gif() -> str:
    import config
    path = os.path.join(config.DATA_DIR, "roulette_spinning", "spin.gif")
    return path if os.path.exists(path) else None


# ---------------------------------------------------------------------------
# Result felt image (reuses the shared casino table shell)
# ---------------------------------------------------------------------------
def _result_body_html(game: RouletteGame) -> str:
    n = game.result
    bg = {"green": "#1b8a4b", "red": "#b3242f", "black": "#1a1a1a"}[color(n)]
    if n == 0:
        tags = ["Zero", "Green"]
    else:
        tags = [color(n).capitalize(), "Even" if n % 2 == 0 else "Odd",
                "1–18" if n <= 18 else "19–36"]
    hero = (
        '<div style="display:flex;flex-direction:column;align-items:center;gap:12px;margin:4px 0 20px">'
        f'<div style="width:148px;height:148px;border-radius:50%;display:flex;align-items:center;'
        f'justify-content:center;font-family:Georgia,serif;font-weight:800;font-size:76px;color:#fff;'
        f'background:{bg};box-shadow:0 0 0 5px rgba(214,164,74,.75),0 12px 30px rgba(0,0,0,.55)">{n}</div>'
        f'<div style="font-size:18px;letter-spacing:.16em;text-transform:uppercase;color:#e8cf92;'
        f'font-weight:700">{" · ".join(tags)}</div></div>'
    )
    rows = []
    for key in sorted(game.bets, key=lambda k: (not bet_wins(k, n), k)):
        amt = game.bets[key]
        won = bet_wins(key, n)
        val = f"+{amt * bet_payout(key):,}" if won else f"−{amt:,}"
        col = "#7CFC9B" if won else "#ff7a7a"
        brd = "rgba(214,164,74,.65)" if won else "rgba(255,255,255,.10)"
        rows.append(
            '<div style="display:flex;justify-content:space-between;align-items:center;'
            f'padding:11px 18px;border-radius:12px;background:rgba(0,0,0,.32);border:1px solid {brd}">'
            f'<span style="font-weight:700;color:#fff;font-size:19px">{bet_label(key)} '
            f'<span style="opacity:.55;font-size:16px">· staked {amt:,}</span></span>'
            f'<span style="font-weight:800;font-size:20px;color:{col}">{val}</span></div>'
        )
    rows_html = "".join(rows) or '<div style="text-align:center;color:rgba(255,255,255,.6)">No bets placed.</div>'
    return hero + ('<div style="width:100%;max-width:540px;margin:0 auto;display:flex;'
                   f'flex-direction:column;gap:9px">{rows_html}</div>')


async def render_result_image(game: RouletteGame) -> io.BytesIO:
    n = game.result
    sub = f"The ball landed on {n} {color(n).upper()}"
    if game.net > 0:
        banner = cb.banner_html("win", f"WIN +{game.net:,}", sub)
    elif game.net < 0:
        banner = cb.banner_html("lose", f"DOWN −{abs(game.net):,}", sub)
    else:
        banner = cb.banner_html("push", "BREAK EVEN", sub)
    session = session_footer_html(
        game.player_id, session_count=game.session_count,
        session_net=game.session_net, current_net=game.net, over=True,
    )
    return await cb.render_table(
        title_main="EUROPEAN", title_accent="ROULETTE",
        subtitle="Where did the ball land?",
        body_html=_result_body_html(game),
        bet=game.total, balance=get_bb(game.player_id),
        hint="Spin the same bets again, or change them.",
        result_banner=banner, session_html=session,
    )


# ---------------------------------------------------------------------------
# Components V2 layouts
# ---------------------------------------------------------------------------
def _slip_text(game: RouletteGame) -> str:
    bal = get_bb(game.player_id)
    lines = [
        f"## \U0001f3a1 European Roulette — {game.player_name}",
        f"Active chip: **{_fmt_chip(game.chip)}**  ·  Balance: **{bal:,}** UKPence",
        "",
    ]
    if game.bets:
        for key, amt in game.bets.items():
            lines.append(f"• **{bet_label(key)}** — {amt:,}  _(pays {bet_payout(key)}:1)_")
        lines.append(f"\n**Total staked: {game.total:,}**")
    else:
        lines.append("_No bets yet — tap a chip size, then tap where to bet._")
    lines.append("-# Single zero · house edge 2.7%. Tap \U0001f3a1 Spin when ready.")
    return "\n".join(lines)


def _btn(game, action, label, style, **kw):
    b = discord.ui.Button(label=label, style=style, custom_id=f"roul:{game.game_id}:{action}", **kw)
    b.callback = _make_cb(game, action)
    return b


def _bet_rows(game: RouletteGame) -> list:
    rows = []
    chip_row = discord.ui.ActionRow()
    for c in CHIP_SIZES:
        style = discord.ButtonStyle.success if c == game.chip else discord.ButtonStyle.secondary
        chip_row.add_item(_btn(game, f"chip:{c}", _fmt_chip(c), style))
    rows.append(chip_row)

    layout = [
        [("\U0001f534 Red", "bet:red"), ("⚫ Black", "bet:black"),
         ("Even", "bet:even"), ("Odd", "bet:odd")],
        [("1–18", "bet:low"), ("19–36", "bet:high"),
         ("1st 12", "bet:dozen1"), ("2nd 12", "bet:dozen2"), ("3rd 12", "bet:dozen3")],
        [("Col 1", "bet:col1"), ("Col 2", "bet:col2"), ("Col 3", "bet:col3"),
         ("# Number", "num"), ("↶ Undo", "undo")],
    ]
    for spec in layout:
        r = discord.ui.ActionRow()
        for label, action in spec:
            r.add_item(_btn(game, action, label, discord.ButtonStyle.secondary))
        rows.append(r)

    final = discord.ui.ActionRow()
    final.add_item(_btn(game, "clear", "Clear", discord.ButtonStyle.danger, emoji="\U0001f5d1️"))
    final.add_item(_btn(game, "spin", "SPIN", discord.ButtonStyle.primary,
                        emoji="\U0001f3a1", disabled=(game.total <= 0)))
    rows.append(final)
    return rows


def build_betting_layout(game: RouletteGame) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=None)
    container = discord.ui.Container(accent_colour=ACCENT)
    container.add_item(discord.ui.TextDisplay(_slip_text(game)))
    view.add_item(container)
    for row in _bet_rows(game):
        view.add_item(row)
    return view


def _spinning_row(game: RouletteGame) -> discord.ui.ActionRow:
    row = discord.ui.ActionRow()
    row.add_item(discord.ui.Button(label="No more bets…", emoji="\U0001f3a1",
                                   style=discord.ButtonStyle.primary, disabled=True,
                                   custom_id=f"roul:{game.game_id}:spinning"))
    return row


def build_spin_anim_layout(game: RouletteGame) -> tuple:
    import config
    view = discord.ui.LayoutView(timeout=None)
    files = []
    used = False
    if getattr(config, "ROULETTE_IMAGE_ENABLED", True):
        gif = _result_gif_path(game.result)
        if not os.path.exists(gif):
            gif = get_spinner_gif()
        if gif and os.path.exists(gif):
            try:
                files = [discord.File(gif, filename="roulette.gif")]
                gallery = discord.ui.MediaGallery()
                gallery.add_item(media="attachment://roulette.gif")
                view.add_item(gallery)
                used = True
            except Exception:
                logger.warning("Failed to attach roulette spin GIF", exc_info=True)
    if not used:
        container = discord.ui.Container(accent_colour=ACCENT)
        container.add_item(discord.ui.TextDisplay("## \U0001f3a1 Round and round she goes…\n**No more bets!**"))
        view.add_item(container)
    view.add_item(_spinning_row(game))
    return view, files


def _result_row(game: RouletteGame) -> discord.ui.ActionRow:
    row = discord.ui.ActionRow()
    row.add_item(_btn(game, "again", "Same Bets", discord.ButtonStyle.success, emoji="\U0001f3a1"))
    row.add_item(_btn(game, "new", "Change Bets", discord.ButtonStyle.secondary, emoji="✏️"))
    row.add_item(_btn(game, "rules", "Rules", discord.ButtonStyle.secondary, emoji="\U0001f4d6"))
    return row


def _result_text(game: RouletteGame) -> str:
    n = game.result
    head = "WIN" if game.net > 0 else ("DOWN" if game.net < 0 else "EVEN")
    return (f"## \U0001f3a1 {n} {color(n).upper()}\n"
            f"**{head} {game.net:+,} UKPence**\n"
            f"-# {game.player_name} · Staked {game.total:,} · Balance {get_bb(game.player_id):,}")


def _result_layout(game: RouletteGame, img) -> tuple:
    view = discord.ui.LayoutView(timeout=None)
    files = []
    if img is not None:
        files = [discord.File(img, filename="roulette.png")]
        gallery = discord.ui.MediaGallery()
        gallery.add_item(media="attachment://roulette.png")
        view.add_item(gallery)
    else:
        container = discord.ui.Container(accent_colour=ACCENT)
        container.add_item(discord.ui.TextDisplay(_result_text(game)))
        view.add_item(container)
    view.add_item(_result_row(game))
    return view, files


# ---------------------------------------------------------------------------
# Interaction handling
# ---------------------------------------------------------------------------
def _make_cb(game: RouletteGame, action: str):
    async def _cb(interaction: Interaction):
        await _handle_action(interaction, game, action)
    return _cb


async def _rerender_slip(interaction: Interaction, game: RouletteGame):
    view = build_betting_layout(game)
    await interaction.response.edit_message(view=view, attachments=[])
    try:
        interaction.client.add_view(view, message_id=game.message_id)
    except Exception:
        logger.debug("roulette add_view after slip edit failed (non-fatal)", exc_info=True)


async def _handle_action(interaction: Interaction, game: RouletteGame, action: str):
    if action == "rules":
        await _show_rules(interaction)
        return
    if interaction.user.id != game.player_id:
        await interaction.response.send_message(
            "This isn't your table — deal your own with `/roulette`.", ephemeral=True)
        return
    if action == "num":
        await interaction.response.send_modal(NumberBetModal(game))
        return
    if action in ("spin", "again"):
        await _do_spin(interaction, game)
        return
    if action == "new":
        await _rerender_slip(interaction, game)
        return

    # chip / bet / undo / clear: mutate state, then re-render the text slip (instant).
    if game.busy:
        await interaction.response.defer()
        return
    import config
    mx = getattr(config, "ROULETTE_MAX_BET", 10_000)
    if action.startswith("chip:"):
        game.chip = int(action.split(":")[1])
    elif action.startswith("bet:"):
        key = action.split(":", 1)[1]
        if game.total + game.chip > mx:
            await interaction.response.send_message(
                f"That would exceed the {mx:,} UKPence table limit for one spin.", ephemeral=True)
            return
        if get_bb(game.player_id) < game.total + game.chip:
            await interaction.response.send_message(
                "You don't have enough UKPence for that chip.", ephemeral=True)
            return
        game.place(key)
    elif action == "undo":
        game.undo()
    elif action == "clear":
        game.clear()
    await _rerender_slip(interaction, game)


async def _do_spin(interaction: Interaction, game: RouletteGame):
    if game.busy:
        await interaction.response.defer()
        return
    if await cb.reject_if_maintenance(interaction):
        return
    if game.total <= 0:
        await interaction.response.send_message("Place a bet first.", ephemeral=True)
        return
    bal = get_bb(game.player_id)
    if bal < game.total:
        await interaction.response.send_message(
            f"You need {game.total:,} UKPence for these bets (balance {bal:,}).", ephemeral=True)
        return

    game.busy = True
    try:
        if not remove_bb(game.player_id, game.total, reason="Roulette bet"):
            await interaction.response.send_message("You don't have enough UKPence.", ephemeral=True)
            return

        game.session_net += game.net   # fold the previous spin into the running session
        game.session_count += 1
        game.spin()                    # decide first so the pre-baked GIF matches

        await interaction.response.defer()
        spin_view, spin_files = build_spin_anim_layout(game)
        try:
            await interaction.edit_original_response(view=spin_view, attachments=spin_files)
        except Exception:
            logger.error("Roulette spin animation edit failed.", exc_info=True)

        try:
            img = await render_result_image(game)
            view, files = _result_layout(game, img)
            await interaction.edit_original_response(view=view, attachments=files)
        except Exception:
            logger.error("Roulette result render failed; refunding stake.", exc_info=True)
            cb.credit_from_bank(game.player_id, game.total, "Roulette stake refund (render failed)")
            return

        if game.returned > 0:
            cb.credit_from_bank(game.player_id, game.returned, "Roulette win")
        record_result(game.player_id, KEY, game.total, game.total, game.returned, str(game.result))
        try:
            interaction.client.add_view(view, message_id=game.message_id)
        except Exception:
            logger.debug("roulette add_view after spin failed (non-fatal)", exc_info=True)
    finally:
        game.busy = False


class NumberBetModal(discord.ui.Modal, title="Roulette — straight-up bet"):
    def __init__(self, game: RouletteGame):
        super().__init__()
        self.game = game
        self.num = discord.ui.TextInput(
            label="Number(s) 0–36", placeholder="17   or   0,17,32",
            required=True, max_length=80,
        )
        self.add_item(self.num)

    async def on_submit(self, interaction: Interaction):
        import config
        raw = str(self.num.value).replace(" ", "")
        try:
            nums = [int(x) for x in raw.split(",") if x != ""]
        except ValueError:
            await interaction.response.send_message("Enter whole numbers between 0 and 36.", ephemeral=True)
            return
        nums = [n for n in nums if 0 <= n <= 36]
        if not nums:
            await interaction.response.send_message("No valid numbers (0–36).", ephemeral=True)
            return
        mx = getattr(config, "ROULETTE_MAX_BET", 10_000)
        cost = self.game.chip * len(nums)
        if self.game.total + cost > mx:
            await interaction.response.send_message(
                f"That would exceed the {mx:,} UKPence table limit for one spin.", ephemeral=True)
            return
        if get_bb(self.game.player_id) < self.game.total + cost:
            await interaction.response.send_message(
                "You don't have enough UKPence for those chips.", ephemeral=True)
            return
        for n in nums:
            self.game.place(f"straight:{n}")
        await _rerender_slip(interaction, self.game)


async def _show_rules(interaction: Interaction):
    import config
    mn = getattr(config, "ROULETTE_MIN_BET", 5)
    mx = getattr(config, "ROULETTE_MAX_BET", 10_000)
    rules = (
        "## \U0001f3a1 European Roulette — House Rules\n"
        "One green **zero** (37 pockets) — the house edge is **2.7%** on every bet.\n\n"
        "**Pick a chip size, then tap where to bet. Stack as many bets as you like, then Spin.**\n\n"
        "- **Red / Black, Even / Odd, 1–18 / 19–36** — pay **1:1**\n"
        "- **Dozens (1st/2nd/3rd 12) and Columns** — pay **2:1**\n"
        "- **Straight up** (a single number via **# Number**) — pays **35:1**\n"
        "- **Zero** loses every outside, dozen and column bet — only a straight-up 0 wins.\n\n"
        f"-# Total stake per spin: {mn:,}–{mx:,} UKPence. Stakes go to the house bank; wins are paid from it."
    )
    await interaction.response.send_message(rules, ephemeral=True)


# ---------------------------------------------------------------------------
# Slash command entry point
# ---------------------------------------------------------------------------
async def handle_roulette_command(interaction: Interaction):
    import config
    if await cb.reject_if_maintenance(interaction):
        return
    if not getattr(config, "ROULETTE_ENABLED", True):
        await interaction.response.send_message("The roulette table is closed.", ephemeral=True)
        return
    name = discord.utils.escape_markdown(interaction.user.display_name)
    game = RouletteGame(interaction.user.id, name, interaction.channel_id)
    view = build_betting_layout(game)
    await interaction.response.send_message(view=view)
    try:
        msg = await interaction.original_response()
        game.message_id = msg.id
        interaction.client.add_view(view, message_id=msg.id)
    except Exception:
        logger.debug("roulette initial add_view failed (non-fatal)", exc_info=True)
