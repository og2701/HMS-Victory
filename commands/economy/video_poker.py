"""HMS Victory - Video Poker (Jacks or Better, vs-the-house).

Bet, get five cards, then **Hold** the ones you want and **Draw** to replace the rest.
You're paid by the poker rank of your final hand (a pair of Jacks or better starts paying).
The most decision-heavy game in the casino - you choose which cards to keep every round.

Built on commands/economy/casino_base (shared card model, 5-card evaluation, renderer,
layout, economy, persistence). The felt table re-renders as you toggle holds and on the
draw; the in-flight draw decision persists so a restart never strands a debited stake.

Paytable is sized for the locked ~800k economy (max bet 10k -> royal-flush jackpot 150k,
not the millions a true 800:1 royal would pay). RTP is tuned to keep a house edge.
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

KEY = "videopoker"
BANK = "Video Poker"   # reason keyword routed to the bank's Video Poker P/L columns

# five_card_rank category -> payout multiple of the bet (total return, "for 1").
# A pair only pays if it's Jacks or better (handled in _decide). Two pair just returns
# your bet (a push); everything below a high pair loses. Top hands capped for the economy.
PAYTABLE = {
    9: 15,   # Royal Flush   (jackpot kept capped so one hit can't warp the 800k economy)
    8: 20,   # Straight Flush (was 12)
    7: 16,   # Four of a Kind (was 10; the big RTP lever - 10-for-1 was very stingy)
    6: 9,    # Full House     (was 8; standard 9/6)
    5: 7,    # Flush          (was 6)
    4: 4,    # Straight
    3: 3,    # Three of a Kind
    2: 2,    # Two Pair
}
JACKS_OR_BETTER = 1   # a pair of J/Q/K/A returns your bet (push)


class VideoPokerGame:
    def __init__(self, game_id, player_id, player_name, channel_id, bet, deck,
                 cards, *, held=None, drawn=False, state="draw_decision", message_id=None):
        self.game_id = game_id
        self.player_id = int(player_id)
        self.player_name = player_name
        self.channel_id = channel_id
        self.bet = int(bet)
        self.deck = deck
        self.cards = list(cards)
        self.held = list(held) if held is not None else [False] * 5
        self.drawn = drawn
        self.state = state                 # "draw_decision" | "over"
        self.message_id = message_id
        # transient
        self.settled = False
        self.replayed = False
        self.busy = False
        self.mult = 0
        self.outcome = None                # hand name, or "nothing"
        self.payout = 0
        self.net = 0
        self.lock = asyncio.Lock()

    @classmethod
    def new(cls, player_id, player_name, channel_id, bet):
        deck = cb.fresh_deck()
        cards = [deck.pop() for _ in range(5)]
        return cls(uuid.uuid4().hex[:12], player_id, player_name, channel_id, bet, deck, cards)

    def toggle_hold(self, i: int):
        if 0 <= i < 5 and self.state == "draw_decision":
            self.held[i] = not self.held[i]

    def draw(self):
        """Replace every un-held card and lock the hand in."""
        for i in range(5):
            if not self.held[i]:
                self.cards[i] = self.deck.pop()
        self.held = [True] * 5
        self.drawn = True
        self.state = "over"

    # --- serialisation (only the in-flight draw decision is persisted) ---
    def to_dict(self) -> dict:
        return {
            "type": KEY, "game_id": self.game_id, "player_id": self.player_id,
            "player_name": self.player_name, "channel_id": self.channel_id,
            "message_id": self.message_id, "bet": self.bet, "deck": self.deck,
            "cards": self.cards, "held": self.held, "drawn": self.drawn, "state": self.state,
        }

    @classmethod
    def from_dict(cls, d):
        return cls(
            d["game_id"], d["player_id"], d.get("player_name", "Player"), d.get("channel_id"),
            d["bet"], d["deck"], d["cards"], held=d.get("held"), drawn=d.get("drawn", False),
            state=d.get("state", "draw_decision"), message_id=d.get("message_id"),
        )


# ---------------------------------------------------------------------------
# Outcome decision + payout
# ---------------------------------------------------------------------------
def _decide(game: VideoPokerGame):
    if game.outcome is not None:
        return
    cat, tb = cb.five_card_rank(game.cards)
    if cat >= 2:
        game.mult = PAYTABLE[cat]
        game.outcome = cb.five_card_name(game.cards)
    elif cat == 1 and tb[0] >= 11:          # Jacks or Better
        game.mult = JACKS_OR_BETTER
        game.outcome = cb.five_card_name(game.cards)
    else:
        game.mult = 0
        game.outcome = "nothing"
    game.payout = game.mult * game.bet
    game.net = game.payout - game.bet


def _pay(game: VideoPokerGame):
    if game.settled:
        return
    game.settled = True
    if game.payout > 0:
        kind = "push" if game.mult == 1 else "win"
        cb.credit_from_bank(game.player_id, game.payout, f"{BANK} {kind}")
    record_result(game.player_id, KEY, game.bet, game.bet, game.payout, game.outcome)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
SUBTITLE = "Hold the cards you want, then draw"
_HELD_TAG = ('<div style="margin-top:8px;font-family:Georgia,serif;font-weight:700;font-size:18px;'
             'letter-spacing:.18em;color:#7CFC9A;text-shadow:0 1px 2px rgba(0,0,0,.6)">HELD</div>')
_GAP_TAG = '<div style="margin-top:8px;height:22px"></div>'


def _result_banner(game: VideoPokerGame) -> str:
    if game.mult >= 9:                      # quads, straight/royal flush
        return cb.banner_html("gold", game.outcome, f"+{game.net:,} UKPence")
    if game.mult > 1:
        return cb.banner_html("win", game.outcome, f"+{game.net:,} UKPence")
    if game.mult == 1:
        return cb.banner_html("push", "Push", "Bet returned")
    return cb.banner_html("lose", "No Win", f"-{game.bet:,} UKPence")


def _body(game: VideoPokerGame) -> str:
    cells = []
    for i, c in enumerate(game.cards):
        # During the draw decision, mark held cards; once over, the hand name says it all.
        tag = _HELD_TAG if (game.held[i] and not game.drawn) else _GAP_TAG
        cells.append(f'<div style="display:flex;flex-direction:column;align-items:center">'
                     f'{cb.card_html(c, size="med")}{tag}</div>')
    hand = '<div class="hand">' + "".join(cells) + "</div>"
    if game.drawn:
        badge = cb.five_card_name(game.cards)
        return cb.zone_html("Your Hand", hand, badge=badge,
                            badge_cls=("gold" if game.mult >= 9 else "win" if game.mult > 1 else ""))
    return cb.zone_html("Your Hand", hand)


async def _render(game: VideoPokerGame):
    return await cb.render_table(
        title_main="Video ", title_accent="Poker", subtitle=SUBTITLE,
        body_html=_body(game), bet=game.bet, balance=get_bb(game.player_id),
        hint=("Hold cards, then Draw" if game.state == "draw_decision" else "Round complete"),
        result_banner=("" if game.state == "draw_decision" else _result_banner(game)),
        session_html=session_footer_html(
            game.player_id, session_count=getattr(game, "session_count", 1),
            session_net=getattr(game, "session_net", 0), current_net=getattr(game, "net", 0),
            over=(game.state == "over")),
    )


def _native(game: VideoPokerGame) -> str:
    row = "  ".join(("[" + cb.card_text(c) + "]" if (game.held[i] and not game.drawn) else cb.card_text(c))
                    for i, c in enumerate(game.cards))
    lines = ["## 🃏 Video Poker - Jacks or Better", f"**Hand:** {row}"]
    if game.state == "draw_decision":
        lines.append("-# Hold the cards you want (shown in `[ ]`), then **Draw**.")
    else:
        if game.mult > 1:
            lines.append(f"-# 🏆 **{game.outcome}** +{game.net:,} UKPence")
        elif game.mult == 1:
            lines.append(f"-# 🤝 **{game.outcome}** - bet returned")
        else:
            lines.append(f"-# ❌ No win -{game.bet:,} UKPence")
        lines.append(f"-# Balance {get_bb(game.player_id):,} UKPence")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# View
# ---------------------------------------------------------------------------
def _action_rows(game: VideoPokerGame) -> list:
    rows = []
    if game.state == "draw_decision":
        hold_row = discord.ui.ActionRow()
        for i, c in enumerate(game.cards):
            held = game.held[i]
            btn = discord.ui.Button(
                label=cb.disp_rank(c[0]), emoji=cb.SUIT_EMOJI[c[1]],
                style=discord.ButtonStyle.success if held else discord.ButtonStyle.secondary,
                custom_id=f"videopoker:{game.game_id}:hold{i}",
            )
            btn.callback = _make_cb(game, f"hold{i}")
            hold_row.add_item(btn)
        rows.append(hold_row)

        ctrl = discord.ui.ActionRow()
        draw = discord.ui.Button(label="Draw", emoji="🎴", style=discord.ButtonStyle.primary,
                                 custom_id=f"videopoker:{game.game_id}:draw")
        draw.callback = _make_cb(game, "draw")
        ctrl.add_item(draw)
        rules = discord.ui.Button(label="Rules", emoji="📖", style=discord.ButtonStyle.secondary,
                                  custom_id=f"videopoker:{game.game_id}:rules")
        rules.callback = _make_cb(game, "rules")
        ctrl.add_item(rules)
        rows.append(ctrl)
    else:
        row = discord.ui.ActionRow()
        again = discord.ui.Button(label="Play Again", emoji="🔁", style=discord.ButtonStyle.primary,
                                  custom_id=f"videopoker:{game.game_id}:again")
        again.callback = _make_cb(game, "again")
        row.add_item(again)
        change = discord.ui.Button(label="Change Bet", emoji="✏️", style=discord.ButtonStyle.secondary,
                                   custom_id=f"videopoker:{game.game_id}:changebet")
        change.callback = _make_cb(game, "changebet")
        row.add_item(change)
        rules = discord.ui.Button(label="Rules", emoji="📖", style=discord.ButtonStyle.secondary,
                                  custom_id=f"videopoker:{game.game_id}:rules")
        rules.callback = _make_cb(game, "rules")
        row.add_item(rules)
        rows.append(row)
    return rows


async def build_vp_layout(game: VideoPokerGame, client):
    import config
    image = None
    if getattr(config, "VIDEOPOKER_IMAGE_ENABLED", True):
        try:
            image = await _render(game)
        except Exception:
            logger.warning("Video Poker render failed; using native layout.", exc_info=True)

    view = discord.ui.LayoutView(timeout=None)
    files = []
    if image is not None:
        files = [discord.File(image, filename="videopoker.png")]
        gallery = discord.ui.MediaGallery()
        gallery.add_item(media="attachment://videopoker.png")
        view.add_item(gallery)
    else:
        container = discord.ui.Container(accent_colour=cb.ACCENT)
        container.add_item(discord.ui.TextDisplay(_native(game)))
        view.add_item(container)
    for row in _action_rows(game):
        view.add_item(row)
    return view, files


def build_control_view(game: VideoPokerGame) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=None)
    for row in _action_rows(game):
        view.add_item(row)
    return view


# ---------------------------------------------------------------------------
# Interaction handling
# ---------------------------------------------------------------------------
def _make_cb(game: VideoPokerGame, action: str):
    async def _cb(interaction: Interaction):
        await _handle_action(interaction, game, action)
    return _cb


async def _show_rules(interaction: Interaction):
    import config
    mn = getattr(config, "VIDEOPOKER_MIN_BET", 5)
    mx = getattr(config, "VIDEOPOKER_MAX_BET", 10_000)
    pay = (
        f"- 👑 Royal Flush - **{PAYTABLE[9]}x**\n"
        f"- Straight Flush - **{PAYTABLE[8]}x**\n"
        f"- Four of a Kind - **{PAYTABLE[7]}x**\n"
        f"- Full House - **{PAYTABLE[6]}x**\n"
        f"- Flush - **{PAYTABLE[5]}x**\n"
        f"- Straight - **{PAYTABLE[4]}x**\n"
        f"- Three of a Kind - **{PAYTABLE[3]}x**\n"
        f"- Two Pair - **{PAYTABLE[2]}x**\n"
        f"- Pair of Jacks or better - **{JACKS_OR_BETTER}x** (your bet back)\n"
    )
    rules = (
        "## 🃏 Video Poker - House Rules (Jacks or Better)\n"
        "You're dealt **five cards**. Tap a card to **Hold** it, then **Draw** to replace the "
        "rest. You're paid for the poker rank of your final five-card hand:\n\n"
        f"{pay}\n"
        "Anything below a pair of Jacks loses. Payouts are multiples of your bet (a `5x` win "
        "pays five times your stake).\n"
        f"- **Bets:** {mn:,} - {mx:,} UKPence. Stakes go to the house bank; wins are paid from it.\n\n"
        "-# Good luck. 🇬🇧"
    )
    await interaction.response.send_message(rules, ephemeral=True)


async def _refresh(interaction: Interaction, game: VideoPokerGame, client, *, via_modal=False):
    view, files = await build_vp_layout(game, client)
    if via_modal:
        await interaction.message.edit(view=view, attachments=files)
    else:
        await interaction.edit_original_response(view=view, attachments=files)
    try:
        client.add_view(view, message_id=game.message_id)
    except Exception:
        logger.debug("video poker add_view after refresh failed (non-fatal)", exc_info=True)


async def _handle_action(interaction: Interaction, game: VideoPokerGame, action: str):
    if action == "rules":
        await _show_rules(interaction)
        return
    if interaction.user.id != game.player_id:
        await interaction.response.send_message(
            "This isn't your machine - deal your own with `/video-poker`.", ephemeral=True
        )
        return
    if action == "changebet":
        await interaction.response.send_modal(ChangeBetModal(game))
        return

    # A hold toggle mutates state synchronously (so rapid taps all register) and then
    # re-renders if not already mid-render; the render of dropped taps is skipped, but
    # the held state stays correct for the eventual Draw.
    if action.startswith("hold"):
        if game.state != "draw_decision":
            await interaction.response.defer()
            return
        game.toggle_hold(int(action[4:]))
        if game.busy:
            await interaction.response.defer()
            return
        game.busy = True
        try:
            await interaction.response.defer()
            try:
                await _refresh(interaction, game, interaction.client)
            except Exception:
                logger.error("Video Poker hold redraw failed.", exc_info=True)
        finally:
            game.busy = False
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

            if action == "draw":
                if game.state != "draw_decision":
                    await interaction.response.defer()
                    return
                await interaction.response.defer()
                game.draw()
                _decide(game)
                cb.delete_state(game.message_id)
                try:
                    await _refresh(interaction, game, client)
                except Exception:
                    logger.error("Video Poker draw redraw failed.", exc_info=True)
                _pay(game)
    finally:
        game.busy = False


class ChangeBetModal(discord.ui.Modal, title="Video Poker - change your bet"):
    def __init__(self, game: VideoPokerGame):
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


async def _start_replay(interaction: Interaction, old_game: VideoPokerGame, client, bet: int, *, via_modal: bool):
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
    if not getattr(config, "VIDEOPOKER_ENABLED", True):
        await interaction.response.send_message("Video Poker is currently closed.", ephemeral=True)
        return
    mn = getattr(config, "VIDEOPOKER_MIN_BET", 5)
    mx = getattr(config, "VIDEOPOKER_MAX_BET", 10_000)
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

    new_game = VideoPokerGame.new(uid, old_game.player_name, old_game.channel_id, bet)
    new_game.message_id = old_game.message_id
    new_game.session_count = getattr(old_game, "session_count", 1) + 1
    new_game.session_net = getattr(old_game, "session_net", 0) + old_game.net
    try:
        await _refresh(interaction, new_game, client, via_modal=via_modal)
    except Exception:
        logger.error("Video Poker replay failed; refunding stake.", exc_info=True)
        cb.credit_from_bank(uid, bet, f"{BANK} stake refund (replay failed)")
        return
    # New hand is a fresh draw decision - persist it (a fresh deal is never instantly over).
    try:
        cb.save_state(new_game.message_id, new_game.to_dict())
    except Exception:
        logger.error("Video Poker replay post-update issue (round is live).", exc_info=True)


# ---------------------------------------------------------------------------
# Slash command entry point
# ---------------------------------------------------------------------------
async def handle_videopoker_command(interaction: Interaction, amount: int):
    import config
    if await cb.reject_if_maintenance(interaction):
        return
    if not getattr(config, "VIDEOPOKER_ENABLED", True):
        await interaction.response.send_message("Video Poker is currently closed.", ephemeral=True)
        return
    mn = getattr(config, "VIDEOPOKER_MIN_BET", 5)
    mx = getattr(config, "VIDEOPOKER_MAX_BET", 10_000)
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
        game = VideoPokerGame.new(interaction.user.id, name, interaction.channel_id, amount)
        view, files = await build_vp_layout(game, interaction.client)
        msg = await interaction.followup.send(view=view, files=files)
    except Exception:
        logger.error("Video Poker deal failed; refunding stake.", exc_info=True)
        cb.credit_from_bank(interaction.user.id, amount, f"{BANK} stake refund (deal failed)")
        try:
            await interaction.followup.send("Something went wrong dealing - your stake was refunded.", ephemeral=True)
        except Exception:
            pass
        return

    game.message_id = msg.id
    try:
        cb.save_state(game.message_id, game.to_dict())     # draw decision is in flight
        interaction.client.add_view(view, message_id=msg.id)
    except Exception:
        logger.error("Video Poker post-send issue (round is live).", exc_info=True)


# ---------------------------------------------------------------------------
# Restart recovery
# ---------------------------------------------------------------------------
def reattach_videopoker_view(client, key, value):
    try:
        game = VideoPokerGame.from_dict(value)
    except Exception as e:
        logger.error(f"Pruning malformed video-poker entry {key}: {e}", exc_info=True)
        cb.delete_state(key)
        return
    if game.state != "draw_decision":
        cb.delete_state(key)
        return
    try:
        game.message_id = int(key)
        client.add_view(build_control_view(game), message_id=int(key))
    except Exception as e:
        logger.error(f"Failed to reattach video-poker view {key}: {e}", exc_info=True)
