"""Shared foundation for HMS Victory casino card games (Casino War, Red Dog, Three Card
Poker, ...). Provides the card model, hand evaluation, the felt-table renderer (one shared
template), the Components V2 layout wrapper, house-bank economy helpers and persistence -
so each game module only has to express its own rules, view and handlers.

Economy convention (UKP conserved; the server bank is the house):
  • stake:  remove_bb(uid, bet, reason="<Game> bet")     - stake enters the bank.
  • payout: credit_from_bank(uid, amount, "<Game> win")  - add_bb(taxable=False) from bank.
  • loss:   nothing - the stake stays in the bank as the edge.
The reason string MUST contain the game's bank keyword (e.g. "Casino War", "Red Dog",
"Three Card Poker") so lib/economy/bank_manager routes it to that game's P/L counters.
"""

import io
import html as _html
import logging
import random

import discord

from lib.economy.economy_manager import add_bb, UKPenceManager
from lib.core.file_operations import (
    read_html_template,
    load_persistent_views,
    save_persistent_views,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Card model (2-char codes: rank+suit, e.g. "AS", "TD"=ten of diamonds). Ace high.
# ---------------------------------------------------------------------------
RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "T", "J", "Q", "K", "A"]
SUITS = ["S", "H", "D", "C"]
SUIT_GLYPH = {"S": "♠", "H": "♥", "D": "♦", "C": "♣"}
SUIT_EMOJI = {"S": "♠️", "H": "♥️", "D": "♦️", "C": "♣️"}
RED_SUITS = {"H", "D"}
RANK_VALUE = {r: i for i, r in enumerate(RANKS, start=2)}  # 2..14 (A=14)
RANK_NAME = {2: "Two", 3: "Three", 4: "Four", 5: "Five", 6: "Six", 7: "Seven",
             8: "Eight", 9: "Nine", 10: "Ten", 11: "Jack", 12: "Queen", 13: "King", 14: "Ace"}


def fresh_deck() -> list:
    deck = [r + s for s in SUITS for r in RANKS]
    random.shuffle(deck)
    return deck


def value(code: str) -> int:
    return RANK_VALUE[code[0]]


def disp_rank(r: str) -> str:
    return "10" if r == "T" else r


def card_text(code) -> str:
    """Inline card for native (text) layouts: `K`♠️. None -> face-down."""
    if code is None:
        return "🂠"
    return f"`{disp_rank(code[0])}`{SUIT_EMOJI[code[1]]}"


# ---------------------------------------------------------------------------
# Three-card poker hand evaluation (3-card rules: a straight beats a flush!)
# Returns (category, tiebreak) where bigger compares as the stronger hand.
#   5 straight flush · 4 three of a kind · 3 straight · 2 flush · 1 pair · 0 high card
# ---------------------------------------------------------------------------
def three_card_rank(codes: list) -> tuple:
    vals = sorted((value(c) for c in codes), reverse=True)
    suits = [c[1] for c in codes]
    is_flush = len(set(suits)) == 1

    distinct = sorted(set(vals))
    is_straight = False
    straight_high = None
    if len(distinct) == 3:
        if distinct[2] - distinct[0] == 2:           # ordinary run
            is_straight, straight_high = True, distinct[2]
        elif set(distinct) == {14, 2, 3}:            # A-2-3 wheel (ace plays low)
            is_straight, straight_high = True, 3

    counts = {}
    for v in vals:
        counts[v] = counts.get(v, 0) + 1

    if vals[0] == vals[1] == vals[2]:
        return (4, (vals[0],))                       # three of a kind
    if is_straight and is_flush:
        return (5, (straight_high,))                 # straight flush
    if is_straight:
        return (3, (straight_high,))
    if is_flush:
        return (2, tuple(vals))
    if 2 in counts.values():
        pair_rank = next(v for v, c in counts.items() if c == 2)
        kicker = next(v for v, c in counts.items() if c == 1)
        return (1, (pair_rank, kicker))
    return (0, tuple(vals))


def three_card_name(codes: list) -> str:
    cat, tb = three_card_rank(codes)
    if cat == 5:
        return "Straight Flush"
    if cat == 4:
        return f"Three {RANK_NAME[tb[0]]}s"
    if cat == 3:
        return "Straight"
    if cat == 2:
        return f"Flush, {RANK_NAME[tb[0]]} high"
    if cat == 1:
        return f"Pair of {RANK_NAME[tb[0]]}s"
    return f"{RANK_NAME[tb[0]]} high"


# ---------------------------------------------------------------------------
# Five-card poker hand evaluation (for Video Poker etc.)
# Returns (category, tiebreak); bigger compares as the stronger hand.
#   9 royal flush · 8 straight flush · 7 four of a kind · 6 full house · 5 flush
#   4 straight · 3 three of a kind · 2 two pair · 1 pair · 0 high card
# (Use pair rank in the tiebreak to tell a high pair apart, e.g. Jacks or Better.)
# ---------------------------------------------------------------------------
def five_card_rank(cards: list) -> tuple:
    vals = sorted((value(c) for c in cards), reverse=True)
    suits = [c[1] for c in cards]
    is_flush = len(set(suits)) == 1

    distinct = sorted(set(vals))
    is_straight = False
    straight_high = None
    if len(distinct) == 5:
        if distinct[4] - distinct[0] == 4:
            is_straight, straight_high = True, distinct[4]
        elif distinct == [2, 3, 4, 5, 14]:          # A-2-3-4-5 wheel
            is_straight, straight_high = True, 5

    counts = {}
    for v in vals:
        counts[v] = counts.get(v, 0) + 1
    # ranks ordered by (count, value) descending
    by_count = sorted(counts.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)
    shape = [c for _, c in by_count]

    if is_straight and is_flush:
        return (9 if straight_high == 14 else 8, (straight_high,))
    if shape[0] == 4:
        return (7, (by_count[0][0], by_count[1][0]))            # quads + kicker
    if shape[:2] == [3, 2]:
        return (6, (by_count[0][0], by_count[1][0]))            # full house
    if is_flush:
        return (5, tuple(vals))
    if is_straight:
        return (4, (straight_high,))
    if shape[0] == 3:
        kick = sorted((v for v, c in counts.items() if c == 1), reverse=True)
        return (3, (by_count[0][0], *kick))
    if shape[:2] == [2, 2]:
        pairs = sorted((v for v, c in counts.items() if c == 2), reverse=True)
        kick = next(v for v, c in counts.items() if c == 1)
        return (2, (pairs[0], pairs[1], kick))
    if shape[0] == 2:
        kick = sorted((v for v, c in counts.items() if c == 1), reverse=True)
        return (1, (by_count[0][0], *kick))
    return (0, tuple(vals))


def five_card_name(cards: list) -> str:
    cat, tb = five_card_rank(cards)
    return {
        9: "Royal Flush", 8: "Straight Flush", 7: "Four of a Kind", 6: "Full House",
        5: "Flush", 4: "Straight", 3: "Three of a Kind", 2: "Two Pair",
        1: f"Pair of {RANK_NAME[tb[0]]}s", 0: f"{RANK_NAME[tb[0]]} high",
    }[cat]


# ---------------------------------------------------------------------------
# Card HTML for the shared felt-table template body
# ---------------------------------------------------------------------------
def card_html(code=None, *, size: str = "med", facedown: bool = False) -> str:
    if facedown or code is None:
        return f'<div class="card {size} back"></div>'
    r, s = code[0], code[1]
    glyph = SUIT_GLYPH[s]
    red = " red" if s in RED_SUITS else ""
    return (
        f'<div class="card {size}{red}">'
        f'<span class="corner tl"><b>{disp_rank(r)}</b><i>{glyph}</i></span>'
        f'<span class="pip">{glyph}</span>'
        f'<span class="corner br"><b>{disp_rank(r)}</b><i>{glyph}</i></span>'
        f"</div>"
    )


def hand_html(cards: list, *, size: str = "med", overlap: bool = False) -> str:
    """A row of cards; cards may be a code or None (face-down)."""
    cls = "hand overlap" if overlap else "hand"
    inner = "".join(card_html(c, size=size, facedown=(c is None)) for c in cards)
    return f'<div class="{cls}">{inner}</div>'


def zone_html(label: str, cards_html: str, *, badge: str = "", badge_cls: str = "") -> str:
    """A labelled seat: a header (label + optional total/name badge) and its cards."""
    badge_html = f'<span class="badge {badge_cls}">{badge}</span>' if badge else ""
    return (
        f'<div class="zone"><div class="zhead">'
        f'<span class="zlabel">{_html.escape(label)}</span>{badge_html}</div>'
        f"{cards_html}</div>"
    )


def banner_html(kind: str, head: str, sub: str = "") -> str:
    """Result banner. kind: win | lose | push | gold."""
    sub_html = f'<div class="sub">{_html.escape(sub)}</div>' if sub else ""
    return (
        f'<div class="banner-wrap"><div class="banner {kind}">'
        f'<div class="head">{_html.escape(head)}</div>{sub_html}</div></div>'
    )


# ---------------------------------------------------------------------------
# Rendering — one shared felt table (templates/casino_table.html)
# ---------------------------------------------------------------------------
async def render_table(*, title_main: str, title_accent: str, subtitle: str,
                       body_html: str, bet: int, balance: int, hint: str,
                       result_banner: str = "") -> io.BytesIO:
    from lib.core.image_processing import screenshot_html
    tpl = read_html_template("templates/casino_table.html")
    out = (
        tpl
        .replace("{{TITLE_MAIN}}", _html.escape(title_main))
        .replace("{{TITLE_ACCENT}}", _html.escape(title_accent))
        .replace("{{SUBTITLE}}", subtitle)  # may contain <br>
        .replace("{{BODY}}", body_html)
        .replace("{{BET}}", f"{bet:,}")
        .replace("{{BALANCE}}", f"{balance:,}")
        .replace("{{HINT}}", _html.escape(hint))
        .replace("{{RESULT_BANNER}}", result_banner)
    )
    return await screenshot_html(out, size=(900, 1500), element_selector=".table")


# ---------------------------------------------------------------------------
# Components V2 layout
# ---------------------------------------------------------------------------
ACCENT = discord.Colour(0x1C6B46)  # felt green


def build_layout(image_bytes, filename: str, action_row, *, native_text: str,
                 accent: discord.Colour = ACCENT):
    """Return (view, files). Image goes straight into the LayoutView (no Container box);
    if no image, a themed Container holds the native text fallback."""
    view = discord.ui.LayoutView(timeout=None)
    files = []
    if image_bytes is not None:
        files = [discord.File(image_bytes, filename=filename)]
        gallery = discord.ui.MediaGallery()
        gallery.add_item(media=f"attachment://{filename}")
        view.add_item(gallery)
    else:
        container = discord.ui.Container(accent_colour=accent)
        container.add_item(discord.ui.TextDisplay(native_text))
        view.add_item(container)
    if action_row is not None:
        view.add_item(action_row)
    return view, files


# ---------------------------------------------------------------------------
# Economy
# ---------------------------------------------------------------------------
def credit_from_bank(uid: int, amount: int, reason: str):
    """Pay a player from the bank (tax-exempt). If the bank is somehow insolvent, mint
    the payout rather than rob a legitimate winner, and log loudly."""
    if amount <= 0:
        return
    if not add_bb(uid, amount, reason=reason, taxable=False):
        logger.critical("Bank insolvent paying %s of %s to %s - minting to honour the win.",
                        reason, amount, uid)
        UKPenceManager.add_amount(uid, amount, reason=f"{reason} [bank insolvent - minted]")


# ---------------------------------------------------------------------------
# Persistence (generic; keyed by message_id, entry carries its own 'type')
# ---------------------------------------------------------------------------
def save_state(message_id, state: dict):
    if message_id is None:
        return
    views = load_persistent_views()
    views[str(message_id)] = state
    save_persistent_views(views)


def delete_state(message_id):
    if message_id is None:
        return
    views = load_persistent_views()
    if str(message_id) in views:
        del views[str(message_id)]
        save_persistent_views(views)


# ---------------------------------------------------------------------------
# Maintenance gate (shared message)
# ---------------------------------------------------------------------------
async def reject_if_maintenance(interaction) -> bool:
    if getattr(interaction.client, "maintenance_mode", False):
        await interaction.response.send_message(
            "🔧 **Under maintenance** - the bot is restarting for an update. "
            "Hold on a minute before playing.", ephemeral=True
        )
        return True
    return False
