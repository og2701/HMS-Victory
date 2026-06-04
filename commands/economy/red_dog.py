"""HMS Victory - Red Dog (a.k.a. In-Between / Acey-Deucey, vs-the-house).

Place a bet and two cards are dealt face up. Consecutive ranks push, a pair deals a
third card for a shot at three-of-a-kind (11:1), and otherwise a spread opens up: you
may Raise (double your stake) or Call before the third card is dealt. If that card
lands strictly between the two you win at the spread odds; otherwise you lose the lot.

Built on commands/economy/casino_base (shared card model, renderer, layout, economy,
persistence). Lifecycle mirrors the other table games: an HTML->PNG felt table in a Components V2
view, a native fallback, persistence of the in-flight raise decision, a busy-guard, a
Rules button, and Play Again / Change Bet on the result.
"""

import asyncio
import logging
import uuid

import discord
from discord import Interaction

from lib.economy.economy_manager import get_bb, remove_bb
from lib.economy.casino_stats import record_result, session_footer_html
import commands.economy.casino_base as cb

logger = logging.getLogger(__name__)

KEY = "reddog"
BANK = "Red Dog"   # reason keyword routed to the bank's Red Dog P/L columns

# Spread (high - low - 1) -> winning odds (X:1). Spread 4+ all pay 1:1.
SPREAD_ODDS = {1: 5, 2: 4, 3: 2, 4: 1}
PAIR_TRIPS_ODDS = 11   # a pair that hits three-of-a-kind pays 11:1


def _odds_for_spread(spread: int) -> int:
    return SPREAD_ODDS.get(spread, 1)


class RedDogGame:
    def __init__(self, game_id, player_id, player_name, channel_id, bet, deck,
                 first_card, second_card, *, third_card=None, total_staked=None,
                 state="over", message_id=None):
        self.game_id = game_id
        self.player_id = int(player_id)
        self.player_name = player_name
        self.channel_id = channel_id
        self.bet = int(bet)
        self.deck = deck
        self.first_card = first_card
        self.second_card = second_card
        self.third_card = third_card
        self.total_staked = int(total_staked if total_staked is not None else bet)
        self.state = state                 # "raise_decision" | "over"
        self.message_id = message_id
        # transient
        self.settled = False
        self.replayed = False
        self.busy = False
        self.outcome = None                # push | trips | win | lose
        self.payout = 0
        self.net = 0
        self.lock = asyncio.Lock()

    # --- board helpers (ordered low/high by value) ---
    @property
    def low_value(self) -> int:
        return min(cb.value(self.first_card), cb.value(self.second_card))

    @property
    def high_value(self) -> int:
        return max(cb.value(self.first_card), cb.value(self.second_card))

    @property
    def is_consecutive(self) -> bool:
        return (self.high_value - self.low_value) == 1

    @property
    def is_pair(self) -> bool:
        return self.high_value == self.low_value

    @property
    def spread(self) -> int:
        return self.high_value - self.low_value - 1

    @property
    def odds(self) -> int:
        return _odds_for_spread(self.spread)

    @classmethod
    def new(cls, player_id, player_name, channel_id, bet):
        deck = cb.fresh_deck()
        a, b = deck.pop(), deck.pop()
        game = cls(uuid.uuid4().hex[:12], player_id, player_name, channel_id, bet, deck, a, b)
        if game.is_consecutive:
            # Immediate push: ante returned, no third card, no decision.
            game.state = "over"
        elif game.is_pair:
            # Auto-deal the third card for the three-of-a-kind shot.
            game.third_card = deck.pop()
            game.state = "over"
        else:
            game.state = "raise_decision"   # a spread exists - await Raise / Call
        return game

    def can_afford_raise(self) -> bool:
        return get_bb(self.player_id) >= self.bet

    # --- resolution ---
    def raise_bet(self):
        """Caller must already have debited the extra ante. Deal the third card."""
        self.total_staked += self.bet
        self.third_card = self.deck.pop()
        self.state = "over"

    def call_bet(self):
        """Keep the single ante; deal the third card."""
        self.third_card = self.deck.pop()
        self.state = "over"

    # --- serialisation (only the in-flight raise decision is persisted) ---
    def to_dict(self) -> dict:
        return {
            "type": KEY, "game_id": self.game_id, "player_id": self.player_id,
            "player_name": self.player_name, "channel_id": self.channel_id,
            "message_id": self.message_id, "bet": self.bet, "deck": self.deck,
            "first_card": self.first_card, "second_card": self.second_card,
            "third_card": self.third_card, "total_staked": self.total_staked,
            "state": self.state,
        }

    @classmethod
    def from_dict(cls, d):
        return cls(
            d["game_id"], d["player_id"], d.get("player_name", "Player"), d.get("channel_id"),
            d["bet"], d["deck"], d["first_card"], d["second_card"],
            third_card=d.get("third_card"),
            total_staked=d.get("total_staked", d["bet"]), state=d.get("state", "over"),
            message_id=d.get("message_id"),
        )


# ---------------------------------------------------------------------------
# Outcome decision + payout (decide for display, pay after the message is shown)
# ---------------------------------------------------------------------------
def _decide_initial(game: RedDogGame):
    """Decide a push (consecutive) or a pair's three-of-a-kind shot, dealt at new()."""
    if game.outcome is not None:
        return
    if game.is_consecutive:
        game.outcome, game.payout = "push", game.bet      # ante returned, net 0
    elif game.is_pair:
        if game.third_card is not None and cb.value(game.third_card) == game.low_value:
            game.outcome = "trips"                         # three of a kind: 11:1
            game.payout = (1 + PAIR_TRIPS_ODDS) * game.bet
        else:
            game.outcome, game.payout = "push", game.bet   # ante returned, net 0
    game.net = game.payout - game.total_staked


def _decide_spread(game: RedDogGame):
    """Decide the third card after a Raise / Call on a spread."""
    if game.outcome is not None:
        return
    v = cb.value(game.third_card)
    if game.low_value < v < game.high_value:
        odds = game.odds
        game.outcome = "win"
        game.payout = game.total_staked * (1 + odds)
    else:
        game.outcome, game.payout = "lose", 0
    game.net = game.payout - game.total_staked


def _pay(game: RedDogGame):
    if game.settled:
        return
    game.settled = True
    if game.payout > 0:
        reason = {"win": f"{BANK} win", "trips": f"{BANK} three of a kind win",
                  "push": f"{BANK} push (ante returned)"}.get(game.outcome, f"{BANK} payout")
        cb.credit_from_bank(game.player_id, game.payout, reason)
    record_result(game.player_id, KEY, game.bet, game.total_staked, game.payout, game.outcome)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
SUBTITLE = "Will the third card fall in between?"


def _result_banner(game: RedDogGame) -> str:
    o = game.outcome
    if o == "trips":
        return cb.banner_html("gold", "Three of a Kind!", f"+{game.net:,} UKPence")
    if o == "win":
        return cb.banner_html("win", "In Between!", f"+{game.net:,} UKPence")
    if o == "push":
        return cb.banner_html("push", "Push", "Bet returned")
    return cb.banner_html("lose", "Outside", f"-{abs(game.net):,} UKPence")


def _rail(game: RedDogGame) -> str:
    """The centre rail: shows the spread + payout odds, or the slot for the 3rd card."""
    if game.state == "raise_decision":
        odds = game.odds
        return (
            '<div class="rail"><span class="ln"></span>'
            f'<span class="pays">Spread {game.spread} &middot; pays {odds}:1</span>'
            '<span class="ln"></span></div>'
        )
    if game.outcome == "trips":
        return (
            '<div class="rail"><span class="ln"></span>'
            '<span class="pays">Trips pay 11:1</span>'
            '<span class="ln"></span></div>'
        )
    if game.outcome == "push" and game.third_card is None:
        return (
            '<div class="rail"><span class="ln"></span>'
            '<span class="pays">Consecutive &middot; push</span>'
            '<span class="ln"></span></div>'
        )
    odds = game.odds
    return (
        '<div class="rail"><span class="ln"></span>'
        f'<span class="pays">Spread {game.spread} &middot; pays {odds}:1</span>'
        '<span class="ln"></span></div>'
    )


def _body(game: RedDogGame) -> str:
    """Board zone: the two outline cards with the third in the middle when revealed.

    The third slot shows face-down (None) while the round is undecided and the spread
    is still live; it reveals once the card has been dealt."""
    over = game.state == "over"
    # Show a middle slot whenever a third card is in play (pair/trips or a resolved
    # spread). For a consecutive push there is no third card at all.
    has_middle = game.third_card is not None or (over and not game.is_consecutive and not game.is_pair)
    middle = game.third_card if game.third_card is not None else None
    if has_middle:
        board = [game.first_card, middle, game.second_card]
    else:
        board = [game.first_card, game.second_card]
    zone = cb.zone_html("Board", cb.hand_html(board, size="big"))
    rail = _rail(game)
    stake = (
        '<div class="rail"><span class="ln"></span>'
        f'<span class="pays">{game.player_name} staked {game.total_staked:,}</span>'
        '<span class="ln"></span></div>'
    )
    return zone + rail + stake


async def _render(game: RedDogGame):
    return await cb.render_table(
        title_main="Red ", title_accent="Dog", subtitle=SUBTITLE,
        body_html=_body(game), bet=game.total_staked, balance=get_bb(game.player_id),
        hint=("Raise or call?" if game.state == "raise_decision" else "Round complete"),
        result_banner=("" if game.state == "raise_decision" else _result_banner(game)),
        session_html=session_footer_html(
            game.player_id, session_count=getattr(game, "session_count", 1),
            session_net=getattr(game, "session_net", 0), current_net=getattr(game, "net", 0),
            over=(game.state == "over")),
    )


def _native(game: RedDogGame) -> str:
    if game.state == "raise_decision":
        board = cb.card_text(game.first_card) + "  " + cb.card_text(game.second_card)
        lines = ["## 🐕 Red Dog", f"**Board:** {board}",
                 f"-# Spread **{game.spread}** pays **{game.odds}:1** - **Raise** or **Call**?"]
        return "\n".join(lines)
    show_third = game.third_card is not None
    if show_third:
        board = (cb.card_text(game.first_card) + "  " + cb.card_text(game.third_card)
                 + "  " + cb.card_text(game.second_card))
    else:
        board = cb.card_text(game.first_card) + "  " + cb.card_text(game.second_card)
    lines = ["## 🐕 Red Dog", f"**Board:** {board}"]
    tag = {"trips": f"🏆 Three of a kind! +{game.net:,}",
           "win": f"✅ In between! +{game.net:,}",
           "push": "↩️ Push - bet returned",
           "lose": f"❌ Outside -{abs(game.net):,}"}.get(game.outcome, "")
    lines.append(f"-# {tag} UKPence  ·  Balance {get_bb(game.player_id):,}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# View
# ---------------------------------------------------------------------------
def _action_row(game: RedDogGame) -> discord.ui.ActionRow:
    row = discord.ui.ActionRow()
    if game.state == "raise_decision":
        rz = discord.ui.Button(label="Raise", emoji="⏫", style=discord.ButtonStyle.success,
                               custom_id=f"{KEY}:{game.game_id}:raise")
        rz.callback = _make_cb(game, "raise")
        row.add_item(rz)
        call = discord.ui.Button(label="Call", emoji="✅", style=discord.ButtonStyle.primary,
                                 custom_id=f"{KEY}:{game.game_id}:call")
        call.callback = _make_cb(game, "call")
        row.add_item(call)
    else:
        again = discord.ui.Button(label="Play Again", emoji="🔁", style=discord.ButtonStyle.primary,
                                  custom_id=f"{KEY}:{game.game_id}:again")
        again.callback = _make_cb(game, "again")
        row.add_item(again)
        change = discord.ui.Button(label="Change Bet", emoji="✏️", style=discord.ButtonStyle.secondary,
                                   custom_id=f"{KEY}:{game.game_id}:changebet")
        change.callback = _make_cb(game, "changebet")
        row.add_item(change)
    rules = discord.ui.Button(label="Rules", emoji="📖", style=discord.ButtonStyle.secondary,
                              custom_id=f"{KEY}:{game.game_id}:rules")
    rules.callback = _make_cb(game, "rules")
    row.add_item(rules)
    return row


async def build_reddog_layout(game: RedDogGame, client):
    import config
    image = None
    if getattr(config, "REDDOG_IMAGE_ENABLED", True):
        try:
            image = await _render(game)
        except Exception:
            logger.warning("Red Dog render failed; using native layout.", exc_info=True)
    return cb.build_layout(image, "reddog.png", _action_row(game), native_text=_native(game))


def build_control_view(game: RedDogGame) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(_action_row(game))
    return view


# ---------------------------------------------------------------------------
# Interaction handling
# ---------------------------------------------------------------------------
def _make_cb(game: RedDogGame, action: str):
    async def _cb(interaction: Interaction):
        await _handle_action(interaction, game, action)
    return _cb


async def _show_rules(interaction: Interaction):
    import config
    mn = getattr(config, "REDDOG_MIN_BET", 5)
    mx = getattr(config, "REDDOG_MAX_BET", 10_000)
    rules = (
        "## 🐕 Red Dog - House Rules\n"
        "Place your bet and two cards are dealt face up. **Aces are high.**\n\n"
        "- **Consecutive ranks** (e.g. 7 & 8, or K & A) **push** - your bet is returned.\n"
        "- **A pair** deals a third card automatically: match it for **three of a kind** "
        "(**11:1**); otherwise it's a **push**.\n"
        "- **Otherwise a spread opens up** (the gap between the cards). You may **Raise** to "
        "double your bet, or **Call** to keep it as is. Then the third card is dealt:\n"
        "  - lands **strictly between** the two cards -> you **win** at the spread odds on your "
        "total stake;\n  - lands on or outside them -> you **lose** the lot.\n"
        "- **Spread payouts:** 1 -> **5:1**, 2 -> **4:1**, 3 -> **2:1**, 4 or more -> **1:1**. "
        "The wider the gap, the likelier the hit - so the smaller the payout.\n"
        "- **Strategy:** that's the whole decision - **Raise** on a **wide** spread (you're very "
        "likely to win), but only **Call** on a **narrow** one, where the long odds rarely pay off.\n"
        f"- **Bets:** {mn:,} - {mx:,} UKPence. Stakes go to the house bank; wins are paid from it.\n\n"
        "-# Good luck. 🇬🇧"
    )
    await interaction.response.send_message(rules, ephemeral=True)


async def _refresh(interaction: Interaction, game: RedDogGame, client, *, via_modal=False):
    view, files = await build_reddog_layout(game, client)
    if via_modal:
        await interaction.message.edit(view=view, attachments=files)
    else:
        await interaction.edit_original_response(view=view, attachments=files)
    try:
        client.add_view(view, message_id=game.message_id)
    except Exception:
        logger.debug("reddog add_view after refresh failed (non-fatal)", exc_info=True)


async def _handle_action(interaction: Interaction, game: RedDogGame, action: str):
    if action == "rules":
        await _show_rules(interaction)
        return
    if interaction.user.id != game.player_id:
        await interaction.response.send_message(
            "This isn't your table - deal your own with `/reddog`.", ephemeral=True
        )
        return
    if action == "changebet":
        await interaction.response.send_modal(ChangeBetModal(game))
        return

    if game.busy:
        await interaction.response.defer()
        return
    game.busy = True
    client = interaction.client
    try:
        async with game.lock:
            if action == "again":
                await _start_replay(interaction, game, client, game.bet, via_modal=False)
                return

            if game.state != "raise_decision":
                await interaction.response.defer()   # stale click on a finished round
                return

            if action == "raise" and not game.can_afford_raise():
                await interaction.response.send_message(
                    f"You need {game.bet:,} more UKPence to raise. Call instead to keep your bet as is.",
                    ephemeral=True,
                )
                return

            await interaction.response.defer()

            if action == "raise":
                if not remove_bb(game.player_id, game.bet, reason=f"{BANK} bet"):
                    await interaction.followup.send("You don't have enough UKPence to raise.", ephemeral=True)
                    return
                game.raise_bet()
            elif action == "call":
                game.call_bet()
            else:
                return

            _decide_spread(game)
            cb.delete_state(game.message_id)   # round is over now
            try:
                await _refresh(interaction, game, client)
            except Exception:
                logger.error("Red Dog redraw failed after the decision.", exc_info=True)
            _pay(game)                          # credit once the result is on screen
    finally:
        game.busy = False


class ChangeBetModal(discord.ui.Modal, title="Red Dog - change your bet"):
    def __init__(self, game: RedDogGame):
        super().__init__()
        self.game = game
        self.amount = discord.ui.TextInput(label="New bet (UKPence)", placeholder=f"{game.bet:,}",
                                           required=True, max_length=12)
        self.add_item(self.amount)

    async def on_submit(self, interaction: Interaction):
        raw = str(self.amount.value).replace(",", "").strip()
        try:
            amount = int(raw)
        except ValueError:
            await interaction.response.send_message("Please enter a whole number of UKPence.", ephemeral=True)
            return
        await _start_replay(interaction, self.game, interaction.client, amount, via_modal=True)


async def _start_replay(interaction: Interaction, old_game: RedDogGame, client, bet: int, *, via_modal: bool):
    import config
    if old_game.replayed:
        if via_modal:
            await interaction.response.send_message("This round has already been replayed.", ephemeral=True)
        else:
            await interaction.response.defer()
        return
    uid = old_game.player_id
    if getattr(interaction.client, "maintenance_mode", False):
        await interaction.response.send_message("🔧 **Under maintenance** - hold on a minute.", ephemeral=True)
        return
    if not getattr(config, "REDDOG_ENABLED", True):
        await interaction.response.send_message("Red Dog is currently closed.", ephemeral=True)
        return
    mn = getattr(config, "REDDOG_MIN_BET", 5)
    mx = getattr(config, "REDDOG_MAX_BET", 10_000)
    if bet < mn or bet > mx:
        await interaction.response.send_message(f"Bets must be between {mn:,} and {mx:,} UKPence.", ephemeral=True)
        return
    if get_bb(uid) < bet:
        await interaction.response.send_message(f"You need {bet:,} UKPence for that bet.", ephemeral=True)
        return
    if not remove_bb(uid, bet, reason=f"{BANK} bet"):
        await interaction.response.send_message("You don't have enough UKPence.", ephemeral=True)
        return
    old_game.replayed = True
    await interaction.response.defer()

    new_game = RedDogGame.new(uid, old_game.player_name, old_game.channel_id, bet)
    new_game.message_id = old_game.message_id
    new_game.session_count = getattr(old_game, "session_count", 1) + 1
    new_game.session_net = getattr(old_game, "session_net", 0) + old_game.net
    if new_game.state == "over":
        _decide_initial(new_game)
    try:
        await _refresh(interaction, new_game, client, via_modal=via_modal)
    except Exception:
        logger.error("Red Dog replay failed; refunding stake.", exc_info=True)
        cb.credit_from_bank(uid, bet, f"{BANK} stake refund (replay failed)")
        return
    if new_game.state == "raise_decision":
        cb.save_state(new_game.message_id, new_game.to_dict())
    else:
        _pay(new_game)


# ---------------------------------------------------------------------------
# Slash command entry point
# ---------------------------------------------------------------------------
async def handle_reddog_command(interaction: Interaction, amount: int):
    import config
    if await cb.reject_if_maintenance(interaction):
        return
    if not getattr(config, "REDDOG_ENABLED", True):
        await interaction.response.send_message("Red Dog is currently closed.", ephemeral=True)
        return
    mn = getattr(config, "REDDOG_MIN_BET", 5)
    mx = getattr(config, "REDDOG_MAX_BET", 10_000)
    if amount < mn:
        await interaction.response.send_message(f"The minimum bet is {mn:,} UKPence.", ephemeral=True)
        return
    if amount > mx:
        await interaction.response.send_message(f"The maximum bet is {mx:,} UKPence.", ephemeral=True)
        return
    if get_bb(interaction.user.id) < amount:
        await interaction.response.send_message(
            f"You don't have enough UKPence. Your balance is {get_bb(interaction.user.id):,}.", ephemeral=True
        )
        return
    if not remove_bb(interaction.user.id, amount, reason=f"{BANK} bet"):
        await interaction.response.send_message("You don't have enough UKPence.", ephemeral=True)
        return

    name = discord.utils.escape_markdown(interaction.user.display_name)
    game = None
    try:
        await interaction.response.defer(thinking=True)
        game = RedDogGame.new(interaction.user.id, name, interaction.channel_id, amount)
        if game.state == "over":
            _decide_initial(game)
        view, files = await build_reddog_layout(game, interaction.client)
        msg = await interaction.followup.send(view=view, files=files)
    except Exception:
        logger.error("Red Dog deal failed; refunding stake.", exc_info=True)
        cb.credit_from_bank(interaction.user.id, amount, f"{BANK} stake refund (deal failed)")
        try:
            await interaction.followup.send("Something went wrong dealing - your stake was refunded.", ephemeral=True)
        except Exception:
            pass
        return

    game.message_id = msg.id
    try:
        if game.state == "raise_decision":
            cb.save_state(game.message_id, game.to_dict())
        else:
            _pay(game)
        interaction.client.add_view(view, message_id=msg.id)
    except Exception:
        logger.error("Red Dog post-send issue (round is live).", exc_info=True)


# ---------------------------------------------------------------------------
# Restart recovery
# ---------------------------------------------------------------------------
def reattach_reddog_view(client, key, value):
    try:
        game = RedDogGame.from_dict(value)
    except Exception as e:
        logger.error(f"Pruning malformed red-dog entry {key}: {e}", exc_info=True)
        cb.delete_state(key)
        return
    if game.state != "raise_decision":
        cb.delete_state(key)
        return
    try:
        game.message_id = int(key)
        client.add_view(build_control_view(game), message_id=int(key))
    except Exception as e:
        logger.error(f"Failed to reattach red-dog view {key}: {e}", exc_info=True)
