"""HMS Victory - Higher or Lower (vs-the-house card ladder).

Bet, see a card, and guess whether the next card is higher or lower. Each correct
guess multiplies your stake by the odds shown on the button (shaved by the house
factor); cash out any time to bank it, or lose the lot on a wrong guess. A tie (equal
value) is a push - no win, no loss - and the run carries on from the new card.

Visuals + lifecycle mirror the blackjack feature: an HTML->PNG felt table wrapped in
a Components V2 LayoutView, a native text fallback, persistence of in-play ladders to
persistent_views.json (so a restart never strands a debited stake), a busy-guard that
drops double-clicks during a render, and a Rules button on the opening hand.

Economy (UKP conserved; the bank is the house): stake -> bank via remove_bb; a cash-out
pays bet x cumulative-multiplier from the bank via add_bb(taxable=False); a bust pays
nothing (the stake stays in the bank as the edge). Per-step EV multiplies by the payout
factor (<1), so the house edge compounds the longer a player rides.
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
from lib.economy.casino_stats import record_result, session_footer_html
from lib.economy.casino_drain import action_in_flight, deal_in_flight
from lib.core.file_operations import (
    read_html_template,
    load_persistent_views,
    save_persistent_views,
)

logger = logging.getLogger(__name__)

# Cards: 2-char codes (rank+suit), Ace high. Value order 2<...<10<J<Q<K<A.
RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "T", "J", "Q", "K", "A"]
SUITS = ["S", "H", "D", "C"]
SUIT_GLYPH = {"S": "♠", "H": "♥", "D": "♦", "C": "♣"}
SUIT_EMOJI = {"S": "♠️", "H": "♥️", "D": "♦️", "C": "♣️"}
RED_SUITS = {"H", "D"}
RANK_VALUE = {r: i for i, r in enumerate(RANKS, start=2)}  # 2..14 (A=14)


def _fresh_deck() -> list:
    deck = [r + s for s in SUITS for r in RANKS]
    random.shuffle(deck)
    return deck


def _value(code: str) -> int:
    return RANK_VALUE[code[0]]


def _disp_rank(r: str) -> str:
    return "10" if r == "T" else r


# ---------------------------------------------------------------------------
# Game state
# ---------------------------------------------------------------------------
class HigherLowerGame:
    def __init__(self, game_id, player_id, player_name, channel_id, bet,
                 deck, current, history=None, *, cumulative=1.0, steps=0,
                 state="player", message_id=None, created_ts=None):
        self.game_id = game_id
        self.player_id = int(player_id)
        self.player_name = player_name
        self.channel_id = channel_id
        self.bet = int(bet)
        self.deck = deck
        self.current = current
        self.history = history or []
        self.cumulative = float(cumulative)
        self.steps = int(steps)
        self.state = state            # "player" | "over"
        self.message_id = message_id
        self.created_ts = created_ts or int(time.time())
        # transient
        self.settled = False
        self.replayed = False
        self.busy = False
        self.outcome = None           # "win" (cashed out) | "lose" (busted)
        self.payout = 0
        self.net = 0
        self.lock = asyncio.Lock()
        self.mult_higher = None
        self.mult_lower = None
        self._recompute()

    @classmethod
    def new(cls, player_id, player_name, channel_id, bet):
        deck = _fresh_deck()
        game = cls(uuid.uuid4().hex[:12], player_id, player_name, channel_id, bet,
                   deck, deck.pop())
        # Never open on an unplayable card (both directions disabled, e.g. an Ace:
        # 'higher' impossible and 'lower' too certain to profit). Burn and redraw.
        while game.mult_higher is None and game.mult_lower is None and game.deck:
            game.current = game.deck.pop()
            game._recompute()
        return game

    # --- odds ---
    def _recompute(self):
        """Set the higher/lower payout multipliers for the current card from the
        remaining deck. A direction is None (button disabled) when it's impossible
        (no cards that way) OR so likely that a win wouldn't pay a real profit - so
        an offered guess always increases your banked value."""
        import config
        factor = getattr(config, "HIGHERLOWER_PAYOUT_FACTOR", 0.95)
        min_mult = getattr(config, "HIGHERLOWER_MIN_MULTIPLIER", 1.05)
        n = len(self.deck)
        if n == 0:
            self.mult_higher = self.mult_lower = None
            return
        cv = _value(self.current)
        higher = sum(1 for c in self.deck if _value(c) > cv)
        lower = sum(1 for c in self.deck if _value(c) < cv)
        decisive = higher + lower  # ties now push, so they're excluded from the win/lose odds
        if decisive == 0:
            self.mult_higher = self.mult_lower = None
            return

        def mk(count):
            if count <= 0:
                return None
            m = round(factor * decisive / count, 2)
            return m if m >= min_mult else None  # too certain to pay a profit -> not offered

        self.mult_higher = mk(higher)
        self.mult_lower = mk(lower)

    def can_cash_out(self) -> bool:
        return self.state == "player" and self.steps >= 1

    def current_value(self) -> int:
        return int(round(self.bet * self.cumulative))

    # --- mutations ---
    def guess(self, direction: str):
        """direction: 'higher' or 'lower'. Advances the ladder, pushes on a tie, or busts."""
        mult = self.mult_higher if direction == "higher" else self.mult_lower
        nxt = self.deck.pop()
        nv, cv = _value(nxt), _value(self.current)
        if nv == cv:
            # Tie -> push: no win, no loss. Carry on from the new (same-value) card.
            self.history.append(self.current)
            self.current = nxt
            self._recompute()
            if not self.deck or (self.mult_higher is None and self.mult_lower is None):
                self.cash_out()
            return
        won = (nv > cv) if direction == "higher" else (nv < cv)
        if won and mult is not None:
            self.cumulative *= mult
            self.history.append(self.current)
            self.current = nxt
            self.steps += 1
            self._recompute()
            # No cards left, or no valid guess remains -> the ladder must be cashed.
            if not self.deck or (self.mult_higher is None and self.mult_lower is None):
                self.cash_out()
        else:
            self.history.append(self.current)
            self.current = nxt
            self.state = "over"
            self.outcome = "lose"
            self.payout = 0
            self.net = -self.bet

    def cash_out(self):
        if self.state != "player":
            return
        self.state = "over"
        self.outcome = "win"
        self.payout = self.current_value()
        self.net = self.payout - self.bet

    # --- serialisation (only in-play ladders are persisted) ---
    def to_dict(self) -> dict:
        return {
            "type": "higherlower",
            "game_id": self.game_id, "player_id": self.player_id,
            "player_name": self.player_name, "channel_id": self.channel_id,
            "message_id": self.message_id, "bet": self.bet, "deck": self.deck,
            "current": self.current, "history": self.history,
            "cumulative": self.cumulative, "steps": self.steps,
            "state": self.state, "created_ts": self.created_ts,
        }

    @classmethod
    def from_dict(cls, d: dict):
        return cls(
            d["game_id"], d["player_id"], d.get("player_name", "Player"),
            d.get("channel_id"), d["bet"], d["deck"], d["current"],
            history=d.get("history", []), cumulative=d.get("cumulative", 1.0),
            steps=d.get("steps", 0), state=d.get("state", "player"),
            message_id=d.get("message_id"), created_ts=d.get("created_ts"),
        )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def save_game(game: HigherLowerGame):
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
    if amount <= 0:
        return
    if not add_bb(uid, amount, reason=reason, taxable=False):
        logger.critical("Bank insolvent paying %s of %s to %s - minting to honour the win.",
                        reason, amount, uid)
        UKPenceManager.add_amount(uid, amount, reason=f"{reason} [bank insolvent - minted]")


def _payout(game: HigherLowerGame):
    if game.settled:
        return
    game.settled = True
    if game.outcome == "win" and game.payout > 0:
        _credit(game.player_id, game.payout, "Higher-Lower cash-out")
    record_result(game.player_id, "higherlower", game.bet, game.bet, game.payout, game.outcome)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def _card_html(code: str, size: str = "big") -> str:
    r, s = code[0], code[1]
    disp = _disp_rank(r)
    glyph = SUIT_GLYPH[s]
    red = " red" if s in RED_SUITS else ""
    return (
        f'<div class="card {size}{red}">'
        f'<span class="corner tl"><b>{disp}</b><i>{glyph}</i></span>'
        f'<span class="pip">{glyph}</span>'
        f'<span class="corner br"><b>{disp}</b><i>{glyph}</i></span>'
        f"</div>"
    )


def _banner_html(game: HigherLowerGame) -> str:
    if game.outcome == "win":
        cls, head, sub = "win", "Cashed Out", f"+{game.net:,} UKPence  ({game.cumulative:.2f}x)"
    else:
        cls, head, sub = "lose", "Busted", f"-{game.bet:,} UKPence"
    return (f'<div class="banner-wrap"><div class="banner {cls}">'
            f'<div class="head">{head}</div><div class="sub">{sub}</div></div></div>')


def build_hl_html(game: HigherLowerGame) -> str:
    template = read_html_template("templates/higher_lower.html")
    history = "".join(_card_html(c, "small") for c in game.history[-8:])
    mh = f"{game.mult_higher:.2f}x" if game.mult_higher else "-"
    ml = f"{game.mult_lower:.2f}x" if game.mult_lower else "-"

    if game.state == "over":
        banner = _banner_html(game)
        hint = "Round complete"
    else:
        banner = ""
        hint = "Higher or lower?" + ("  ·  cash out to bank it" if game.can_cash_out() else "")

    return (
        template
        .replace("{{RULE}}", "Will the next card be higher or lower?<br>Cash out any time")
        .replace("{{PLAYER_NAME}}", _html.escape(str(game.player_name)[:24]) or "Player")
        .replace("{{CURRENT_CARD}}", _card_html(game.current, "big"))
        .replace("{{HISTORY}}", history or "<span class='empty'>no streak yet</span>")
        .replace("{{MULT_HIGHER}}", mh)
        .replace("{{MULT_LOWER}}", ml)
        .replace("{{STREAK}}", str(game.steps))
        .replace("{{BET}}", f"{game.bet:,}")
        .replace("{{VALUE}}", f"{game.current_value():,}")
        .replace("{{HINT}}", hint)
        .replace("{{RESULT_BANNER}}", banner)
        .replace("{{SESSION}}", session_footer_html(
            game.player_id, session_count=getattr(game, "session_count", 1),
            session_net=getattr(game, "session_net", 0),
            current_net=(game.payout - game.bet), over=(game.outcome is not None)))
    )


async def render_hl_image(game: HigherLowerGame) -> io.BytesIO:
    from lib.core.image_processing import screenshot_html
    return await screenshot_html(build_hl_html(game), size=(900, 1500), element_selector=".table")


def _native_text(game: HigherLowerGame) -> str:
    def ct(code):
        return f"`{_disp_rank(code[0])}`{SUIT_EMOJI[code[1]]}"
    mh = f"{game.mult_higher:.2f}x" if game.mult_higher else "-"
    ml = f"{game.mult_lower:.2f}x" if game.mult_lower else "-"
    lines = [
        f"## 🎴 Higher or Lower - {game.bet:,} UKPence",
        f"**Current card:** {ct(game.current)}",
        f"⬆️ Higher pays **{mh}**   ·   ⬇️ Lower pays **{ml}**",
    ]
    if game.history:
        lines.append("-# Streak: " + " ".join(ct(c) for c in game.history[-10:]))
    if game.state == "over":
        if game.outcome == "win":
            lines.append(f"-# 💰 **Cashed out** +{game.net:,} UKPence ({game.cumulative:.2f}x)")
        else:
            lines.append(f"-# ❌ **Busted** -{game.bet:,} UKPence")
    else:
        lines.append(f"-# Streak {game.steps} · banked value {game.current_value():,} UKPence · Balance {get_bb(game.player_id):,}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Components V2 view
# ---------------------------------------------------------------------------
ACCENT = discord.Colour(0x1C6B46)


def _action_row(game: HigherLowerGame) -> discord.ui.ActionRow:
    row = discord.ui.ActionRow()
    if game.state == "player":
        up = discord.ui.Button(
            label=f"Higher  {game.mult_higher:.2f}x" if game.mult_higher else "Higher  -",
            emoji="⬆️", style=discord.ButtonStyle.success,
            custom_id=f"higherlower:{game.game_id}:higher",
            disabled=game.mult_higher is None,
        )
        up.callback = _make_cb(game, "higher")
        row.add_item(up)

        down = discord.ui.Button(
            label=f"Lower  {game.mult_lower:.2f}x" if game.mult_lower else "Lower  -",
            emoji="⬇️", style=discord.ButtonStyle.primary,
            custom_id=f"higherlower:{game.game_id}:lower",
            disabled=game.mult_lower is None,
        )
        down.callback = _make_cb(game, "lower")
        row.add_item(down)

        if game.can_cash_out():
            cash = discord.ui.Button(
                label=f"Cash Out  {game.current_value():,}", emoji="💰",
                style=discord.ButtonStyle.secondary,
                custom_id=f"higherlower:{game.game_id}:cashout",
            )
            cash.callback = _make_cb(game, "cashout")
            row.add_item(cash)
        elif game.steps == 0:
            rules = discord.ui.Button(
                label="Rules", emoji="📖", style=discord.ButtonStyle.secondary,
                custom_id=f"higherlower:{game.game_id}:rules",
            )
            rules.callback = _make_cb(game, "rules")
            row.add_item(rules)
    else:
        again = discord.ui.Button(
            label="Play Again", emoji="🔁", style=discord.ButtonStyle.primary,
            custom_id=f"higherlower:{game.game_id}:again",
        )
        again.callback = _make_cb(game, "again")
        row.add_item(again)

        change = discord.ui.Button(
            label="Change Bet", emoji="✏️", style=discord.ButtonStyle.secondary,
            custom_id=f"higherlower:{game.game_id}:changebet",
        )
        change.callback = _make_cb(game, "changebet")
        row.add_item(change)
    return row


async def build_hl_layout(game: HigherLowerGame, client):
    import config
    files = []
    view = discord.ui.LayoutView(timeout=None)
    used_image = False
    if getattr(config, "HIGHERLOWER_IMAGE_ENABLED", True):
        try:
            img = await render_hl_image(game)
            files = [discord.File(img, filename="higherlower.png")]
            gallery = discord.ui.MediaGallery()
            gallery.add_item(media="attachment://higherlower.png")
            view.add_item(gallery)
            used_image = True
        except Exception:
            logger.warning("Higher-Lower image render failed; using native layout.", exc_info=True)
    if not used_image:
        container = discord.ui.Container(accent_colour=ACCENT)
        container.add_item(discord.ui.TextDisplay(_native_text(game)))
        view.add_item(container)
    view.add_item(_action_row(game))
    return view, files


def build_control_view(game: HigherLowerGame) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(_action_row(game))
    return view


# ---------------------------------------------------------------------------
# Interaction handling
# ---------------------------------------------------------------------------
def _make_cb(game: HigherLowerGame, action: str):
    async def _cb(interaction: Interaction):
        with action_in_flight():
            await _handle_action(interaction, game, action)
    return _cb


async def _show_rules(interaction: Interaction):
    import config
    mn = getattr(config, "HIGHERLOWER_MIN_BET", 5)
    mx = getattr(config, "HIGHERLOWER_MAX_BET", 250_000)
    rules = (
        "## 🎴 Higher or Lower - House Rules\n"
        "A card is shown. Guess whether the **next** card is higher or lower (Aces are high).\n\n"
        "- Each button shows the **multiplier** it pays if you're right - the longer odds, "
        "the bigger the multiplier.\n"
        "- A correct guess multiplies your banked value and deals the next card; keep going or "
        "**Cash Out** to collect.\n"
        "- A **wrong guess** loses the whole stake. A **tie** (same value) is a **push** - no "
        "win, no loss - and the run carries on from the new card.\n"
        "- The multiplier is shaved slightly below true odds, so each step carries a small house "
        "edge - the further you climb, the more the house edges in. Cash out to lock it.\n"
        f"- **Bets:** {mn:,} - {mx:,} UKPence. Stakes go to the house bank; cash-outs are paid from it.\n\n"
        "-# Good luck. 🇬🇧"
    )
    await interaction.response.send_message(rules, ephemeral=True)


async def _refresh(interaction: Interaction, game: HigherLowerGame, client):
    view, files = await build_hl_layout(game, client)
    await interaction.edit_original_response(view=view, attachments=files)
    try:
        client.add_view(view, message_id=game.message_id)
    except Exception:
        logger.debug("add_view after refresh failed (non-fatal)", exc_info=True)


async def _handle_action(interaction: Interaction, game: HigherLowerGame, action: str):
    if action == "rules":
        await _show_rules(interaction)
        return

    if interaction.user.id != game.player_id:
        await interaction.response.send_message(
            "This isn't your game - start your own with `/higherlower`.", ephemeral=True
        )
        return

    # Change Bet opens a modal (must be the immediate response, before defer/busy).
    if action == "changebet":
        await interaction.response.send_modal(ChangeBetModal(game))
        return

    if game.busy:
        try:
            await interaction.response.defer()
        except discord.NotFound:
            pass  # duplicate click whose interaction already expired - ignore
        return
    game.busy = True
    client = interaction.client
    try:
        async with game.lock:
            if action == "again":
                await _handle_again(interaction, game, client)
                return

            if game.state != "player":
                await interaction.response.defer()
                return

            # Guard against a disabled-direction click sneaking through a stale view.
            if action in ("higher", "lower"):
                mult = game.mult_higher if action == "higher" else game.mult_lower
                if mult is None:
                    await interaction.response.send_message(
                        "That guess isn't possible on this card.", ephemeral=True
                    )
                    return

            try:
                await interaction.response.defer()
            except discord.NotFound:
                return  # interaction expired before we could ack it (loop was busy)

            if action in ("higher", "lower"):
                game.guess(action)
            elif action == "cashout":
                game.cash_out()

            if game.state == "over":
                _payout(game)
                delete_game(game.message_id)
            else:
                save_game(game)

            try:
                from lib.economy.game_badges import award_higherlower_badges
                await award_higherlower_badges(client, game)
            except Exception:
                logger.error("higher/lower badge hook failed", exc_info=True)

            try:
                await _refresh(interaction, game, client)
            except Exception:
                logger.error("Higher-Lower redraw failed after applying the move.", exc_info=True)
    finally:
        game.busy = False


async def _handle_again(interaction: Interaction, old_game: HigherLowerGame, client):
    """Play Again: a fresh ladder on the same message at the previous stake."""
    await _start_replay(interaction, old_game, client, old_game.bet, via_modal=False)


class ChangeBetModal(discord.ui.Modal, title="Higher or Lower - change your bet"):
    def __init__(self, game: HigherLowerGame):
        super().__init__()
        self.game = game
        self.amount = discord.ui.TextInput(
            label="New bet (UKPence)", placeholder=f"{game.bet:,}", required=True, max_length=12,
        )
        self.add_item(self.amount)

    async def on_submit(self, interaction: Interaction):
        raw = str(self.amount.value).replace(",", "").strip()
        try:
            amount = int(raw)
        except ValueError:
            await interaction.response.send_message(
                "Please enter a whole number of UKPence.", ephemeral=True
            )
            return
        await _start_replay(interaction, self.game, interaction.client, amount, via_modal=True)


@deal_in_flight
async def _start_replay(interaction: Interaction, old_game: HigherLowerGame, client,
                        bet: int, *, via_modal: bool):
    """Deal a fresh ladder on the same message at `bet`. Drives Play Again (button, same
    stake) and Change Bet (modal, new stake)."""
    import config
    if old_game.replayed:
        if via_modal:
            await interaction.response.send_message("This game has already been replayed.", ephemeral=True)
        else:
            await interaction.response.defer()
        return
    uid = old_game.player_id
    if getattr(interaction.client, "maintenance_mode", False):
        await interaction.response.send_message(
            "🔧 **Under maintenance** - the bot is restarting. Hold on a minute.", ephemeral=True
        )
        return
    if not getattr(config, "HIGHERLOWER_ENABLED", True):
        await interaction.response.send_message("Higher or Lower is currently closed.", ephemeral=True)
        return
    mn = getattr(config, "HIGHERLOWER_MIN_BET", 5)
    mx = getattr(config, "HIGHERLOWER_MAX_BET", 10_000)
    if bet < mn or bet > mx:
        await interaction.response.send_message(
            f"Bets must be between {mn:,} and {mx:,} UKPence.", ephemeral=True
        )
        return
    if get_bb(uid) < bet:
        await interaction.response.send_message(f"You need {bet:,} UKPence for that bet.", ephemeral=True)
        return
    if not remove_bb(uid, bet, reason="Higher-Lower bet"):
        await interaction.response.send_message("You don't have enough UKPence.", ephemeral=True)
        return
    old_game.replayed = True
    await interaction.response.defer()

    new_game = HigherLowerGame.new(uid, old_game.player_name, old_game.channel_id, bet)
    new_game.message_id = old_game.message_id
    new_game.session_count = getattr(old_game, "session_count", 1) + 1
    new_game.session_net = getattr(old_game, "session_net", 0) + (old_game.payout - old_game.bet)
    # Refundable section ends the moment the new ladder is shown.
    try:
        view, files = await build_hl_layout(new_game, client)
        if via_modal:
            await interaction.message.edit(view=view, attachments=files)
        else:
            await interaction.edit_original_response(view=view, attachments=files)
    except Exception:
        logger.error("Higher-Lower replay failed; refunding stake.", exc_info=True)
        _credit(uid, bet, "Higher-Lower stake refund (replay failed)")
        return

    # New ladder is live - persistence/add_view failures are logged, never refunded.
    try:
        save_game(new_game)
        client.add_view(view, message_id=new_game.message_id)
    except Exception:
        logger.error("Higher-Lower replay post-update issue (game is live).", exc_info=True)


# ---------------------------------------------------------------------------
# Slash command entry point
# ---------------------------------------------------------------------------
@deal_in_flight
async def handle_higherlower_command(interaction: Interaction, amount: int):
    import config

    if getattr(interaction.client, "maintenance_mode", False):
        await interaction.response.send_message(
            "🔧 **Under maintenance** - the bot is restarting for an update. "
            "Hold on a minute before starting a new game.", ephemeral=True
        )
        return
    if not getattr(config, "HIGHERLOWER_ENABLED", True):
        await interaction.response.send_message("Higher or Lower is currently closed.", ephemeral=True)
        return

    mn = getattr(config, "HIGHERLOWER_MIN_BET", 5)
    mx = getattr(config, "HIGHERLOWER_MAX_BET", 250_000)
    if amount < mn:
        await interaction.response.send_message(f"The minimum bet is {mn:,} UKPence.", ephemeral=True)
        return
    if amount > mx:
        await interaction.response.send_message(f"The maximum bet is {mx:,} UKPence.", ephemeral=True)
        return

    balance = get_bb(interaction.user.id)
    if balance < amount:
        await interaction.response.send_message(
            f"You don't have enough UKPence. Your balance is {balance:,}.", ephemeral=True
        )
        return

    if not remove_bb(interaction.user.id, amount, reason="Higher-Lower bet"):
        await interaction.response.send_message(
            f"You don't have enough UKPence. Your balance is {get_bb(interaction.user.id):,}.",
            ephemeral=True,
        )
        return

    name = discord.utils.escape_markdown(interaction.user.display_name)
    game = None
    try:
        await interaction.response.defer(thinking=True)
        game = HigherLowerGame.new(interaction.user.id, name, interaction.channel_id, amount)
        view, files = await build_hl_layout(game, interaction.client)
        msg = await interaction.followup.send(view=view, files=files)
    except Exception:
        logger.error("Higher-Lower deal failed; refunding stake.", exc_info=True)
        _credit(interaction.user.id, amount, "Higher-Lower stake refund (deal failed)")
        try:
            await interaction.followup.send(
                "Something went wrong starting your game - your stake has been refunded.",
                ephemeral=True,
            )
        except Exception:
            pass
        return

    game.message_id = msg.id
    try:
        save_game(game)
        interaction.client.add_view(view, message_id=msg.id)
    except Exception:
        logger.error("Higher-Lower post-send persistence issue (game is live).", exc_info=True)


# ---------------------------------------------------------------------------
# Restart recovery
# ---------------------------------------------------------------------------
def reattach_hl_view(client, key, value):
    try:
        game = HigherLowerGame.from_dict(value)
    except Exception as e:
        logger.error(f"Pruning malformed higher-lower entry {key}: {e}", exc_info=True)
        delete_game(key)
        return
    if game.state != "player":
        delete_game(key)
        return
    try:
        game.message_id = int(key)
        client.add_view(build_control_view(game), message_id=int(key))
    except Exception as e:
        logger.error(f"Failed to reattach higher-lower view {key}: {e}", exc_info=True)
