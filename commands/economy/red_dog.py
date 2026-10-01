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
from lib.economy import casino_felt as felt
from lib.economy.casino_stats import record_result
from lib.economy.casino_drain import action_in_flight, deal_in_flight
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
def _stage_html(game: RedDogGame) -> str:
    """The two board cards with the third between them (face down until it's dealt)."""
    if game.is_consecutive:
        cards = felt.card(game.first_card, "md") + felt.card(game.second_card, "md")
        left = felt.stat("Spread", "0", narrow=True)
        right = felt.stat("Pays", "Push", right=True, narrow=True)
    else:
        hit = game.outcome in ("win", "trips")
        cards = (felt.card(game.first_card, "md") + felt.card(game.third_card, "md", lit=hit)
                 + felt.card(game.second_card, "md"))
        if game.is_pair:
            left = felt.stat("Pair", felt.rank_name(game.first_card), narrow=True)
            right = felt.stat("Trips", f"{PAIR_TRIPS_ODDS}:1", right=True, narrow=True)
        else:
            left = felt.stat("Spread", str(game.spread), narrow=True)
            right = felt.stat("Pays", f"{game.odds}:1", right=True, narrow=True)
    return felt.stage(f'<div class="row">{cards}</div>', left, right)


def _rail_html(game: RedDogGame) -> str:
    ledger = felt.player_ledger(
        game.player_id, bet=game.total_staked, session_count=getattr(game, "session_count", 1),
        session_net=getattr(game, "session_net", 0), current_net=getattr(game, "net", 0),
        over=(game.state == "over"))
    if game.state == "raise_decision":
        return felt.rail(f"Spread of {game.spread}", f"Pays {game.odds}:1 if the next card lands between",
                         actions="Raise · Call", ledger=ledger)
    o, net = game.outcome, game.net
    if o == "trips":
        return felt.rail("Three of a kind!", f"Trips pay {PAIR_TRIPS_ODDS}:1", money=felt.signed(net),
                         tone="gold", head_tone="gold", ledger=ledger)
    if o == "win":
        return felt.rail("In between!", f"The {felt.rank_name(game.third_card)} landed inside",
                         money=felt.signed(net), tone="win", ledger=ledger)
    if o == "push":
        sub = "Consecutive cards · ante back" if game.is_consecutive else "Pair without trips · ante back"
        return felt.rail("Push", sub, money="0", tone="push", ledger=ledger)
    return felt.rail("Outside", f"The {felt.rank_name(game.third_card)} missed the spread",
                     money=felt.signed(net), tone="lose", ledger=ledger)


def build_html(game: RedDogGame) -> str:
    return felt.build_page("Red Dog", f"Hand {getattr(game, 'session_count', 1)}",
                           _stage_html(game) + felt.arc(), _rail_html(game))


async def _render(game: RedDogGame):
    return await felt.render(build_html(game))


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
        with action_in_flight():
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

            # The persisted entry is the source of truth across reconnects and view
            # reattachment, so reconcile the stake from it before settling in case the
            # in-memory hand drifted (e.g. the view was rebuilt mid-round).
            try:
                _stored = cb.load_persistent_views().get(str(game.message_id))
                if _stored and _stored.get("type") == KEY:
                    _b = int(_stored.get("bet", game.bet))
                    if _b != game.bet:
                        game.bet = _b
                        game.total_staked = int(_stored.get("total_staked", _b))
            except Exception:
                logger.error("Red Dog state reconcile failed.", exc_info=True)

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


@deal_in_flight
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
@deal_in_flight
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
