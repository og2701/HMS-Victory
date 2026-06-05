"""HMS Victory - European Roulette (single-zero), multiplayer shared table.

One live table per channel. `/roulette` opens it (a public felt message with a 2-minute
countdown) or, if one's already running, lets you join. Players tap **Enter Table** to
get a private (ephemeral) chip + bet-slip; **Submit** stamps their chips onto the shared
felt and debits their stake. When the countdown hits zero the wheel spins once and every
player's bets resolve against that one number, with a shared results board and payouts
from the house bank.

The wheel itself is a pre-baked horizontal-ticker GIF (one per outcome 0-36) rendered on
the same felt shell as the table, so the spinning phase and the result are one table.

Single green zero gives the house its 2.70% edge; every bet is otherwise paid true odds.
Stats go through casino_stats.record_result (game="roulette").
"""

import asyncio
import io
import logging
import os
import random
import time
from html import escape as _esc

import discord
from discord import Interaction

from lib.economy.economy_manager import get_bb, remove_bb
from lib.economy.casino_stats import record_result
from lib.core.file_operations import read_html_template
import commands.economy.casino_base as cb

logger = logging.getLogger(__name__)

KEY = "roulette"
ACCENT = discord.Colour(0x1C6B46)  # felt green
COUNTDOWN_SECONDS = 120

# ---------------------------------------------------------------------------
# Wheel model (European single-zero)
# ---------------------------------------------------------------------------
WHEEL_ORDER = [0, 32, 15, 19, 4, 21, 2, 25, 17, 34, 6, 27, 13, 36, 11, 30, 8, 23, 10,
               5, 24, 16, 33, 1, 20, 14, 31, 9, 22, 18, 29, 7, 28, 12, 35, 3, 26]
RED = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}

CHIP_SIZES = [10, 50, 100, 500, 1000]

_OUTSIDE_PAYOUT = {
    "red": 1, "black": 1, "even": 1, "odd": 1, "low": 1, "high": 1,
    "dozen1": 2, "dozen2": 2, "dozen3": 2, "col1": 2, "col2": 2, "col3": 2,
}
_BET_LABELS = {
    "red": "Red", "black": "Black", "even": "Even", "odd": "Odd",
    "low": "1-18", "high": "19-36",
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
        return False
    return {
        "red": n in RED, "black": n not in RED,
        "even": n % 2 == 0, "odd": n % 2 == 1,
        "low": 1 <= n <= 18, "high": 19 <= n <= 36,
        "dozen1": 1 <= n <= 12, "dozen2": 13 <= n <= 24, "dozen3": 25 <= n <= 36,
        "col1": n % 3 == 1, "col2": n % 3 == 2, "col3": n % 3 == 0,
    }[key]


def _resolve(bets: dict, n: int) -> int:
    """Total returned (stake + winnings of winning bets) for a slip vs result ``n``."""
    return sum(a * (bet_payout(k) + 1) for k, a in bets.items() if bet_wins(k, n))


def _fmt_chip(c: int) -> str:
    return f"{c // 1000}K" if c >= 1000 and c % 1000 == 0 else str(c)


class BetSlip:
    """One player's in-progress (ephemeral) chip placements before they Submit."""
    def __init__(self, player_id, name):
        self.player_id = player_id
        self.name = name
        self.bets = {}
        self.history = []
        self.chip = 50

    @property
    def total(self) -> int:
        return sum(self.bets.values())

    def place(self, key: str):
        self.bets[key] = self.bets.get(key, 0) + self.chip
        self.history.append((key, self.chip))

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


# ---------------------------------------------------------------------------
# Spin ticker animation (pre-generated; one GIF per outcome 0..36), rendered on
# the same felt-table shell as the table/result so the phases are one table.
# ---------------------------------------------------------------------------
SHELL_TEMPLATE = "templates/casino_table.html"
CELL_W = 130
VIEW_W = 688                # = .table width 760 - .body padding (36*2)
VIEW_H = 240
CENTER_X = VIEW_W / 2
LEADIN = 72                 # pockets scrolled through (doubled for a ~2x longer spin)
HALF = 3
TARGET_IDX = LEADIN + HALF
SPIN_STOP = 88              # frames of deceleration (doubled)
SPIN_HOLD = 6
SPIN_FRAME_MS = 60
SPIN_FINAL_MS = 2200

_TICKER_CSS = (
    "<style>"
    f".rl-vp{{position:relative;width:{VIEW_W}px;height:{VIEW_H}px;overflow:hidden;border-radius:14px;"
    "background:linear-gradient(180deg,#0c2a52,#061d3e);"
    "box-shadow:inset 0 0 0 3px rgba(214,164,74,.55),inset 0 0 44px rgba(0,0,0,.6);}"
    ".rl-track{position:absolute;top:32px;display:flex;height:176px;}"
    f".rl-pk{{width:{CELL_W}px;height:176px;flex:none;display:flex;align-items:center;justify-content:center;"
    "font-family:Georgia,serif;font-weight:800;font-size:64px;color:#fff;"
    "border-right:2px solid rgba(0,0,0,.55);text-shadow:0 2px 4px rgba(0,0,0,.55);}"
    ".rl-pk.red{background:linear-gradient(180deg,#c62a35,#9c1f29);}"
    ".rl-pk.black{background:linear-gradient(180deg,#262a30,#121316);}"
    ".rl-pk.green{background:linear-gradient(180deg,#1d9a52,#10683a);}"
    ".rl-pk.win{box-shadow:inset 0 0 0 5px #ffd95e,inset 0 0 30px rgba(255,217,94,.45);}"
    ".rl-ptr{position:absolute;top:8px;left:50%;margin-left:-14px;z-index:3;width:0;height:0;"
    "border-left:14px solid transparent;border-right:14px solid transparent;border-top:20px solid #ffd95e;"
    "filter:drop-shadow(0 2px 3px rgba(0,0,0,.7));}"
    ".rl-cl{position:absolute;top:32px;height:176px;left:50%;width:0;z-index:2;"
    "box-shadow:0 0 0 1px rgba(255,217,94,.55),0 0 16px rgba(255,217,94,.4);}"
    ".rl-fl,.rl-fr{position:absolute;top:32px;height:176px;width:150px;z-index:2;pointer-events:none;}"
    ".rl-fl{left:0;background:linear-gradient(90deg,#08214a,rgba(8,33,74,0));}"
    ".rl-fr{right:0;background:linear-gradient(270deg,#08214a,rgba(8,33,74,0));}"
    "</style>"
)


def _ease_out_cubic(p: float) -> float:
    return 1 - (1 - p) ** 3


def _track_numbers(target: int) -> list:
    w = WHEEL_ORDER.index(target)
    length = LEADIN + 2 * HALF + 1
    return [WHEEL_ORDER[(w + (i - TARGET_IDX)) % 37] for i in range(length)]


def _ticker_body(nums: list, left: float, *, win_idx: int = None) -> str:
    cells = "".join(
        f'<div class="rl-pk {color(x)}{" win" if i == win_idx else ""}">{x}</div>'
        for i, x in enumerate(nums)
    )
    return (
        _TICKER_CSS
        + '<div class="rl-vp"><div class="rl-ptr"></div><div class="rl-cl"></div>'
        + f'<div class="rl-track" style="left:{left:.1f}px">{cells}</div>'
        + '<div class="rl-fl"></div><div class="rl-fr"></div></div>'
    )


def _fill_shell(template: str, body: str, *, subtitle: str, hint: str,
                bet: str = "-", balance: str = "-", banner: str = "") -> str:
    # Replace the exact body <div>, not the bare {{BODY}} - the template's CSS comment also
    # contains the literal "{{BODY}}", and a body carrying </style> would otherwise close
    # the head <style> early and kill the shell styling.
    return (
        template
        .replace("{{TITLE_MAIN}}", "EUROPEAN").replace("{{TITLE_ACCENT}}", "ROULETTE")
        .replace("{{SUBTITLE}}", subtitle)
        .replace('<div class="body">{{BODY}}</div>', f'<div class="body">{body}</div>')
        .replace("{{BET_LABEL}}", "Pot").replace("{{BALANCE_LABEL}}", "Players")
        .replace("{{BET_UNIT}}", "").replace("{{BALANCE_UNIT}}", "")
        .replace("{{BET}}", bet).replace("{{BALANCE}}", balance)
        .replace("{{HINT}}", hint)
        .replace("{{RESULT_BANNER}}", banner).replace("{{SESSION}}", "")
    )


def build_spin_frames(target: int) -> list:
    nums = _track_numbers(target)
    template = read_html_template(SHELL_TEMPLATE)
    final_left = CENTER_X - (TARGET_IDX * CELL_W + CELL_W / 2)
    start_left = final_left + LEADIN * CELL_W
    n = SPIN_STOP + SPIN_HOLD
    frames = []
    for f in range(n + 1):
        landed = f >= SPIN_STOP
        p = 1.0 if landed else _ease_out_cubic(f / SPIN_STOP)
        left = start_left + (final_left - start_left) * p
        body = _ticker_body(nums, left, win_idx=(TARGET_IDX if landed else None))
        frames.append(_fill_shell(template, body, subtitle="No more bets - where will the ball land?",
                                  hint="Spinning…"))
    return frames


def build_spinner_frames() -> list:
    template = read_html_template(SHELL_TEMPLATE)
    nums = WHEEL_ORDER * 2
    span = 37 * CELL_W
    start = CENTER_X - CELL_W / 2
    n = 30
    return [_fill_shell(template, _ticker_body(nums, start - span * (f / n)),
                        subtitle="No more bets - where will the ball land?", hint="Spinning…")
            for f in range(n)]


async def render_result_gif(target: int) -> io.BytesIO:
    from lib.core.image_processing import screenshot_html_sequence
    frames = build_spin_frames(target)
    durations = [SPIN_FRAME_MS] * (len(frames) - 1) + [SPIN_FINAL_MS]
    return await screenshot_html_sequence(
        frames, size=(900, 1500), element_selector=".table", durations=durations, loop=None)


async def render_spinner_gif() -> io.BytesIO:
    from lib.core.image_processing import screenshot_html_sequence
    frames = build_spinner_frames()
    return await screenshot_html_sequence(
        frames, size=(900, 1500), element_selector=".table",
        durations=[SPIN_FRAME_MS] * len(frames), loop=0)


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
# Felt images: the betting roster and the results board (shared table shell)
# ---------------------------------------------------------------------------
def _roster_body(table) -> str:
    if not table.players:
        return ('<div style="text-align:center;color:rgba(255,255,255,.6);font-size:22px;'
                'padding:46px 20px">No bets yet - tap <b>Enter Table</b> to place your chips.</div>')
    rows = []
    for slot in sorted(table.players.values(), key=lambda s: -sum(s["bets"].values())):
        ptotal = sum(slot["bets"].values())
        chips = " · ".join(f"{bet_label(k)} {a:,}" for k, a in slot["bets"].items())
        rows.append(
            '<div style="display:flex;flex-direction:column;gap:5px;padding:12px 18px;border-radius:12px;'
            'background:rgba(0,0,0,.32);border:1px solid rgba(214,164,74,.32)">'
            '<div style="display:flex;justify-content:space-between;align-items:center">'
            f'<span style="font-weight:800;color:#fff;font-size:20px">{_esc(slot["name"])}</span>'
            f'<span style="font-weight:800;color:#e8cf92;font-size:18px">{ptotal:,}</span></div>'
            f'<div style="color:rgba(255,255,255,.72);font-size:15px">{_esc(chips)}</div></div>'
        )
    return ('<div style="width:100%;max-width:560px;margin:0 auto;display:flex;flex-direction:column;'
            f'gap:8px">{"".join(rows)}</div>')


def _results_body(table) -> str:
    n = table.result
    bg = {"green": "#1b8a4b", "red": "#b3242f", "black": "#1a1a1a"}[color(n)]
    tags = ["Zero", "Green"] if n == 0 else [
        color(n).capitalize(), "Even" if n % 2 == 0 else "Odd", "1-18" if n <= 18 else "19-36"]
    hero = (
        '<div style="display:flex;flex-direction:column;align-items:center;gap:10px;margin:2px 0 16px">'
        f'<div style="width:132px;height:132px;border-radius:50%;display:flex;align-items:center;'
        f'justify-content:center;font-family:Georgia,serif;font-weight:800;font-size:68px;color:#fff;'
        f'background:{bg};box-shadow:0 0 0 5px rgba(214,164,74,.75),0 12px 30px rgba(0,0,0,.55)">{n}</div>'
        f'<div style="font-size:16px;letter-spacing:.16em;text-transform:uppercase;color:#e8cf92;'
        f'font-weight:700">{" · ".join(tags)}</div></div>'
    )
    cards = []
    standings = sorted(table.players.values(),
                       key=lambda s: -(_resolve(s["bets"], n) - sum(s["bets"].values())))
    for slot in standings:
        bets = slot["bets"]
        net = _resolve(bets, n) - sum(bets.values())
        ncol = "#7CFC9B" if net > 0 else ("#ff7a7a" if net < 0 else "#e8e2cf")
        nsign = f"+{net:,}" if net > 0 else (f"-{abs(net):,}" if net < 0 else "even")
        bet_rows = []
        for key in sorted(bets, key=lambda k: (not bet_wins(k, n), k)):
            amt = bets[key]
            won = bet_wins(key, n)
            val = f"+{amt * bet_payout(key):,}" if won else f"-{amt:,}"
            bcol = "#7CFC9B" if won else "#ff7a7a"
            bet_rows.append(
                '<div style="display:flex;justify-content:space-between;align-items:center;padding:3px 2px">'
                f'<span style="color:rgba(255,255,255,.82);font-size:16px">{bet_label(key)}'
                f'<span style="opacity:.5"> · staked {amt:,}</span></span>'
                f'<span style="font-weight:700;font-size:16px;color:{bcol}">{val}</span></div>'
            )
        cards.append(
            '<div style="background:rgba(0,0,0,.32);border:1px solid rgba(214,164,74,.3);'
            'border-radius:12px;padding:11px 16px">'
            '<div style="display:flex;justify-content:space-between;align-items:center;'
            'padding-bottom:6px;margin-bottom:5px;border-bottom:1px solid rgba(255,255,255,.12)">'
            f'<span style="font-weight:800;color:#fff;font-size:20px">{_esc(slot["name"])}</span>'
            f'<span style="font-weight:800;font-size:20px;color:{ncol}">{nsign}</span></div>'
            f'{"".join(bet_rows)}</div>'
        )
    if cards:
        body = ('<div style="width:100%;max-width:560px;margin:0 auto;display:flex;flex-direction:column;'
                f'gap:9px">{"".join(cards)}</div>')
    else:
        body = ('<div style="text-align:center;color:rgba(255,255,255,.6);font-size:22px;'
                'padding:30px">No bets were placed this round.</div>')
    return hero + body


async def render_table_image(table) -> io.BytesIO:
    return await cb.render_table(
        title_main="EUROPEAN", title_accent="ROULETTE",
        subtitle="Place your bets - tap Enter Table",
        body_html=_roster_body(table),
        bet=table.pot, balance=len(table.players),
        bet_label="Pot", balance_label="Players", balance_unit="",
        hint="Bets lock when the countdown ends.", result_banner="", session_html="")


async def render_results_image(table) -> io.BytesIO:
    return await cb.render_table(
        title_main="EUROPEAN", title_accent="ROULETTE",
        subtitle="The ball has landed",
        body_html=_results_body(table),
        bet=table.pot, balance=len(table.players),
        bet_label="Pot", balance_label="Players", balance_unit="",
        hint="Tap New Round to play again.", result_banner="", session_html="")


# ---------------------------------------------------------------------------
# Shared table state (one live table per channel)
# ---------------------------------------------------------------------------
_TABLES = {}  # channel_id -> RouletteTable


class RouletteTable:
    def __init__(self, channel_id, opener_id, client):
        self.id = f"{channel_id}-{int(opener_id)}"
        self.channel_id = channel_id
        self.opener_id = opener_id
        self.client = client
        self.message = None
        self.players = {}            # player_id -> {"name": str, "bets": {key: amt}}
        self.status = "betting"      # betting | spinning | closed
        self.close_ts = int(time.time()) + COUNTDOWN_SECONDS
        self.result = None
        self.lock = asyncio.Lock()
        self._timer = None

    @property
    def pot(self) -> int:
        return sum(sum(p["bets"].values()) for p in self.players.values())

    def commit(self, player_id, name, bets: dict):
        slot = self.players.setdefault(player_id, {"name": name, "bets": {}})
        slot["name"] = name
        for k, a in bets.items():
            slot["bets"][k] = slot["bets"].get(k, 0) + a


def get_table(channel_id):
    t = _TABLES.get(channel_id)
    return t if (t and t.status != "closed") else None


# ---------------------------------------------------------------------------
# Public table message layout
# ---------------------------------------------------------------------------
def _table_text(table) -> str:
    if table.status == "betting":
        return (f"## \U0001f3a1 European Roulette - open table\n"
                f"**{len(table.players)}** players · pot **{table.pot:,}** UKPence · "
                f"bets close <t:{table.close_ts}:R>\n"
                f"-# Tap **Enter Table** to place chips. The wheel spins when the timer ends.")
    if table.status == "spinning":
        return "## \U0001f3a1 No more bets!\nRound and round she goes…"
    return "## \U0001f3a1 Round over\nTap **New Round** to play again."


def _table_buttons(table) -> discord.ui.ActionRow:
    row = discord.ui.ActionRow()
    enter = discord.ui.Button(label="Enter Table", emoji="\U0001f3b2",
                              style=discord.ButtonStyle.success,
                              custom_id=f"roul:tbl:{table.id}:enter",
                              disabled=(table.status != "betting"))
    enter.callback = _make_table_cb(table, "enter")
    row.add_item(enter)
    rules = discord.ui.Button(label="Rules", emoji="\U0001f4d6", style=discord.ButtonStyle.secondary,
                              custom_id=f"roul:tbl:{table.id}:rules")
    rules.callback = _make_table_cb(table, "rules")
    row.add_item(rules)
    return row


def build_table_layout(table, img) -> tuple:
    view = discord.ui.LayoutView(timeout=None)
    files = []
    if img is not None:
        files = [discord.File(img, filename="roulette_table.png")]
        gallery = discord.ui.MediaGallery()
        gallery.add_item(media="attachment://roulette_table.png")
        view.add_item(gallery)
    container = discord.ui.Container(accent_colour=ACCENT)
    container.add_item(discord.ui.TextDisplay(_table_text(table)))
    view.add_item(container)
    view.add_item(_table_buttons(table))
    return view, files


def build_table_spin_layout(table) -> tuple:
    import config
    view = discord.ui.LayoutView(timeout=None)
    files = []
    used = False
    if getattr(config, "ROULETTE_IMAGE_ENABLED", True):
        gif = _result_gif_path(table.result)
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
        container.add_item(discord.ui.TextDisplay("## \U0001f3a1 No more bets!\nSpinning…"))
        view.add_item(container)
    row = discord.ui.ActionRow()
    row.add_item(discord.ui.Button(label="No more bets…", style=discord.ButtonStyle.primary,
                                   disabled=True, custom_id=f"roul:tbl:{table.id}:spinning"))
    view.add_item(row)
    return view, files


def build_results_layout(table, img) -> tuple:
    view = discord.ui.LayoutView(timeout=None)
    files = []
    if img is not None:
        files = [discord.File(img, filename="roulette_results.png")]
        gallery = discord.ui.MediaGallery()
        gallery.add_item(media="attachment://roulette_results.png")
        view.add_item(gallery)
    else:
        container = discord.ui.Container(accent_colour=ACCENT)
        container.add_item(discord.ui.TextDisplay(
            f"## \U0001f3a1 {table.result} {color(table.result).upper()}\nRound over."))
        view.add_item(container)
    row = discord.ui.ActionRow()
    new = discord.ui.Button(label="New Round", emoji="\U0001f3a1", style=discord.ButtonStyle.success,
                            custom_id=f"roul:tbl:{table.id}:new")
    new.callback = _make_table_cb(table, "new")
    row.add_item(new)
    rules = discord.ui.Button(label="Rules", emoji="\U0001f4d6", style=discord.ButtonStyle.secondary,
                              custom_id=f"roul:tbl:{table.id}:rules")
    rules.callback = _make_table_cb(table, "rules")
    row.add_item(rules)
    view.add_item(row)
    return view, files


async def _refresh_table_message(table):
    """Re-render the felt roster and edit the public message (call inside table.lock)."""
    try:
        img = await render_table_image(table)
        view, files = build_table_layout(table, img)
        await table.message.edit(view=view, attachments=files)
        try:
            table.client.add_view(view, message_id=table.message.id)
        except Exception:
            pass
    except Exception:
        logger.error("Roulette table refresh failed.", exc_info=True)


# ---------------------------------------------------------------------------
# Per-player ephemeral bet slip
# ---------------------------------------------------------------------------
def _slip_text(table, slip: BetSlip) -> str:
    bal = get_bb(slip.player_id)
    lines = [
        f"## \U0001f3a1 Your bets - bets close <t:{table.close_ts}:R>",
        f"Active chip: **{_fmt_chip(slip.chip)}**  ·  Balance: **{bal:,}** UKPence",
        "",
    ]
    if slip.bets:
        for key, amt in slip.bets.items():
            lines.append(f"• **{bet_label(key)}** - {amt:,}  _(pays {bet_payout(key)}:1)_")
        lines.append(f"\n**This slip: {slip.total:,}** - tap Submit to put it on the table.")
    else:
        lines.append("_Pick a chip size, then tap where to bet. Submit to add it to the table._")
    return "\n".join(lines)


def _slip_btn(table, slip, action, label, style, **kw):
    b = discord.ui.Button(label=label, style=style,
                          custom_id=f"roul:slip:{table.id}:{slip.player_id}:{action}", **kw)
    b.callback = _make_slip_cb(table, slip, action)
    return b


def build_slip_layout(table, slip: BetSlip) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=COUNTDOWN_SECONDS + 30)
    container = discord.ui.Container(accent_colour=ACCENT)
    container.add_item(discord.ui.TextDisplay(_slip_text(table, slip)))
    view.add_item(container)

    chip_row = discord.ui.ActionRow()
    for c in CHIP_SIZES:
        style = discord.ButtonStyle.success if c == slip.chip else discord.ButtonStyle.secondary
        chip_row.add_item(_slip_btn(table, slip, f"chip:{c}", _fmt_chip(c), style))
    view.add_item(chip_row)

    layout = [
        [("\U0001f534 Red", "bet:red"), ("⚫ Black", "bet:black"), ("Even", "bet:even"), ("Odd", "bet:odd")],
        [("1-18", "bet:low"), ("19-36", "bet:high"),
         ("1st 12", "bet:dozen1"), ("2nd 12", "bet:dozen2"), ("3rd 12", "bet:dozen3")],
        [("Col 1", "bet:col1"), ("Col 2", "bet:col2"), ("Col 3", "bet:col3"),
         ("# Number", "num"), ("↶ Undo", "undo")],
    ]
    for spec in layout:
        r = discord.ui.ActionRow()
        for label, action in spec:
            r.add_item(_slip_btn(table, slip, action, label, discord.ButtonStyle.secondary))
        view.add_item(r)

    final = discord.ui.ActionRow()
    final.add_item(_slip_btn(table, slip, "clear", "Clear", discord.ButtonStyle.danger, emoji="\U0001f5d1️"))
    final.add_item(_slip_btn(table, slip, "submit", "Submit Bets", discord.ButtonStyle.primary,
                             emoji="✅", disabled=(slip.total <= 0)))
    view.add_item(final)
    return view


# ---------------------------------------------------------------------------
# Slip interaction handling
# ---------------------------------------------------------------------------
def _make_slip_cb(table, slip, action):
    async def _cb(interaction: Interaction):
        await _handle_slip(interaction, table, slip, action)
    return _cb


async def _handle_slip(interaction: Interaction, table, slip: BetSlip, action: str):
    if interaction.user.id != slip.player_id:
        await interaction.response.send_message("That isn't your slip.", ephemeral=True)
        return
    if table.status != "betting":
        await interaction.response.send_message("Betting has closed for this round.", ephemeral=True)
        return
    if action == "num":
        await interaction.response.send_modal(NumberBetModal(table, slip))
        return
    if action == "submit":
        await _submit_slip(interaction, table, slip)
        return

    import config
    mx = getattr(config, "ROULETTE_MAX_BET", 10_000)
    if action.startswith("chip:"):
        slip.chip = int(action.split(":")[1])
    elif action.startswith("bet:"):
        if slip.total + slip.chip > mx:
            await interaction.response.send_message(
                f"That would exceed the {mx:,} UKPence per-spin limit for one player.", ephemeral=True)
            return
        slip.place(action.split(":", 1)[1])
    elif action == "undo":
        slip.undo()
    elif action == "clear":
        slip.clear()
    await interaction.response.edit_message(view=build_slip_layout(table, slip))


async def _submit_slip(interaction: Interaction, table, slip: BetSlip):
    if slip.total <= 0:
        await interaction.response.send_message("Place at least one chip first.", ephemeral=True)
        return
    bal = get_bb(slip.player_id)
    if bal < slip.total:
        await interaction.response.send_message(
            f"You need {slip.total:,} UKPence for that slip (balance {bal:,}).", ephemeral=True)
        return
    async with table.lock:
        if table.status != "betting":
            await interaction.response.send_message("Betting just closed for this round.", ephemeral=True)
            return
        if not remove_bb(slip.player_id, slip.total, reason="Roulette bet"):
            await interaction.response.send_message("You don't have enough UKPence.", ephemeral=True)
            return
        name = discord.utils.escape_markdown(interaction.user.display_name)
        staked = slip.total
        table.commit(slip.player_id, name, dict(slip.bets))
        slip.clear()
    # Confirm to the player. The slip is a Components-V2 message, so the confirmation must
    # be a V2 view (TextDisplay) - `content` is rejected on V2 messages.
    done = discord.ui.LayoutView(timeout=1)
    box = discord.ui.Container(accent_colour=ACCENT)
    box.add_item(discord.ui.TextDisplay(
        f"✅ **{staked:,} UKPence** on the table. Tap **Enter Table** again to add more."))
    done.add_item(box)
    try:
        await interaction.response.edit_message(view=done)
    except Exception:
        logger.error("Roulette submit confirm edit failed.", exc_info=True)
    await _refresh_table_message(table)


class NumberBetModal(discord.ui.Modal, title="Roulette - straight-up bet"):
    def __init__(self, table, slip: BetSlip):
        super().__init__()
        self.table = table
        self.slip = slip
        self.num = discord.ui.TextInput(label="Number(s) 0-36", placeholder="17   or   0,17,32",
                                        required=True, max_length=80)
        self.add_item(self.num)

    async def on_submit(self, interaction: Interaction):
        import config
        if self.table.status != "betting":
            await interaction.response.send_message("Betting has closed.", ephemeral=True)
            return
        raw = str(self.num.value).replace(" ", "")
        try:
            nums = [int(x) for x in raw.split(",") if x != ""]
        except ValueError:
            await interaction.response.send_message("Enter whole numbers between 0 and 36.", ephemeral=True)
            return
        nums = [n for n in nums if 0 <= n <= 36]
        if not nums:
            await interaction.response.send_message("No valid numbers (0-36).", ephemeral=True)
            return
        mx = getattr(config, "ROULETTE_MAX_BET", 10_000)
        if self.slip.total + self.slip.chip * len(nums) > mx:
            await interaction.response.send_message(
                f"That would exceed the {mx:,} UKPence per-spin limit.", ephemeral=True)
            return
        for n in nums:
            self.slip.place(f"straight:{n}")
        await interaction.response.edit_message(view=build_slip_layout(self.table, self.slip))


# ---------------------------------------------------------------------------
# Table buttons (Enter / Spin Now / New Round / Rules)
# ---------------------------------------------------------------------------
def _make_table_cb(table, action):
    async def _cb(interaction: Interaction):
        await _handle_table(interaction, table, action)
    return _cb


async def _handle_table(interaction: Interaction, table, action: str):
    if action == "rules":
        await _show_rules(interaction)
        return
    if action == "new":
        await _open_table(interaction)
        return
    if action == "enter":
        if table.status != "betting":
            await interaction.response.send_message("Betting has closed for this round.", ephemeral=True)
            return
        name = discord.utils.escape_markdown(interaction.user.display_name)
        slip = BetSlip(interaction.user.id, name)
        await interaction.response.send_message(view=build_slip_layout(table, slip), ephemeral=True)
        return


# ---------------------------------------------------------------------------
# Countdown + the spin
# ---------------------------------------------------------------------------
async def _run_timer(table):
    try:
        await asyncio.sleep(max(0, table.close_ts - time.time()))
    except asyncio.CancelledError:
        return
    await _lock_and_spin(table)


async def _lock_and_spin(table):
    async with table.lock:
        if table.status != "betting":
            return
        table.status = "spinning"
    # Do NOT cancel table._timer here: this coroutine runs INSIDE that timer task, so
    # cancelling it would raise CancelledError into our own awaits and abort the spin.

    table.result = random.randint(0, 36)  # the wheel always spins, even with no bets

    # Phase 1: spin animation on the public message.
    try:
        spin_view, spin_files = build_table_spin_layout(table)
        await table.message.edit(view=spin_view, attachments=spin_files)
    except Exception:
        logger.error("Roulette table spin animation failed.", exc_info=True)

    # Phase 2: results board.
    try:
        img = await render_results_image(table)
    except Exception:
        logger.error("Roulette results render failed.", exc_info=True)
        img = None
    try:
        view, files = build_results_layout(table, img)
        await table.message.edit(view=view, attachments=files)
        try:
            table.client.add_view(view, message_id=table.message.id)
        except Exception:
            pass
    except Exception:
        logger.error("Roulette results edit failed.", exc_info=True)

    # Pay everyone from the bank and record stats (independent of rendering).
    n = table.result
    from lib.features.income_badges import award_badge_safe, record_income_source
    outcomes = []  # (pid, net) for the result ping below
    for pid, slot in table.players.items():
        bets = slot["bets"]
        staked = sum(bets.values())
        returned = _resolve(bets, n)
        if returned > 0:
            cb.credit_from_bank(pid, returned, "Roulette win")
        record_result(pid, KEY, staked, staked, returned, str(n))
        outcomes.append((pid, returned - staked))
        # Badges
        if n == 0:
            await award_badge_safe(table.client, pid, "zero_hero")
        if any(k.startswith("straight:") and bet_wins(k, n) for k in bets):
            await award_badge_safe(table.client, pid, "lucky_number")
        if returned - staked >= 1000:
            await award_badge_safe(table.client, pid, "red_letter_day")
        if returned > 0:
            await record_income_source(table.client, pid, "casino")

    # Fresh message pinging everyone who entered, with the result and each player's net.
    # (The table message itself keeps the rendered results board, unchanged.)
    if outcomes:
        try:
            await _post_result_ping(table, n, outcomes)
        except Exception:
            logger.error("Roulette result ping failed.", exc_info=True)

    table.status = "closed"
    _TABLES.pop(table.channel_id, None)


_COLOR_EMOJI = {"green": "\U0001f7e2", "red": "\U0001f534", "black": "⚫"}


async def _post_result_ping(table, n, outcomes):
    """Announce the spin result in a new channel message that @-mentions every entrant."""
    channel = table.message.channel if table.message else table.client.get_channel(table.channel_id)
    if channel is None:
        return
    col = color(n)
    label = "Zero" if n == 0 else col.capitalize()
    lines = [f"# \U0001f3a1 Roulette result: **{n} {label}** {_COLOR_EMOJI[col]}"]
    lines.append(" ".join(f"<@{pid}>" for pid, _ in outcomes))  # the ping
    winners = sorted((o for o in outcomes if o[1] > 0), key=lambda o: -o[1])
    others = [o for o in outcomes if o[1] <= 0]
    CAP = 15
    if winners:
        lines.append("**Winners**")
        for pid, net in winners[:CAP]:
            lines.append(f"\U0001f4b0 <@{pid}> **+{net:,}** UKPence")
        if len(winners) > CAP:
            lines.append(f"-# ...and {len(winners) - CAP} more winners")
    if others:
        lines.append("**No luck this time**")
        for pid, net in others[:CAP]:
            lines.append(f"\U0001f4b8 <@{pid}> {('**-' + format(abs(net), ',') + '**') if net < 0 else 'broke even'}")
        if len(others) > CAP:
            lines.append(f"-# ...and {len(others) - CAP} more")
    await channel.send("\n".join(lines))


# ---------------------------------------------------------------------------
# Rules + command entry
# ---------------------------------------------------------------------------
async def _show_rules(interaction: Interaction):
    import config
    mx = getattr(config, "ROULETTE_MAX_BET", 10_000)
    rules = (
        "## \U0001f3a1 European Roulette - House Rules\n"
        "A shared table: everyone bets, then one wheel spins for the whole table.\n\n"
        "- **Red / Black, Even / Odd, 1-18 / 19-36** - pay **1:1**\n"
        "- **Dozens & Columns** - pay **2:1**\n"
        "- **Straight up** (# Number) - pays **35:1**\n"
        "- One green **zero** (2.7% house edge); zero loses every outside/dozen/column bet.\n\n"
        f"-# Up to {mx:,} UKPence per player per spin. Stakes go to the house bank; wins are paid from it."
    )
    await interaction.response.send_message(rules, ephemeral=True)


async def _open_slip_for(interaction: Interaction, table):
    """Open the joiner's private bet slip (handles both fresh and deferred interactions)."""
    name = discord.utils.escape_markdown(interaction.user.display_name)
    slip = BetSlip(interaction.user.id, name)
    view = build_slip_layout(table, slip)
    if interaction.response.is_done():
        await interaction.followup.send(view=view, ephemeral=True)
    else:
        await interaction.response.send_message(view=view, ephemeral=True)


async def _open_table(interaction: Interaction):
    """Create and post a fresh shared table in this channel - or join the live one.

    The channel slot is reserved in _TABLES *synchronously* (before any await) so a second
    /roulette firing during the slow felt render can't spawn a duplicate table."""
    existing = get_table(interaction.channel_id)
    if existing and existing.status == "betting":
        await _open_slip_for(interaction, existing)
        return
    # Ack immediately - the felt render below can take >3s under load, which would
    # otherwise expire the interaction (10062) before we could respond.
    try:
        if not interaction.response.is_done():
            await interaction.response.defer()
    except discord.NotFound:
        return
    table = RouletteTable(interaction.channel_id, interaction.user.id, interaction.client)
    _TABLES[interaction.channel_id] = table  # reserve the slot before the render await
    try:
        img = await render_table_image(table)
        view, files = build_table_layout(table, img)
        msg = await interaction.followup.send(view=view, files=files)
        table.message = msg
        try:
            interaction.client.add_view(view, message_id=msg.id)
        except Exception:
            pass
    except Exception:
        logger.error("Roulette open table failed; releasing the channel slot.", exc_info=True)
        if _TABLES.get(interaction.channel_id) is table:
            _TABLES.pop(interaction.channel_id, None)
        return
    table._timer = asyncio.create_task(_run_timer(table))


async def handle_roulette_command(interaction: Interaction):
    import config
    if await cb.reject_if_maintenance(interaction):
        return
    if not getattr(config, "ROULETTE_ENABLED", True):
        await interaction.response.send_message("The roulette table is closed.", ephemeral=True)
        return
    await _open_table(interaction)
