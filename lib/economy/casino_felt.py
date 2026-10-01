"""Felt table shared by the casino card games (blackjack, higher/lower, three card poker,
red dog, video poker and hold'em).

Each game builds its own body from these pieces (cards, fans, seats, the gold arc) plus a
rail (the result or prompt, and the money ledger), then calls ``render``. Everything here
is plain markup so the layouts are testable offline; templates/casino_felt.html holds the
styles. Card codes are rank+suit, e.g. "AS", "TD" (T = ten); None is a face-down card.
"""

from __future__ import annotations

import base64
import html
import io
import math
import re
import struct
import zlib
from functools import lru_cache
from urllib.parse import quote

from lib.core.file_operations import read_html_template

TEMPLATE = "templates/casino_felt.html"
WIDTH = 820
TABLE_W = 740                       # content width inside the side padding

SUIT_GLYPH = {"S": "♠", "H": "♥", "D": "♦", "C": "♣"}
RED_SUITS = {"H", "D"}
CARD_W = {"xl": 220, "lg": 176, "md": 136, "sm": 86}
FAN_STEP = {"xl": 110, "lg": 84, "md": 66, "sm": 44}   # visible width of each covered card
RANK_NAME = {"A": "Ace", "K": "King", "Q": "Queen", "J": "Jack", "T": "10"}

MINUS = "−"

ANCHOR_SVG = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="#D9B66B" stroke-width="1.6" '
    'stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="4.6" r="2.1"/>'
    '<path d="M12 6.7V21"/><path d="M8.6 9.6h6.8"/>'
    '<path d="M4.6 13.6c0.6 4.6 3.6 7.2 7.4 7.4c3.8-0.2 6.8-2.8 7.4-7.4"/>'
    '<path d="M3 15.4l1.6-1.8l1.9 1.5"/><path d="M21 15.4l-1.6-1.8l-1.9 1.5"/></svg>'
)

ARC_SVG = (
    '<svg width="640" height="84" viewBox="0 0 640 84"><defs>'
    '<linearGradient id="arcfade" x1="0" y1="0" x2="1" y2="0">'
    '<stop offset="0" stop-color="#E2BE78" stop-opacity="0"/>'
    '<stop offset="0.18" stop-color="#E2BE78" stop-opacity="0.85"/>'
    '<stop offset="0.82" stop-color="#E2BE78" stop-opacity="0.85"/>'
    '<stop offset="1" stop-color="#E2BE78" stop-opacity="0"/></linearGradient></defs>'
    '<path d="M10 10 Q320 82 630 10" fill="none" stroke="url(#arcfade)" stroke-width="3.5" stroke-linecap="round"/>'
    '<path d="M60 26 Q320 88 580 26" fill="none" stroke="url(#arcfade)" stroke-width="1.5" stroke-linecap="round"/>'
    '<circle cx="320" cy="50" r="25" fill="#145040" stroke="#E2BE78" stroke-width="2.5"/>'
    '<circle cx="320" cy="50" r="19" fill="none" stroke="#E2BE78" stroke-opacity="0.45" stroke-width="1"/>'
    '<g transform="translate(306 36) scale(1.17)" fill="none" stroke="#E2BE78" stroke-width="1.7" '
    'stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="4.6" r="2.1"/>'
    '<path d="M12 6.7V21"/><path d="M8.6 9.6h6.8"/>'
    '<path d="M4.6 13.6c0.6 4.6 3.6 7.2 7.4 7.4c3.8-0.2 6.8-2.8 7.4-7.4"/></g></svg>'
)


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def rank_name(code: str) -> str:
    """"AS" -> "Ace", "TD" -> "10", "7C" -> "7"."""
    return RANK_NAME.get(code[0], code[0])


def rank_with_article(code: str) -> str:
    """"AS" -> "an Ace", "8H" -> "an 8", "KD" -> "a King"."""
    name = rank_name(code)
    return ("an " if name[0] in "A8" else "a ") + name


def signed(n: int) -> str:
    """+1,000 / −500 / 0, with a proper minus sign."""
    n = int(n)
    if n > 0:
        return f"+{n:,}"
    if n < 0:
        return f"{MINUS}{abs(n):,}"
    return "0"


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------
def card(code=None, size: str = "lg", *, lit: bool = False, style: str = "") -> str:
    """One card, face up (code) or face down (None). lit outlines it in gold."""
    attr = f' style="{style}"' if style else ""
    extra = " lit" if lit else ""
    if code is None:
        return (f'<div class="card {size} back{extra}"{attr}><div class="face">'
                f'<div class="medal">{ANCHOR_SVG}</div></div></div>')
    rank = "10" if code[0] == "T" else code[0]
    suit = SUIT_GLYPH[code[1]]
    red = " red" if code[1] in RED_SUITS else ""
    return (f'<div class="card {size}{red}{extra}"{attr}>'
            f'<div class="idx"><b>{rank}</b><i>{suit}</i></div>'
            f'<div class="pip">{suit}</div></div>')


def fan(codes, size: str = "lg", *, max_w: int = TABLE_W) -> str:
    """Cards overlapped like a dealt hand, tightening so any number of cards fits max_w."""
    codes = list(codes)
    width = CARD_W[size]
    step = FAN_STEP[size]
    if len(codes) > 1:
        step = min(step, (max_w - width) / (len(codes) - 1))
    margin = math.floor(step - width)  # round the overlap up so the fan never overruns
    parts = [card(c, size, style=("" if i == 0 else f"margin-left:{margin}px"))
             for i, c in enumerate(codes)]
    return f'<div class="fan">{"".join(parts)}</div>'


def row(codes, size: str = "md") -> str:
    """Cards side by side with a gap (nothing covered)."""
    return f'<div class="row">{"".join(card(c, size) for c in codes)}</div>'


# ---------------------------------------------------------------------------
# Table pieces
# ---------------------------------------------------------------------------
def seat(label: str, cards_html: str, total: str = "", *, dealer: bool = False,
         tone: str = "") -> str:
    """A hand with its label and total underneath. tone: "" | "bust" | "gold" | "name"
    (name = a hand name like "Pair of Kings", set smaller than a number)."""
    cls = "seat dealer" if dealer else "seat"
    total_html = f'<span class="total serif {tone}">{esc(total)}</span>' if total else ""
    return (f'<div class="{cls}">{cards_html}<div class="label">'
            f'<span class="who ellipsis">{esc(label)}</span>{total_html}</div></div>')


def arc() -> str:
    return f'<div class="arc">{ARC_SVG}</div>'


def stat(label: str, value: str, *, right: bool = False, off: bool = False,
         narrow: bool = False) -> str:
    """A label over a big number, beside a stage. narrow suits a row of three cards."""
    cls = "stat" + (" right" if right else "") + (" off" if off else "") + (" narrow" if narrow else "")
    small = " small" if len(value) > 5 else ""
    return (f'<div class="{cls}"><span class="lab">{esc(label)}</span>'
            f'<span class="val serif{small}">{esc(value)}</span></div>')


def stage(centre_html: str, left: str = "", right: str = "") -> str:
    """A centre card (or row) with a stat column either side; no stats centres it alone."""
    if not left and not right:
        return f'<div class="stage solo">{centre_html}</div>'
    return f'<div class="stage">{left}{centre_html}{right}</div>'


def held_hand(codes, held=None) -> str:
    """Five cards side by side; while choosing, held cards are outlined and tagged HELD."""
    slots = []
    for i, code in enumerate(codes):
        on = bool(held and held[i])
        tag = '<span class="held">Held</span>' if on else '<span class="held off">Held</span>'
        slots.append(f'<div class="slot">{card(code, "md", lit=on)}{tag if held is not None else ""}</div>')
    return f'<div class="slots">{"".join(slots)}</div>'


def paytable(rows, hit=None) -> str:
    """rows: [(key, name, multiplier)] split over two columns; the row whose key == hit
    is highlighted in gold."""
    rows = list(rows)
    half = (len(rows) + 1) // 2

    def col(chunk):
        return '<div class="col">' + "".join(
            f'<div class="r{" hit" if key == hit else ""}"><span class="n">{esc(name)}</span>'
            f'<span class="m serif">{esc(mult)}×</span></div>' for key, name, mult in chunk) + "</div>"

    return f'<div class="pay">{col(rows[:half])}{col(rows[half:])}</div>'


def rail(head: str, sub: str = "", *, money: str | None = None, tone: str = "push",
         actions: str | None = None, side_html: str | None = None, ledger=(), head_tone: str = "") -> str:
    """The dark strip at the bottom: what happened (or what to do next), then the ledger.

    money shows on the right in a result colour (tone: win | lose | push | gold);
    actions shows the next moves in gold small caps instead; side_html puts any markup there
    (roulette's little wheel). ledger is [(label, value)]."""
    crowded = len(head) > 14 or (actions and money is None and len(head) + len(actions) > 32)
    head_cls = "head serif" + (" small" if crowded else "") + (f" {head_tone}" if head_tone else "")
    sub_html = f'<span class="sub">{esc(sub)}</span>' if sub else ""
    if money is not None:
        side = f'<span class="money serif {tone}">{esc(money)}</span>'
    elif actions:
        side = f'<span class="actions">{esc(actions)}</span>'
    elif side_html:
        side = f'<div style="flex:none">{side_html}</div>'
    else:
        side = ""
    cols = "".join(f'<div class="c"><span class="l">{esc(lab)}</span>'
                   f'<span class="v serif">{esc(val)}</span></div>' for lab, val in ledger)
    ledger_html = f'<div class="ledger">{cols}</div>' if cols else ""
    return (f'<div class="rail"><div class="main"><div class="say">'
            f'<span class="{head_cls}">{esc(head)}</span>{sub_html}</div>{side}</div>'
            f'{ledger_html}</div>')


def player_ledger(player_id, *, bet: int, session_count: int, session_net: int,
                  current_net: int = 0, over: bool = False, bet_label: str = "Bet") -> list:
    """Bet / Balance / Session / Career for a single-player game."""
    from lib.economy.casino_stats import session_career
    from lib.economy.economy_manager import get_bb
    _, session_total, _, career_net = session_career(
        player_id, session_count=session_count, session_net=session_net,
        current_net=current_net, over=over)
    return [(bet_label, f"{int(bet):,}"), ("Balance", f"{get_bb(player_id):,}"),
            ("Session", signed(session_total)), ("Career", signed(career_net))]


# ---------------------------------------------------------------------------
# The felt texture: the table's shading ordered-dithered into a handful of greens
# ---------------------------------------------------------------------------
FELT_W, FELT_H = 820, 1800       # tall enough for the tallest table (roulette results)
DITHER_CELL = 4                  # px per Bayer cell - big enough to survive Discord's downscale
DITHER_LEVELS = 18               # quantisation steps per channel
_BAYER = ((0, 8, 2, 10), (12, 4, 14, 6), (3, 11, 1, 9), (15, 7, 13, 5))


def _grey_png(size: int, pixel) -> str:
    """A greyscale PNG as a data URI; pixel(x, y) -> 0..255."""
    raw = b"".join(b"\x00" + bytes(pixel(x, y) for x in range(size)) for y in range(size))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 0, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
    return "data:image/png;base64," + base64.b64encode(png).decode()


@lru_cache(maxsize=1)
def felt_texture() -> str:
    """A CSS url() of the felt as an SVG. The radial shading is posterised to a few greens
    per channel, and a Bayer threshold tile (added in before the posterise) decides which
    pixels round up - a classic ordered dither, in flat colours that compress well."""
    tile = 4 * DITHER_CELL
    bayer = _grey_png(tile, lambda x, y: int((_BAYER[(y // DITHER_CELL) % 4][(x // DITHER_CELL) % 4] + 0.5) / 16 * 255))
    amp = 1 / DITHER_LEVELS
    table = " ".join(f"{k / (DITHER_LEVELS - 1):.4f}" for k in range(DITHER_LEVELS))
    funcs = "".join(f"<feFunc{c} type='discrete' tableValues='{table}'/>" for c in "RGB")
    svg = (
        f"<svg xmlns='http://www.w3.org/2000/svg' xmlns:xlink='http://www.w3.org/1999/xlink' width='{FELT_W}' height='{FELT_H}'>"
        "<defs><radialGradient id='g' gradientUnits='userSpaceOnUse' cx='410' cy='510' r='984' "
        "gradientTransform='translate(410 510) scale(1 0.9) translate(-410 -510)'>"
        "<stop offset='0' stop-color='#145040'/><stop offset='0.55' stop-color='#0E3B2C'/>"
        "<stop offset='1' stop-color='#0A2E22'/></radialGradient>"
        f"<filter id='q' filterUnits='userSpaceOnUse' x='0' y='0' width='{FELT_W}' height='{FELT_H}' "
        "color-interpolation-filters='sRGB'>"
        f"<feImage xlink:href='{bayer}' x='0' y='0' width='{tile}' height='{tile}' result='t'/>"
        "<feTile in='t' result='th'/>"
        f"<feComposite in='SourceGraphic' in2='th' operator='arithmetic' k1='0' k2='1' k3='{amp:.4f}' "
        f"k4='{-amp / 2:.4f}' result='s'/>"
        f"<feComponentTransfer in='s'>{funcs}</feComponentTransfer></filter></defs>"
        f"<rect width='{FELT_W}' height='{FELT_H}' fill='url(#g)' filter='url(#q)'/></svg>")
    return 'url("data:image/svg+xml,' + quote(svg, safe=" =:/,'") + '")'


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------
_TOKEN = re.compile(r"\{\{(TITLE|TAG|BODY|RAIL|VARIANT|TEXTURE)\}\}")


def build_page(title: str, tag: str, body: str, rail_html: str, *, variant: str = "") -> str:
    """Fill the template in a single pass, so text inside a player's name can never be
    read as another token. variant "arena" swaps the felt for a full-bleed scene."""
    parts = {"TITLE": esc(title), "TAG": esc(tag), "BODY": body, "RAIL": rail_html,
             "VARIANT": f" {variant}" if variant else "", "TEXTURE": felt_texture()}
    return _TOKEN.sub(lambda m: parts[m.group(1)], read_html_template(TEMPLATE))


async def render(page: str) -> io.BytesIO:
    from lib.core.image_processing import screenshot_html
    return await screenshot_html(page, size=(WIDTH, 1400), element_selector=".felt")
