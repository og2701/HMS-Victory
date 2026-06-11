"""HMS Victory - Three Card Poker (ante-play, vs-the-house).

Ante up, get three cards face up against the dealer's three face down. Then decide:
**Play** (match your ante as a Play bet) or **Fold** (forfeit the ante). On Play the dealer
reveals - the dealer only **qualifies** with Queen-high or better. Beat a qualifying dealer
and both bets pay even money; the dealer not qualifying pays the ante and pushes the Play.
A strong hand also earns an **Ante Bonus** (straight, trips or straight flush) regardless of
the dealer.

Built on commands/economy/casino_base (shared card model, 3-card evaluation, renderer,
layout, economy, persistence). Lifecycle mirrors the other casino games: an HTML->PNG felt
table in a Components V2 view, a native fallback, persistence of the in-flight play/fold
decision, a busy-guard, a Rules button, and Play Again / Change Bet on the result.
"""

import asyncio
import logging
import uuid

import discord
from discord import Interaction

from lib.economy.economy_manager import get_bb, remove_bb
from lib.economy.casino_stats import record_result, session_footer_html
from lib.economy.casino_drain import action_in_flight, deal_in_flight
import commands.economy.casino_base as cb

logger = logging.getLogger(__name__)

KEY = "tcp"
BANK = "Three Card Poker"   # reason keyword routed to the bank's TCP P/L columns

# Ante bonus paydom (paid on the ante regardless of the dealer), in units of the ante B.
#   straight -> +1, three of a kind -> +4, straight flush -> +5, otherwise +0.
_ANTE_BONUS = {5: 5, 4: 4, 3: 1}   # keyed by three_card_rank category


class TcpGame:
    def __init__(self, game_id, player_id, player_name, channel_id, bet, deck,
                 player_cards, dealer_cards, *, total_staked=None, state="over",
                 message_id=None):
        self.game_id = game_id
        self.player_id = int(player_id)
        self.player_name = player_name
        self.channel_id = channel_id
        self.bet = int(bet)
        self.deck = deck
        self.player_cards = list(player_cards)
        self.dealer_cards = list(dealer_cards)
        self.total_staked = int(total_staked if total_staked is not None else bet)
        self.state = state                 # "play_decision" | "over"
        self.message_id = message_id
        # transient
        self.settled = False
        self.replayed = False
        self.busy = False
        self.dealer_shown = False          # have the dealer's cards been revealed?
        self.outcome = None                # fold | dealer_no_qualify | win | lose | push
        self.payout = 0
        self.net = 0
        self.lock = asyncio.Lock()

    @classmethod
    def new(cls, player_id, player_name, channel_id, bet):
        deck = cb.fresh_deck()
        player_cards = [deck.pop(), deck.pop(), deck.pop()]
        dealer_cards = [deck.pop(), deck.pop(), deck.pop()]
        game = cls(uuid.uuid4().hex[:12], player_id, player_name, channel_id, bet, deck,
                   player_cards, dealer_cards)
        game.state = "play_decision"        # await the player's Play / Fold
        return game

    def can_afford_play(self) -> bool:
        return get_bb(self.player_id) >= self.bet

    # --- resolution ---
    def play(self):
        """Caller must already have debited the Play bet. Reveal the dealer."""
        self.dealer_shown = True
        self.total_staked += self.bet
        self.state = "over"

    def fold(self):
        self.state = "over"

    @staticmethod
    def dealer_qualifies(dealer_rank) -> bool:
        """Dealer qualifies with Queen-high or better."""
        cat, tb = dealer_rank
        return cat > 0 or (cat == 0 and tb[0] >= 12)

    # --- serialisation (only the in-flight play decision is persisted) ---
    def to_dict(self) -> dict:
        return {
            "type": KEY, "game_id": self.game_id, "player_id": self.player_id,
            "player_name": self.player_name, "channel_id": self.channel_id,
            "message_id": self.message_id, "bet": self.bet, "deck": self.deck,
            "player_cards": self.player_cards, "dealer_cards": self.dealer_cards,
            "total_staked": self.total_staked, "state": self.state,
        }

    @classmethod
    def from_dict(cls, d):
        return cls(
            d["game_id"], d["player_id"], d.get("player_name", "Player"), d.get("channel_id"),
            d["bet"], d["deck"], d["player_cards"], d["dealer_cards"],
            total_staked=d.get("total_staked", d["bet"]), state=d.get("state", "over"),
            message_id=d.get("message_id"),
        )


# ---------------------------------------------------------------------------
# Outcome decision + payout (decide for display, pay after the message is shown)
# ---------------------------------------------------------------------------
def _ante_bonus(game: TcpGame) -> int:
    cat, _ = cb.three_card_rank(game.player_cards)
    return _ANTE_BONUS.get(cat, 0) * game.bet


def _decide_fold(game: TcpGame):
    if game.outcome is not None:
        return
    game.outcome = "fold"
    game.payout = 0
    game.net = game.payout - game.total_staked      # -B


def _decide_play(game: TcpGame):
    if game.outcome is not None:
        return
    B = game.bet
    player_rank = cb.three_card_rank(game.player_cards)
    dealer_rank = cb.three_card_rank(game.dealer_cards)
    bonus = _ante_bonus(game)

    if not TcpGame.dealer_qualifies(dealer_rank):
        # Ante wins 1:1, Play pushes -> base 3B.
        game.outcome, base = "dealer_no_qualify", 3 * B
    elif player_rank > dealer_rank:
        # Ante 1:1 + Play 1:1 -> base 4B.
        game.outcome, base = "win", 4 * B
    elif player_rank < dealer_rank:
        # Lose both -> base 0.
        game.outcome, base = "lose", 0
    else:
        # Tie (equal rank) -> push both -> base 2B.
        game.outcome, base = "push", 2 * B

    game.payout = base + bonus
    game.net = game.payout - game.total_staked


def _pay(game: TcpGame):
    if game.settled:
        return
    game.settled = True
    if game.payout > 0:
        reason = {
            "dealer_no_qualify": f"{BANK} win (dealer folds)",
            "win": f"{BANK} win",
            "push": f"{BANK} push",
        }.get(game.outcome, f"{BANK} payout")
        cb.credit_from_bank(game.player_id, game.payout, reason)
    record_result(game.player_id, KEY, game.bet, game.total_staked, game.payout, game.outcome)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
SUBTITLE = "Beat the dealer · play or fold"


def _result_banner(game: TcpGame) -> str:
    o = game.outcome
    if o == "win":
        return cb.banner_html("gold" if game.net > 2 * game.bet else "win",
                              "You Win", f"+{game.net:,} UKPence")
    if o == "dealer_no_qualify":
        return cb.banner_html("win", "Dealer Folds", f"+{game.net:,} UKPence")
    if o == "push":
        head = "Push" if game.net == 0 else "Push +Bonus"
        sub = "Bets returned" if game.net == 0 else f"+{game.net:,} UKPence"
        return cb.banner_html("push", head, sub)
    if o == "fold":
        return cb.banner_html("lose", "Folded", f"-{abs(game.net):,} UKPence")
    return cb.banner_html("lose", "Dealer Wins", f"-{abs(game.net):,} UKPence")


def _player_badge(game: TcpGame) -> str:
    return cb.three_card_name(game.player_cards)


def _body(game: TcpGame) -> str:
    if game.dealer_shown:
        dealer_cards = game.dealer_cards
        dealer_badge = cb.three_card_name(game.dealer_cards)
    else:
        dealer_cards = [None, None, None]
        dealer_badge = ""
    dealer = cb.zone_html("Dealer", cb.hand_html(dealer_cards, size="med"),
                          badge=dealer_badge)
    rail = '<div class="vs">VS</div>'
    player = cb.zone_html(game.player_name, cb.hand_html(game.player_cards, size="med"),
                          badge=_player_badge(game))
    return dealer + rail + player


async def _render(game: TcpGame):
    return await cb.render_table(
        title_main="3-Card ", title_accent="Poker", subtitle=SUBTITLE,
        body_html=_body(game), bet=game.total_staked, balance=get_bb(game.player_id),
        hint=("Play or fold?" if game.state == "play_decision" else "Round complete"),
        result_banner=("" if game.state == "play_decision" else _result_banner(game)),
        session_html=session_footer_html(
            game.player_id, session_count=getattr(game, "session_count", 1),
            session_net=getattr(game, "session_net", 0), current_net=getattr(game, "net", 0),
            over=(game.state == "over")),
    )


def _native(game: TcpGame) -> str:
    if game.dealer_shown:
        d = "  ".join(cb.card_text(c) for c in game.dealer_cards)
        d += f"  ({cb.three_card_name(game.dealer_cards)})"
    else:
        d = "  ".join(cb.card_text(None) for _ in range(3))
    p = "  ".join(cb.card_text(c) for c in game.player_cards)
    p += f"  ({cb.three_card_name(game.player_cards)})"
    lines = ["## 🃏 Three Card Poker", f"**Dealer:** {d}", f"**{game.player_name}:** {p}"]
    if game.state == "play_decision":
        lines.append("-# **Play** (match your bet) or **Fold**?")
    else:
        tag = {
            "win": f"✅ You win +{game.net:,}",
            "dealer_no_qualify": f"✅ Dealer folds +{game.net:,}",
            "push": (f"🤝 Push +{game.net:,}" if game.net > 0 else "🤝 Push (bets returned)"),
            "fold": f"🏳️ Folded -{abs(game.net):,}",
            "lose": f"❌ Dealer wins -{abs(game.net):,}",
        }.get(game.outcome, "")
        suffix = "" if (game.outcome == "push" and game.net == 0) else " UKPence"
        lines.append(f"-# {tag}{suffix}  ·  Balance {get_bb(game.player_id):,}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# View
# ---------------------------------------------------------------------------
def _action_row(game: TcpGame) -> discord.ui.ActionRow:
    row = discord.ui.ActionRow()
    if game.state == "play_decision":
        play = discord.ui.Button(label="Play", emoji="🃏", style=discord.ButtonStyle.success,
                                 custom_id=f"{KEY}:{game.game_id}:play")
        play.callback = _make_cb(game, "play")
        row.add_item(play)
        fold = discord.ui.Button(label="Fold", emoji="🏳️", style=discord.ButtonStyle.danger,
                                 custom_id=f"{KEY}:{game.game_id}:fold")
        fold.callback = _make_cb(game, "fold")
        row.add_item(fold)
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


async def build_tcp_layout(game: TcpGame, client):
    import config
    image = None
    if getattr(config, "TCP_IMAGE_ENABLED", True):
        try:
            image = await _render(game)
        except Exception:
            logger.warning("Three Card Poker render failed; using native layout.", exc_info=True)
    return cb.build_layout(image, "tcp.png", _action_row(game), native_text=_native(game))


def build_control_view(game: TcpGame) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(_action_row(game))
    return view


# ---------------------------------------------------------------------------
# Interaction handling
# ---------------------------------------------------------------------------
def _make_cb(game: TcpGame, action: str):
    async def _cb(interaction: Interaction):
        with action_in_flight():
            await _handle_action(interaction, game, action)
    return _cb


async def _show_rules(interaction: Interaction):
    import config
    mn = getattr(config, "TCP_MIN_BET", 5)
    mx = getattr(config, "TCP_MAX_BET", 10_000)
    rules = (
        "## 🃏 Three Card Poker - House Rules\n"
        "Place your bet and you each get **three cards** - yours face up, the dealer's face "
        "down. **Aces are high** and, with only three cards, a **straight beats a flush**.\n\n"
        "- **Fold** to give up your bet.\n"
        "- **Play** to match your bet (you now have two equal bets riding). The dealer reveals.\n"
        "- The dealer only **qualifies** with **Queen-high or better**.\n"
        "  - Dealer **doesn't qualify** -> your first bet pays **1:1**, the Play bet pushes "
        "(net **+1 bet**).\n"
        "  - Dealer qualifies and you **win** -> both bets pay **1:1** (net **+2 bets**).\n"
        "  - Dealer qualifies and you **lose** -> you forfeit both bets.\n"
        "  - **Tie** -> both bets push.\n"
        "- **Hand Bonus** (paid on your first bet no matter what the dealer holds): "
        "Straight **+1x**, Three of a Kind **+4x**, Straight Flush **+5x**.\n"
        f"- **Bets:** {mn:,} - {mx:,} UKPence. Stakes go to the house bank; wins are paid from it.\n\n"
        "-# Good luck. 🇬🇧"
    )
    await interaction.response.send_message(rules, ephemeral=True)


async def _refresh(interaction: Interaction, game: TcpGame, client, *, via_modal=False):
    view, files = await build_tcp_layout(game, client)
    if via_modal:
        await interaction.message.edit(view=view, attachments=files)
    else:
        await interaction.edit_original_response(view=view, attachments=files)
    try:
        client.add_view(view, message_id=game.message_id)
    except Exception:
        logger.debug("tcp add_view after refresh failed (non-fatal)", exc_info=True)


async def _handle_action(interaction: Interaction, game: TcpGame, action: str):
    if action == "rules":
        await _show_rules(interaction)
        return
    if interaction.user.id != game.player_id:
        await interaction.response.send_message(
            "This isn't your table - deal your own with `/threecardpoker`.", ephemeral=True
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

            if game.state != "play_decision":
                await interaction.response.defer()   # stale click on a finished round
                return

            if action == "play" and not game.can_afford_play():
                await interaction.response.send_message(
                    f"You need {game.bet:,} more UKPence to play. Try fold instead.",
                    ephemeral=True,
                )
                return

            await interaction.response.defer()

            if action == "play":
                if not remove_bb(game.player_id, game.bet, reason=f"{BANK} bet"):
                    await interaction.followup.send("You don't have enough UKPence to play.", ephemeral=True)
                    return
                game.play()
                _decide_play(game)
            elif action == "fold":
                game.fold()
                _decide_fold(game)

            cb.delete_state(game.message_id)   # round is over now
            try:
                await _refresh(interaction, game, client)
            except Exception:
                logger.error("Three Card Poker redraw failed after the decision.", exc_info=True)
            _pay(game)                          # credit once the result is on screen
    finally:
        game.busy = False


class ChangeBetModal(discord.ui.Modal, title="Three Card Poker - change your bet"):
    def __init__(self, game: TcpGame):
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
async def _start_replay(interaction: Interaction, old_game: TcpGame, client, bet: int, *, via_modal: bool):
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
    if not getattr(config, "TCP_ENABLED", True):
        await interaction.response.send_message("Three Card Poker is currently closed.", ephemeral=True)
        return
    mn = getattr(config, "TCP_MIN_BET", 5)
    mx = getattr(config, "TCP_MAX_BET", 10_000)
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

    new_game = TcpGame.new(uid, old_game.player_name, old_game.channel_id, bet)
    new_game.message_id = old_game.message_id
    new_game.session_count = getattr(old_game, "session_count", 1) + 1
    new_game.session_net = getattr(old_game, "session_net", 0) + old_game.net
    # Deal always lands on the play_decision state (no auto-resolution at deal time).
    try:
        await _refresh(interaction, new_game, client, via_modal=via_modal)
    except Exception:
        logger.error("Three Card Poker replay failed; refunding stake.", exc_info=True)
        cb.credit_from_bank(uid, bet, f"{BANK} stake refund (replay failed)")
        return
    if new_game.state == "play_decision":
        cb.save_state(new_game.message_id, new_game.to_dict())
    else:
        _pay(new_game)


# ---------------------------------------------------------------------------
# Slash command entry point
# ---------------------------------------------------------------------------
@deal_in_flight
async def handle_tcp_command(interaction: Interaction, amount: int):
    import config
    if await cb.reject_if_maintenance(interaction):
        return
    if not getattr(config, "TCP_ENABLED", True):
        await interaction.response.send_message("Three Card Poker is currently closed.", ephemeral=True)
        return
    mn = getattr(config, "TCP_MIN_BET", 5)
    mx = getattr(config, "TCP_MAX_BET", 10_000)
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
        game = TcpGame.new(interaction.user.id, name, interaction.channel_id, amount)
        # Deal always lands on the play_decision state.
        view, files = await build_tcp_layout(game, interaction.client)
        msg = await interaction.followup.send(view=view, files=files)
    except Exception:
        logger.error("Three Card Poker deal failed; refunding stake.", exc_info=True)
        cb.credit_from_bank(interaction.user.id, amount, f"{BANK} stake refund (deal failed)")
        try:
            await interaction.followup.send("Something went wrong dealing - your stake was refunded.", ephemeral=True)
        except Exception:
            pass
        return

    game.message_id = msg.id
    try:
        if game.state == "play_decision":
            cb.save_state(game.message_id, game.to_dict())
        else:
            _pay(game)
        interaction.client.add_view(view, message_id=msg.id)
    except Exception:
        logger.error("Three Card Poker post-send issue (round is live).", exc_info=True)


# ---------------------------------------------------------------------------
# Restart recovery
# ---------------------------------------------------------------------------
def reattach_tcp_view(client, key, value):
    try:
        game = TcpGame.from_dict(value)
    except Exception as e:
        logger.error(f"Pruning malformed three-card-poker entry {key}: {e}", exc_info=True)
        cb.delete_state(key)
        return
    if game.state != "play_decision":
        cb.delete_state(key)
        return
    try:
        game.message_id = int(key)
        client.add_view(build_control_view(game), message_id=int(key))
    except Exception as e:
        logger.error(f"Failed to reattach three-card-poker view {key}: {e}", exc_info=True)
