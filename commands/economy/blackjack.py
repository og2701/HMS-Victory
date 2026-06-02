"""HMS Victory - Blackjack (vs-the-house).

A premium single-hand blackjack game. The table is rendered as an HTML→PNG image
(templates/blackjack_table.html) via the shared headless-Chrome pipeline and wrapped
in a Components V2 LayoutView with Hit / Stand / Double Down buttons - mirroring the
prediction system. If image rendering is disabled (config.BLACKJACK_IMAGE_ENABLED) or
fails, it falls back to a native CV2 text layout, exactly like build_prediction_render.

Economy model (UKP is conserved; the server bank is the house):
    • Stake:  remove_bb(uid, bet)              - to_bank=True, stake enters the bank.
    • Win:    add_bb(uid, 2·staked, taxable=False)        - paid from the bank.
    • BJ 3:2: add_bb(uid, staked + ⌊3·staked/2⌋, taxable=False).
    • Push:   add_bb(uid, staked, taxable=False)          - stake refunded.
    • Loss:   nothing - the staked UKP stays in the bank (the house edge / sink).
Gaming payouts are tax-exempt (taxable=False) like wager wins, so the small house edge
is a mild UKPence sink rather than a faucet. See lib/economy/economy_manager.add_bb.
"""

import asyncio
import io
import html as _html
import logging
import random
import time
import uuid

import discord
from discord import Interaction

from lib.economy.economy_manager import get_bb, add_bb, remove_bb, UKPenceManager
from lib.core.file_operations import (
    read_html_template,
    load_persistent_views,
    save_persistent_views,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cards & hand evaluation
# ---------------------------------------------------------------------------
# A card is a 2-char code: rank + suit, e.g. "AS", "TD" (T=ten), "QH".
RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "T", "J", "Q", "K"]
SUITS = ["S", "H", "D", "C"]
SUIT_GLYPH = {"S": "♠", "H": "♥", "D": "♦", "C": "♣"}
SUIT_EMOJI = {"S": "♠️", "H": "♥️", "D": "♦️", "C": "♣️"}
RED_SUITS = {"H", "D"}
DEALER_STANDS_ON = 17  # dealer stands on all 17s (incl. soft 17) - player-friendly.


def _fresh_deck() -> list:
    deck = [r + s for s in SUITS for r in RANKS]
    random.shuffle(deck)
    return deck


def hand_value(cards: list) -> tuple:
    """Return (best_total, is_soft). Aces count as 11 then drop to 1 as needed."""
    total = 0
    aces = 0
    for c in cards:
        r = c[0]
        if r in ("T", "J", "Q", "K"):
            total += 10
        elif r == "A":
            total += 11
            aces += 1
        else:
            total += int(r)
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total, aces > 0  # a still-counted ace means the hand is "soft"


def _disp_rank(r: str) -> str:
    return "10" if r == "T" else r


# ---------------------------------------------------------------------------
# Game state
# ---------------------------------------------------------------------------
class BlackjackGame:
    """A single blackjack hand. Mutated in place across Hit/Stand/Double within one
    hand; Play Again creates a brand-new game object (and game_id)."""

    def __init__(self, game_id, player_id, player_name, channel_id, bet,
                 deck, player_cards, dealer_cards, *, total_staked=None,
                 doubled=False, state="player", hole_revealed=False,
                 message_id=None, created_ts=None):
        self.game_id = game_id
        self.player_id = int(player_id)
        self.player_name = player_name
        self.channel_id = channel_id
        self.bet = int(bet)
        self.total_staked = int(total_staked if total_staked is not None else bet)
        self.deck = deck
        self.player_cards = player_cards
        self.dealer_cards = dealer_cards
        self.doubled = doubled
        self.state = state            # "player" | "over"
        self.hole_revealed = hole_revealed
        self.message_id = message_id
        self.created_ts = created_ts or int(time.time())
        # transient (never serialised)
        self.settled = False
        self.replayed = False
        self.busy = False             # True while an action is mid-render (drops double-clicks)
        self.outcome = None           # "win" | "lose" | "push" | "blackjack"
        self.payout = 0
        self.net = 0
        self.lock = asyncio.Lock()

    # --- construction ---
    @classmethod
    def new(cls, player_id, player_name, channel_id, bet):
        deck = _fresh_deck()
        player = [deck.pop(), deck.pop()]
        dealer = [deck.pop(), deck.pop()]
        game = cls(
            game_id=uuid.uuid4().hex[:12],
            player_id=player_id, player_name=player_name, channel_id=channel_id,
            bet=bet, deck=deck, player_cards=player, dealer_cards=dealer,
        )
        # Naturals resolve immediately (dealer effectively peeks).
        if game.is_blackjack(player) or game.is_blackjack(dealer):
            game.hole_revealed = True
            game.state = "over"
        return game

    # --- evaluation helpers ---
    @staticmethod
    def is_blackjack(cards: list) -> bool:
        return len(cards) == 2 and hand_value(cards)[0] == 21

    def player_total(self) -> int:
        return hand_value(self.player_cards)[0]

    def dealer_total(self) -> int:
        return hand_value(self.dealer_cards)[0]

    def player_busted(self) -> bool:
        return self.player_total() > 21

    def can_double(self) -> bool:
        return self.state == "player" and len(self.player_cards) == 2 and not self.doubled

    # --- mutations ---
    def hit_player(self):
        self.player_cards.append(self.deck.pop())
        if self.player_busted():
            # Player loses immediately; reveal the hole for transparency but the
            # dealer does not draw (standard casino rule).
            self.hole_revealed = True
            self.state = "over"

    def dealer_play(self):
        """Reveal the hole card and draw to DEALER_STANDS_ON, then end the hand."""
        self.hole_revealed = True
        while hand_value(self.dealer_cards)[0] < DEALER_STANDS_ON:
            self.dealer_cards.append(self.deck.pop())
        self.state = "over"

    # --- serialisation (only in-play games are persisted) ---
    def to_dict(self) -> dict:
        return {
            "type": "blackjack",
            "game_id": self.game_id,
            "player_id": self.player_id,
            "player_name": self.player_name,
            "channel_id": self.channel_id,
            "message_id": self.message_id,
            "bet": self.bet,
            "total_staked": self.total_staked,
            "doubled": self.doubled,
            "state": self.state,
            "hole_revealed": self.hole_revealed,
            "deck": self.deck,
            "player_cards": self.player_cards,
            "dealer_cards": self.dealer_cards,
            "created_ts": self.created_ts,
        }

    @classmethod
    def from_dict(cls, d: dict):
        return cls(
            game_id=d["game_id"], player_id=d["player_id"],
            player_name=d.get("player_name", "Player"), channel_id=d.get("channel_id"),
            bet=d["bet"], deck=d["deck"], player_cards=d["player_cards"],
            dealer_cards=d["dealer_cards"], total_staked=d.get("total_staked", d["bet"]),
            doubled=d.get("doubled", False), state=d.get("state", "player"),
            hole_revealed=d.get("hole_revealed", False), message_id=d.get("message_id"),
            created_ts=d.get("created_ts"),
        )


# ---------------------------------------------------------------------------
# Persistence (only in-play games; removed the moment a hand settles)
# ---------------------------------------------------------------------------
def save_game(game: BlackjackGame):
    if game.message_id is None:
        return
    views = load_persistent_views()
    views[str(game.message_id)] = game.to_dict()
    save_persistent_views(views)


def delete_game(message_id):
    if message_id is None:
        return
    views = load_persistent_views()
    if str(message_id) in views:
        del views[str(message_id)]
        save_persistent_views(views)


# ---------------------------------------------------------------------------
# Economy
# ---------------------------------------------------------------------------
def _credit(uid: int, amount: int, reason: str):
    """Pay a player from the bank (tax-exempt). If the bank is somehow insolvent,
    mint the payout directly rather than rob a legitimate winner, and log loudly."""
    if amount <= 0:
        return
    if not add_bb(uid, amount, reason=reason, taxable=False):
        logger.critical(
            "Bank insolvent paying blackjack %s of %s UKP to %s - minting to honour the win.",
            reason, amount, uid,
        )
        UKPenceManager.add_amount(uid, amount, reason=f"{reason} [bank insolvent - minted]")


def _decide(game: BlackjackGame):
    """Work out the outcome and payout (no money moves). Safe to call repeatedly."""
    if game.outcome is not None:
        return

    pt = game.player_total()
    dt = game.dealer_total()
    pbj = game.is_blackjack(game.player_cards)
    dbj = game.is_blackjack(game.dealer_cards)
    staked = game.total_staked

    if pt > 21:
        outcome, payout = "lose", 0
    elif pbj and dbj:
        outcome, payout = "push", staked
    elif pbj:
        outcome, payout = "blackjack", staked + (staked * 3) // 2
    elif dbj:
        outcome, payout = "lose", 0
    elif dt > 21:
        outcome, payout = "win", 2 * staked
    elif pt > dt:
        outcome, payout = "win", 2 * staked
    elif pt < dt:
        outcome, payout = "lose", 0
    else:
        outcome, payout = "push", staked

    game.outcome = outcome
    game.payout = payout
    game.net = payout - staked


def _payout(game: BlackjackGame):
    """Credit the decided payout exactly once. Idempotent (claims `settled` first)."""
    if game.settled:
        return
    game.settled = True
    if game.payout > 0:
        reason = {
            "blackjack": "Blackjack win (3:2)",
            "win": "Blackjack win",
            "push": "Blackjack push (refund)",
        }[game.outcome]
        _credit(game.player_id, game.payout, reason)


def _settle(game: BlackjackGame):
    """Decide and pay out in one step (used once a hand concludes mid-play)."""
    _decide(game)
    _payout(game)


# ---------------------------------------------------------------------------
# Rendering - premium HTML table
# ---------------------------------------------------------------------------
# Card fan geometry (keep in sync with .card width in templates/blackjack_table.html).
_CARD_W = 160
_HAND_MAXW = 600       # cards never spill past this, however many are dealt
_DEFAULT_STEP = 112    # comfortable spacing for small hands


def _overlaps(n: int) -> list:
    """Per-card left-margins (for cards after the first) so the fan fits _HAND_MAXW."""
    if n <= 1:
        return []
    step = min(_DEFAULT_STEP, (_HAND_MAXW - _CARD_W) / (n - 1))
    return [round(step - _CARD_W)] * (n - 1)


def _style(margin_left):
    return f' style="margin-left:{margin_left}px"' if margin_left is not None else ""


def _card_html(code: str, margin_left=None) -> str:
    r, s = code[0], code[1]
    disp = _disp_rank(r)
    glyph = SUIT_GLYPH[s]
    red = " red" if s in RED_SUITS else ""
    return (
        f'<div class="card{red}"{_style(margin_left)}>'
        f'<span class="corner tl"><b>{disp}</b><i>{glyph}</i></span>'
        f'<span class="pip">{glyph}</span>'
        f'<span class="corner br"><b>{disp}</b><i>{glyph}</i></span>'
        f"</div>"
    )


def _back_html(margin_left=None) -> str:
    return f'<div class="card back"{_style(margin_left)}></div>'


def _hand_html(specs: list) -> str:
    """Render a hand from specs (a card code, or None for a face-down back),
    overlapping cards just enough that even a big hand stays on the table."""
    margins = _overlaps(len(specs))
    parts = []
    for i, spec in enumerate(specs):
        ml = None if i == 0 else margins[i - 1]
        parts.append(_back_html(ml) if spec is None else _card_html(spec, ml))
    return "".join(parts)


def _banner_html(game: BlackjackGame) -> str:
    o = game.outcome
    if o == "blackjack":
        cls, head, sub = "win", "Blackjack!", f"+{game.net:,} UKPence"
    elif o == "win":
        cls, head, sub = "win", "You Win", f"+{game.net:,} UKPence"
    elif o == "push":
        cls, head, sub = "push", "Push", "Stake returned"
    else:
        cls = "lose"
        head = "Bust" if game.player_busted() else "Dealer Wins"
        sub = f"-{game.total_staked:,} UKPence"
    return (
        f'<div class="banner-wrap"><div class="banner {cls}">'
        f'<div class="head">{head}</div><div class="sub">{sub}</div>'
        f"</div></div>"
    )


def build_table_html(game: BlackjackGame) -> str:
    template = read_html_template("templates/blackjack_table.html")

    if game.hole_revealed:
        dealer_cards = _hand_html(list(game.dealer_cards))
        dt = game.dealer_total()
        dealer_total = str(dt)
        d_cls = "bust" if dt > 21 else ("bj" if game.is_blackjack(game.dealer_cards) else "")
    else:
        dealer_cards = _hand_html([game.dealer_cards[0], None])  # None = face-down hole card
        dealer_total = "?"
        d_cls = ""

    player_cards = _hand_html(list(game.player_cards))
    pt = game.player_total()
    if pt > 21:
        p_cls = "bust"
    elif game.is_blackjack(game.player_cards):
        p_cls = "bj"
    elif game.state == "over" and game.outcome in ("win", "blackjack"):
        p_cls = "win"
    else:
        p_cls = ""

    if game.state == "over":
        banner = _banner_html(game)
        hint = "Round complete"
    else:
        banner = ""
        hint = "Your move - Hit or Stand" + (" or Double" if game.can_double() else "")

    return (
        template
        .replace("{{RULE}}", "Dealer stands on all 17s<br>Blackjack pays 3 : 2")
        .replace("{{PLAYER_NAME}}", _html.escape(str(game.player_name)[:24]) or "Player")
        .replace("{{DEALER_CARDS}}", dealer_cards)
        .replace("{{PLAYER_CARDS}}", player_cards)
        .replace("{{DEALER_TOTAL}}", dealer_total)
        .replace("{{PLAYER_TOTAL}}", str(pt))
        .replace("{{DEALER_TOTAL_CLASS}}", d_cls)
        .replace("{{PLAYER_TOTAL_CLASS}}", p_cls)
        .replace("{{BET}}", f"{game.total_staked:,}")
        .replace("{{BALANCE}}", f"{get_bb(game.player_id):,}")
        .replace("{{STATE_HINT}}", hint)
        .replace("{{RESULT_BANNER}}", banner)
    )


async def render_blackjack_image(game: BlackjackGame) -> io.BytesIO:
    from lib.core.image_processing import screenshot_html
    html_out = build_table_html(game)
    # Portrait table; the CDP element-clip captures the real .table size regardless.
    return await screenshot_html(html_out, size=(900, 1500), element_selector=".table")


def _native_text(game: BlackjackGame) -> str:
    """Components V2 text fallback (mobile-crisp, zero render cost)."""
    def card_text(code):
        return f"`{_disp_rank(code[0])}`{SUIT_EMOJI[code[1]]}"

    if game.hole_revealed:
        dealer = "  ".join(card_text(c) for c in game.dealer_cards)
        dt = str(game.dealer_total())
    else:
        dealer = card_text(game.dealer_cards[0]) + "  ❓"
        dt = "?"
    player = "  ".join(card_text(c) for c in game.player_cards)
    pt = game.player_total()

    lines = [
        f"## 🎴 Blackjack - {game.total_staked:,} UKPence",
        f"**Dealer** ({dt}):  {dealer}",
        f"**{game.player_name}** ({pt}):  {player}",
    ]
    bal = get_bb(game.player_id)
    if game.state == "over":
        if game.outcome == "blackjack":
            tag = f"🃏 **Blackjack!** +{game.net:,} UKPence"
        elif game.outcome == "win":
            tag = f"✅ **You win** +{game.net:,} UKPence"
        elif game.outcome == "push":
            tag = "🤝 **Push** - stake returned"
        else:
            tag = f"❌ **{'Bust' if pt > 21 else 'Dealer wins'}** -{game.total_staked:,} UKPence"
        lines.append(f"-# {tag}  ·  Balance: {bal:,} UKPence")
    else:
        lines.append(f"-# Your move  ·  Balance: {bal:,} UKPence")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Components V2 view
# ---------------------------------------------------------------------------
ACCENT = discord.Colour(0x1C6B46)  # felt green


def _action_row(game: BlackjackGame) -> discord.ui.ActionRow:
    row = discord.ui.ActionRow()
    if game.state == "player":
        hit = discord.ui.Button(
            label="Hit", emoji="🃏", style=discord.ButtonStyle.success,
            custom_id=f"blackjack:{game.game_id}:hit",
        )
        hit.callback = _make_cb(game, "hit")
        row.add_item(hit)

        stand = discord.ui.Button(
            label="Stand", emoji="✋", style=discord.ButtonStyle.secondary,
            custom_id=f"blackjack:{game.game_id}:stand",
        )
        stand.callback = _make_cb(game, "stand")
        row.add_item(stand)

        if game.can_double():
            dbl = discord.ui.Button(
                label="Double Down", emoji="💰", style=discord.ButtonStyle.primary,
                custom_id=f"blackjack:{game.game_id}:double",
            )
            dbl.callback = _make_cb(game, "double")
            row.add_item(dbl)

        # Rules is only offered on the untouched opening hand (anyone may click it);
        # the owner's first Hit/Stand/Double re-renders the hand and it disappears.
        if len(game.player_cards) == 2 and not game.doubled:
            rules = discord.ui.Button(
                label="Rules", emoji="📖", style=discord.ButtonStyle.secondary,
                custom_id=f"blackjack:{game.game_id}:rules",
            )
            rules.callback = _make_cb(game, "rules")
            row.add_item(rules)
    else:
        again = discord.ui.Button(
            label="Play Again", emoji="🔁", style=discord.ButtonStyle.primary,
            custom_id=f"blackjack:{game.game_id}:again",
        )
        again.callback = _make_cb(game, "again")
        row.add_item(again)
    return row


async def build_blackjack_layout(game: BlackjackGame, client):
    """Return (view, files) for sending/editing. Renders the premium table image when
    enabled; on failure falls back to a native text layout (files == [])."""
    import config
    files = []
    view = discord.ui.LayoutView(timeout=None)
    used_image = False

    if getattr(config, "BLACKJACK_IMAGE_ENABLED", True):
        try:
            img = await render_blackjack_image(game)
            files = [discord.File(img, filename="blackjack.png")]
            # Image goes straight into the view - no Container wrapper, so there's no
            # accent-rail "embed" box around it. The rendered table carries its own frame.
            gallery = discord.ui.MediaGallery()
            gallery.add_item(media="attachment://blackjack.png")
            view.add_item(gallery)
            used_image = True
        except Exception:
            logger.warning("Blackjack image render failed; using native layout.", exc_info=True)

    if not used_image:
        # Text-only fallback has no image to carry the brand, so a themed container helps.
        container = discord.ui.Container(accent_colour=ACCENT)
        container.add_item(discord.ui.TextDisplay(_native_text(game)))
        view.add_item(container)

    view.add_item(_action_row(game))
    return view, files


def build_control_view(game: BlackjackGame) -> discord.ui.LayoutView:
    """A buttons-only persistent view used on restart to re-register click routing.
    The message keeps the table image it already had; the first click re-renders."""
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(_action_row(game))
    return view


# ---------------------------------------------------------------------------
# Interaction handling
# ---------------------------------------------------------------------------
def _make_cb(game: BlackjackGame, action: str):
    async def _cb(interaction: Interaction):
        await _handle_action(interaction, game, action)
    return _cb


async def _refresh(interaction: Interaction, game: BlackjackGame, client):
    view, files = await build_blackjack_layout(game, client)
    await interaction.edit_original_response(view=view, attachments=files)
    try:
        client.add_view(view, message_id=game.message_id)
    except Exception:
        logger.debug("add_view after refresh failed (non-fatal)", exc_info=True)


async def _show_rules(interaction: Interaction):
    """Ephemeral house rules. Open to anyone (no owner check) and changes no state."""
    import config
    min_bet = getattr(config, "BLACKJACK_MIN_BET", 10)
    max_bet = getattr(config, "BLACKJACK_MAX_BET", 250_000)
    rules = (
        "## 🎴 Blackjack - House Rules\n"
        "Beat the dealer by getting closer to **21** than they do, without going over.\n\n"
        "- **Card values:** 2-10 are face value, J/Q/K are 10, and an Ace is 1 or 11 - "
        "whichever is better for your hand (it drops to 1 automatically to save you from a bust).\n"
        "- **Blackjack:** an Ace plus a 10-value card on your first two cards. Pays **3:2**.\n"
        "- **Hit** draws another card; **Stand** locks in your total.\n"
        "- **Double Down:** on your opening two cards only - doubles your stake for exactly "
        "one more card, then stands.\n"
        "- **Dealer** reveals the hole card and draws until **17** (stands on all 17s, soft 17 included).\n"
        "- **Bust** (over 21) loses at once. Matching totals **push** and your stake is returned.\n"
        f"- **Bets:** {min_bet:,} - {max_bet:,} UKPence. Stakes go to the house bank; wins are paid from it.\n\n"
        "-# Good luck. 🇬🇧"
    )
    await interaction.response.send_message(rules, ephemeral=True)


async def _handle_action(interaction: Interaction, game: BlackjackGame, action: str):
    # Rules is open to everyone and never touches game state, so it's handled before
    # the owner / busy / state checks below.
    if action == "rules":
        await _show_rules(interaction)
        return

    if interaction.user.id != game.player_id:
        await interaction.response.send_message(
            "This isn't your table - deal your own hand with `/blackjack`.", ephemeral=True
        )
        return

    # Drop clicks that arrive while a previous one is still being processed. The image
    # render takes ~1-2s and the old buttons stay live during it, so a fast double-click
    # would otherwise queue a second action (an extra Hit). Reading and setting `busy`
    # has no await between them, so it's atomic on the event loop - exactly one action
    # runs; the rest are silently acknowledged.
    if game.busy:
        await interaction.response.defer()
        return
    game.busy = True
    client = interaction.client
    try:
        async with game.lock:
            if action == "again":
                await _handle_again(interaction, game, client)
                return

            if game.state != "player":
                await interaction.response.defer()  # stale click; the hand is already over
                return

            if action == "double":
                if not game.can_double():
                    await interaction.response.send_message(
                        "You can only double down on your opening two cards.", ephemeral=True
                    )
                    return
                if get_bb(game.player_id) < game.bet:
                    await interaction.response.send_message(
                        f"You need {game.bet:,} more UKPence to double down.", ephemeral=True
                    )
                    return

            await interaction.response.defer()

            if action == "hit":
                game.hit_player()
            elif action == "stand":
                game.dealer_play()
            elif action == "double":
                if not remove_bb(game.player_id, game.bet, reason="Blackjack double down"):
                    await interaction.followup.send(
                        "You don't have enough UKPence to double down.", ephemeral=True
                    )
                    return
                game.total_staked += game.bet
                game.doubled = True
                game.player_cards.append(game.deck.pop())
                if game.player_busted():
                    game.hole_revealed = True
                    game.state = "over"
                else:
                    game.dealer_play()

            if game.state == "over":
                if not game.settled:
                    _settle(game)
                delete_game(game.message_id)
            else:
                save_game(game)

            # Money is already settled/persisted above; a failed redraw is cosmetic only.
            try:
                await _refresh(interaction, game, client)
            except Exception:
                logger.error("Blackjack redraw failed after applying the move.", exc_info=True)
    finally:
        game.busy = False


async def _handle_again(interaction: Interaction, old_game: BlackjackGame, client):
    """Start a fresh hand on the same message, reusing the previous stake amount."""
    import config
    if old_game.replayed:  # a previous click on this message already dealt the next hand
        await interaction.response.defer()
        return

    uid = old_game.player_id
    bet = old_game.bet

    if not getattr(config, "BLACKJACK_ENABLED", True):
        await interaction.response.send_message("The blackjack table is closed.", ephemeral=True)
        return
    if get_bb(uid) < bet:
        await interaction.response.send_message(
            f"You need {bet:,} UKPence to play another hand.", ephemeral=True
        )
        return
    if not remove_bb(uid, bet, reason="Blackjack bet"):
        await interaction.response.send_message("You don't have enough UKPence.", ephemeral=True)
        return
    old_game.replayed = True  # claim under the lock so a double-click can't deal twice

    await interaction.response.defer()

    new_game = BlackjackGame.new(uid, old_game.player_name, old_game.channel_id, bet)
    new_game.message_id = old_game.message_id
    if new_game.state == "over":
        _decide(new_game)  # decide for display; pay only after the message updates

    # Refundable section ends the moment the new hand is shown. If we never get it on
    # screen, nothing has been credited yet, so the new stake is refunded in full.
    try:
        view, files = await build_blackjack_layout(new_game, client)
        await interaction.edit_original_response(view=view, attachments=files)
    except Exception:
        logger.error("Blackjack replay failed before showing the new hand; refunding stake.", exc_info=True)
        _credit(uid, bet, "Blackjack stake refund (replay failed)")
        return

    # New hand is live - persistence/payout/add_view failures are logged, never refunded.
    try:
        if new_game.state == "over":
            _payout(new_game)
            delete_game(new_game.message_id)
        else:
            save_game(new_game)
        client.add_view(view, message_id=new_game.message_id)
    except Exception:
        logger.error("Blackjack replay post-update issue (hand is live).", exc_info=True)


# ---------------------------------------------------------------------------
# Slash command entry point
# ---------------------------------------------------------------------------
async def handle_blackjack_command(interaction: Interaction, amount: int):
    import config

    if getattr(interaction.client, "maintenance_mode", False):
        await interaction.response.send_message(
            "🔧 **Under maintenance** - the bot is restarting for an update. "
            "Hold on a minute before dealing a new hand.", ephemeral=True
        )
        return

    if not getattr(config, "BLACKJACK_ENABLED", True):
        await interaction.response.send_message("The blackjack table is closed.", ephemeral=True)
        return

    min_bet = getattr(config, "BLACKJACK_MIN_BET", 10)
    max_bet = getattr(config, "BLACKJACK_MAX_BET", 250_000)

    if amount < min_bet:
        await interaction.response.send_message(
            f"The minimum bet is {min_bet:,} UKPence.", ephemeral=True
        )
        return
    if amount > max_bet:
        await interaction.response.send_message(
            f"The maximum bet is {max_bet:,} UKPence.", ephemeral=True
        )
        return

    balance = get_bb(interaction.user.id)
    if balance < amount:
        await interaction.response.send_message(
            f"You don't have enough UKPence. Your balance is {balance:,}.", ephemeral=True
        )
        return

    # Take the stake into the bank (escrow / house). Race-safe atomic debit.
    if not remove_bb(interaction.user.id, amount, reason="Blackjack bet"):
        await interaction.response.send_message(
            f"You don't have enough UKPence. Your balance is {get_bb(interaction.user.id):,}.",
            ephemeral=True,
        )
        return

    name = discord.utils.escape_markdown(interaction.user.display_name)
    game = None
    try:
        await interaction.response.defer(thinking=True)
        game = BlackjackGame.new(interaction.user.id, name, interaction.channel_id, amount)
        if game.state == "over":
            _decide(game)  # decide for display; pay only after the message is posted
        view, files = await build_blackjack_layout(game, interaction.client)
        msg = await interaction.followup.send(view=view, files=files)
    except Exception:
        # The hand never made it to the table - nothing has been credited yet, so the
        # stake (sitting in the bank) is refunded in full rather than vanishing.
        logger.error("Blackjack deal failed; refunding stake.", exc_info=True)
        _credit(interaction.user.id, amount, "Blackjack stake refund (deal failed)")
        try:
            await interaction.followup.send(
                "Something went wrong dealing your hand - your stake has been refunded.",
                ephemeral=True,
            )
        except Exception:
            pass
        return

    # The table is now on screen and the hand is live (discord.py registered the view
    # on send). Paying the natural / persisting / add_view happen here: a failure is
    # logged but must NOT refund - the stake belongs to a real, playable hand.
    game.message_id = msg.id
    try:
        if game.state == "over":
            _payout(game)
        else:
            save_game(game)
        interaction.client.add_view(view, message_id=msg.id)
    except Exception:
        logger.error("Blackjack post-send persistence/payout issue (hand is live).", exc_info=True)


# ---------------------------------------------------------------------------
# Restart recovery (called from event_handlers.reattach_persistent_views)
# ---------------------------------------------------------------------------
def reattach_blackjack_view(client, key, value):
    """Re-register click routing for an in-play hand after a restart. Terminal or
    malformed entries are pruned."""
    try:
        game = BlackjackGame.from_dict(value)
    except Exception as e:
        logger.error(f"Pruning malformed blackjack entry {key}: {e}", exc_info=True)
        delete_game(key)  # unreconstructable - drop it so it can't wedge future restarts
        return

    if game.state != "player":  # a hand that already settled - nothing to resume
        delete_game(key)
        return

    try:
        game.message_id = int(key)
        view = build_control_view(game)
        client.add_view(view, message_id=int(key))
    except Exception as e:
        logger.error(f"Failed to reattach blackjack view {key}: {e}", exc_info=True)
