"""HMS Victory - Casino War (vs-the-house).

One card each, high card wins (pays 1:1). On a tie you may Go to War - match your bet,
the dealer burns three cards, and one more card is dealt to each: tie-or-higher wins and
pays even money on the raise (the ante pushes); lose and you forfeit both. Or Surrender
to give up half your ante.

Built on commands/economy/casino_base (shared card model, renderer, layout, economy,
persistence). Lifecycle mirrors the other casino games: an HTML->PNG felt table in a
Components V2 view, a native fallback, persistence of the in-flight war decision, a
busy-guard, a Rules button, and Play Again / Change Bet on the result.
"""

import asyncio
import logging
import uuid

import discord
from discord import Interaction

from lib.economy.economy_manager import get_bb, remove_bb
import commands.economy.casino_base as cb

logger = logging.getLogger(__name__)

KEY = "war"
BANK = "Casino War"   # reason keyword routed to the bank's war P/L columns


class WarGame:
    def __init__(self, game_id, player_id, player_name, channel_id, bet, deck,
                 player_card, dealer_card, *, war_player=None, war_dealer=None,
                 total_staked=None, state="over", message_id=None):
        self.game_id = game_id
        self.player_id = int(player_id)
        self.player_name = player_name
        self.channel_id = channel_id
        self.bet = int(bet)
        self.deck = deck
        self.player_card = player_card
        self.dealer_card = dealer_card
        self.war_player = war_player
        self.war_dealer = war_dealer
        self.total_staked = int(total_staked if total_staked is not None else bet)
        self.state = state                 # "war_decision" | "over"
        self.message_id = message_id
        # transient
        self.settled = False
        self.replayed = False
        self.busy = False
        self.outcome = None                # win | lose | war_win | war_lose | surrender
        self.payout = 0
        self.net = 0
        self.lock = asyncio.Lock()

    @classmethod
    def new(cls, player_id, player_name, channel_id, bet):
        deck = cb.fresh_deck()
        p, d = deck.pop(), deck.pop()
        game = cls(uuid.uuid4().hex[:12], player_id, player_name, channel_id, bet, deck, p, d)
        if cb.value(p) == cb.value(d):
            game.state = "war_decision"     # await player's Go to War / Surrender
        else:
            game.state = "over"
        return game

    def can_afford_war(self) -> bool:
        return get_bb(self.player_id) >= self.bet

    # --- resolution ---
    def go_to_war(self):
        """Caller must already have debited the raise. Burn 3, deal the war cards."""
        for _ in range(3):
            if self.deck:
                self.deck.pop()
        self.war_player = self.deck.pop()
        self.war_dealer = self.deck.pop()
        self.total_staked += self.bet
        self.state = "over"

    def surrender(self):
        self.state = "over"

    # --- serialisation (only the in-flight war decision is persisted) ---
    def to_dict(self) -> dict:
        return {
            "type": KEY, "game_id": self.game_id, "player_id": self.player_id,
            "player_name": self.player_name, "channel_id": self.channel_id,
            "message_id": self.message_id, "bet": self.bet, "deck": self.deck,
            "player_card": self.player_card, "dealer_card": self.dealer_card,
            "war_player": self.war_player, "war_dealer": self.war_dealer,
            "total_staked": self.total_staked, "state": self.state,
        }

    @classmethod
    def from_dict(cls, d):
        return cls(
            d["game_id"], d["player_id"], d.get("player_name", "Player"), d.get("channel_id"),
            d["bet"], d["deck"], d["player_card"], d["dealer_card"],
            war_player=d.get("war_player"), war_dealer=d.get("war_dealer"),
            total_staked=d.get("total_staked", d["bet"]), state=d.get("state", "over"),
            message_id=d.get("message_id"),
        )


# ---------------------------------------------------------------------------
# Outcome decision + payout (decide for display, pay after the message is shown)
# ---------------------------------------------------------------------------
def _decide_deal(game: WarGame):
    """Decide an immediate (non-tie) deal."""
    if game.outcome is not None:
        return
    pv, dv = cb.value(game.player_card), cb.value(game.dealer_card)
    if pv > dv:
        game.outcome, game.payout = "win", 2 * game.bet
    else:
        game.outcome, game.payout = "lose", 0
    game.net = game.payout - game.total_staked


def _decide_surrender(game: WarGame):
    if game.outcome is not None:
        return
    game.outcome = "surrender"
    game.payout = game.bet // 2          # forfeit half the ante
    game.net = game.payout - game.total_staked


def _decide_war(game: WarGame):
    if cb.value(game.war_player) >= cb.value(game.war_dealer):
        game.outcome, game.payout = "war_win", 3 * game.bet   # ante pushes + raise pays 1:1
    else:
        game.outcome, game.payout = "war_lose", 0
    game.net = game.payout - game.total_staked


def _pay(game: WarGame):
    if game.settled:
        return
    game.settled = True
    if game.payout > 0:
        reason = {"win": f"{BANK} win", "war_win": f"{BANK} war win",
                  "surrender": f"{BANK} surrender (half back)"}.get(game.outcome, f"{BANK} payout")
        cb.credit_from_bank(game.player_id, game.payout, reason)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
SUBTITLE = "High card wins · a tie goes to war"


def _result_banner(game: WarGame) -> str:
    o = game.outcome
    if o == "win":
        return cb.banner_html("win", "You Win", f"+{game.net:,} UKPence")
    if o == "war_win":
        return cb.banner_html("gold", "War Won!", f"+{game.net:,} UKPence")
    if o == "surrender":
        return cb.banner_html("push", "Surrendered", f"-{abs(game.net):,} UKPence")
    if o == "war_lose":
        return cb.banner_html("lose", "War Lost", f"-{abs(game.net):,} UKPence")
    return cb.banner_html("lose", "Dealer Wins", f"-{abs(game.net):,} UKPence")


def _body(game: WarGame) -> str:
    show_war = game.war_player is not None
    dealer_cards = [game.dealer_card] + ([game.war_dealer] if show_war else [])
    player_cards = [game.player_card] + ([game.war_player] if show_war else [])
    dealer = cb.zone_html("Dealer", cb.hand_html(dealer_cards, size="big"))
    rail = '<div class="rail"><span class="ln"></span><span class="pays">at war</span><span class="ln"></span></div>' if show_war \
        else '<div class="vs">VS</div>'
    player = cb.zone_html(game.player_name, cb.hand_html(player_cards, size="big"))
    return dealer + rail + player


async def _render(game: WarGame):
    return await cb.render_table(
        title_main="Casino ", title_accent="War", subtitle=SUBTITLE,
        body_html=_body(game), bet=game.total_staked, balance=get_bb(game.player_id),
        hint=("Go to war or surrender?" if game.state == "war_decision" else "Round complete"),
        result_banner=("" if game.state == "war_decision" else _result_banner(game)),
    )


def _native(game: WarGame) -> str:
    show_war = game.war_player is not None
    d = cb.card_text(game.dealer_card) + ("  " + cb.card_text(game.war_dealer) if show_war else "")
    p = cb.card_text(game.player_card) + ("  " + cb.card_text(game.war_player) if show_war else "")
    lines = ["## ⚔️ Casino War", f"**Dealer:** {d}", f"**{game.player_name}:** {p}"]
    if game.state == "war_decision":
        lines.append("-# It's a tie - **Go to War** or **Surrender**?")
    else:
        tag = {"win": f"✅ You win +{game.net:,}", "war_win": f"🏆 War won! +{game.net:,}",
               "surrender": f"🏳️ Surrendered -{abs(game.net):,}",
               "war_lose": f"❌ War lost -{abs(game.net):,}",
               "lose": f"❌ Dealer wins -{abs(game.net):,}"}.get(game.outcome, "")
        lines.append(f"-# {tag} UKPence  ·  Balance {get_bb(game.player_id):,}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# View
# ---------------------------------------------------------------------------
def _action_row(game: WarGame) -> discord.ui.ActionRow:
    row = discord.ui.ActionRow()
    if game.state == "war_decision":
        war = discord.ui.Button(label="Go to War", emoji="⚔️", style=discord.ButtonStyle.success,
                                custom_id=f"war:{game.game_id}:war")
        war.callback = _make_cb(game, "war")
        row.add_item(war)
        surr = discord.ui.Button(label="Surrender", emoji="🏳️", style=discord.ButtonStyle.danger,
                                 custom_id=f"war:{game.game_id}:surrender")
        surr.callback = _make_cb(game, "surrender")
        row.add_item(surr)
    else:
        again = discord.ui.Button(label="Play Again", emoji="🔁", style=discord.ButtonStyle.primary,
                                  custom_id=f"war:{game.game_id}:again")
        again.callback = _make_cb(game, "again")
        row.add_item(again)
        change = discord.ui.Button(label="Change Bet", emoji="✏️", style=discord.ButtonStyle.secondary,
                                   custom_id=f"war:{game.game_id}:changebet")
        change.callback = _make_cb(game, "changebet")
        row.add_item(change)
    rules = discord.ui.Button(label="Rules", emoji="📖", style=discord.ButtonStyle.secondary,
                              custom_id=f"war:{game.game_id}:rules")
    rules.callback = _make_cb(game, "rules")
    row.add_item(rules)
    return row


async def build_war_layout(game: WarGame, client):
    import config
    image = None
    if getattr(config, "WAR_IMAGE_ENABLED", True):
        try:
            image = await _render(game)
        except Exception:
            logger.warning("Casino War render failed; using native layout.", exc_info=True)
    return cb.build_layout(image, "war.png", _action_row(game), native_text=_native(game))


def build_control_view(game: WarGame) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(_action_row(game))
    return view


# ---------------------------------------------------------------------------
# Interaction handling
# ---------------------------------------------------------------------------
def _make_cb(game: WarGame, action: str):
    async def _cb(interaction: Interaction):
        await _handle_action(interaction, game, action)
    return _cb


async def _show_rules(interaction: Interaction):
    import config
    mn = getattr(config, "WAR_MIN_BET", 10)
    mx = getattr(config, "WAR_MAX_BET", 10_000)
    rules = (
        "## ⚔️ Casino War - House Rules\n"
        "You and the dealer each get one card. **Aces are high.**\n\n"
        "- **Higher card wins** and pays **1:1**; a lower card loses your bet.\n"
        "- **Tie -> War:** you may **Go to War** by matching your bet. The dealer burns three "
        "cards and deals one more to each. Your war card **ties-or-beats** the dealer's: you win "
        "even money on the raise and your original bet pushes (net +1 bet). Lose and you forfeit "
        "both bets.\n"
        "- Or **Surrender** on the tie to give up half your original bet.\n"
        f"- **Bets:** {mn:,} - {mx:,} UKPence. Stakes go to the house bank; wins are paid from it.\n\n"
        "-# Good luck. 🇬🇧"
    )
    await interaction.response.send_message(rules, ephemeral=True)


async def _refresh(interaction: Interaction, game: WarGame, client, *, via_modal=False):
    view, files = await build_war_layout(game, client)
    if via_modal:
        await interaction.message.edit(view=view, attachments=files)
    else:
        await interaction.edit_original_response(view=view, attachments=files)
    try:
        client.add_view(view, message_id=game.message_id)
    except Exception:
        logger.debug("war add_view after refresh failed (non-fatal)", exc_info=True)


async def _handle_action(interaction: Interaction, game: WarGame, action: str):
    if action == "rules":
        await _show_rules(interaction)
        return
    if interaction.user.id != game.player_id:
        await interaction.response.send_message(
            "This isn't your table - deal your own with `/war`.", ephemeral=True
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

            if game.state != "war_decision":
                await interaction.response.defer()   # stale click on a finished round
                return

            if action == "war" and not game.can_afford_war():
                await interaction.response.send_message(
                    f"You need {game.bet:,} more UKPence to go to war. Try surrender instead.",
                    ephemeral=True,
                )
                return

            await interaction.response.defer()

            if action == "war":
                if not remove_bb(game.player_id, game.bet, reason=f"{BANK} bet"):
                    await interaction.followup.send("You don't have enough UKPence to go to war.", ephemeral=True)
                    return
                game.go_to_war()
                _decide_war(game)
            elif action == "surrender":
                game.surrender()
                _decide_surrender(game)

            cb.delete_state(game.message_id)   # round is over now
            try:
                await _refresh(interaction, game, client)
            except Exception:
                logger.error("Casino War redraw failed after the decision.", exc_info=True)
            _pay(game)                          # credit once the result is on screen
    finally:
        game.busy = False


class ChangeBetModal(discord.ui.Modal, title="Casino War - change your bet"):
    def __init__(self, game: WarGame):
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


async def _start_replay(interaction: Interaction, old_game: WarGame, client, bet: int, *, via_modal: bool):
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
    if not getattr(config, "WAR_ENABLED", True):
        await interaction.response.send_message("Casino War is currently closed.", ephemeral=True)
        return
    mn = getattr(config, "WAR_MIN_BET", 10)
    mx = getattr(config, "WAR_MAX_BET", 10_000)
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

    new_game = WarGame.new(uid, old_game.player_name, old_game.channel_id, bet)
    new_game.message_id = old_game.message_id
    if new_game.state == "over":
        _decide_deal(new_game)
    try:
        await _refresh(interaction, new_game, client, via_modal=via_modal)
    except Exception:
        logger.error("Casino War replay failed; refunding stake.", exc_info=True)
        cb.credit_from_bank(uid, bet, f"{BANK} stake refund (replay failed)")
        return
    if new_game.state == "war_decision":
        cb.save_state(new_game.message_id, new_game.to_dict())
    else:
        _pay(new_game)


# ---------------------------------------------------------------------------
# Slash command entry point
# ---------------------------------------------------------------------------
async def handle_war_command(interaction: Interaction, amount: int):
    import config
    if await cb.reject_if_maintenance(interaction):
        return
    if not getattr(config, "WAR_ENABLED", True):
        await interaction.response.send_message("Casino War is currently closed.", ephemeral=True)
        return
    mn = getattr(config, "WAR_MIN_BET", 10)
    mx = getattr(config, "WAR_MAX_BET", 10_000)
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
        game = WarGame.new(interaction.user.id, name, interaction.channel_id, amount)
        if game.state == "over":
            _decide_deal(game)
        view, files = await build_war_layout(game, interaction.client)
        msg = await interaction.followup.send(view=view, files=files)
    except Exception:
        logger.error("Casino War deal failed; refunding stake.", exc_info=True)
        cb.credit_from_bank(interaction.user.id, amount, f"{BANK} stake refund (deal failed)")
        try:
            await interaction.followup.send("Something went wrong dealing - your stake was refunded.", ephemeral=True)
        except Exception:
            pass
        return

    game.message_id = msg.id
    try:
        if game.state == "war_decision":
            cb.save_state(game.message_id, game.to_dict())
        else:
            _pay(game)
        interaction.client.add_view(view, message_id=msg.id)
    except Exception:
        logger.error("Casino War post-send issue (round is live).", exc_info=True)


# ---------------------------------------------------------------------------
# Restart recovery
# ---------------------------------------------------------------------------
def reattach_war_view(client, key, value):
    try:
        game = WarGame.from_dict(value)
    except Exception as e:
        logger.error(f"Pruning malformed casino-war entry {key}: {e}", exc_info=True)
        cb.delete_state(key)
        return
    if game.state != "war_decision":
        cb.delete_state(key)
        return
    try:
        game.message_id = int(key)
        client.add_view(build_control_view(game), message_id=int(key))
    except Exception as e:
        logger.error(f"Failed to reattach casino-war view {key}: {e}", exc_info=True)
