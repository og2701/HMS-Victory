"""HMS Victory - /casino lobby.

One front door for the house games. /casino shows an ephemeral menu; pick a table and
a small modal asks for your bet, then the chosen game launches publicly in the channel.
The individual /blackjack, /higher-lower and /slots commands still work too - this just
adds a single discoverable entry point.
"""

import logging

import discord
from discord import Interaction

from commands.economy.blackjack import handle_blackjack_command
from commands.economy.higher_lower import handle_higherlower_command
from commands.economy.slots import handle_slots_command
from commands.economy.video_poker import handle_videopoker_command
from commands.economy.red_dog import handle_reddog_command
from commands.economy.three_card_poker import handle_tcp_command
from commands.economy.roulette import handle_roulette_command
from commands.economy.mines import handle_mines_command
from commands.economy.chest import handle_chest_command
from commands.economy.blockade import handle_blockade_command
from commands.economy.penalty import handle_penalty_command
from lib.economy.casino_stats import get_net_standings

logger = logging.getLogger(__name__)
ACCENT = discord.Colour(0xD4AF37)  # brass
SCOPE_OVERALL = "overall"

GAMES = [
    {"key": "blackjack", "label": "Blackjack", "emoji": "🎴",
     "handler": handle_blackjack_command,
     "desc": "Beat the dealer to 21 - a natural blackjack pays 3:2."},
    {"key": "higherlower", "label": "Higher or Lower", "emoji": "🔼",
     "handler": handle_higherlower_command,
     "desc": "Climb the card ladder; cash out any time before you bust."},
    {"key": "slots", "label": "Fruit Machine", "emoji": "🎰",
     "handler": handle_slots_command,
     "desc": "Spin three reels - match symbols for the jackpot."},
    {"key": "videopoker", "label": "Video Poker", "emoji": "🃏",
     "handler": handle_videopoker_command,
     "desc": "Hold the cards you want, draw, paid by poker rank."},
    {"key": "reddog", "label": "Red Dog", "emoji": "🐕",
     "handler": handle_reddog_command,
     "desc": "Bet the third card falls between the first two."},
    {"key": "tcp", "label": "3-Card Poker", "emoji": "♣️",
     "handler": handle_tcp_command,
     "desc": "Make the best three-card hand and beat the dealer."},
    {"key": "roulette", "label": "Roulette", "emoji": "🎡",
     "handler": handle_roulette_command, "prompt_bet": False,
     "desc": "Place chips on the felt (red/black, dozens, numbers…) and spin the wheel."},
    {"key": "mines", "label": "Mines", "emoji": "💣",
     "handler": handle_mines_command,
     "desc": "Reveal gems and cash out before you hit a mine."},
    {"key": "chest", "label": "Chest Upgrade", "emoji": "🧰",
     "handler": handle_chest_command,
     "desc": "Risk it to level up the chest - cash out before it shatters."},
    {"key": "blockade", "label": "Blockade Run", "emoji": "🚢",
     "handler": handle_blockade_command,
     "desc": "Run the blockade - bank the climbing multiplier before they sink you."},
    {"key": "penalty", "label": "Penalty Shootout", "emoji": "⚽",
     "handler": handle_penalty_command,
     "desc": "Beat the keeper from the spot - score to build your multiplier."},
]


# ---------------------------------------------------------------------------
# Casino leaderboard (net P/L, overall or per game)
# ---------------------------------------------------------------------------
def _fmt_net(n: int) -> str:
    return f"+{n:,}" if n >= 0 else f"-{abs(n):,}"


def _scope_label(scope: str) -> str:
    if scope == SCOPE_OVERALL:
        return "All Games"
    return next((g["label"] for g in GAMES if g["key"] == scope), scope)


def build_leaderboard_embed(scope: str) -> discord.Embed:
    game = None if scope == SCOPE_OVERALL else scope
    winners, losers = get_net_standings(game=game, top=5)

    embed = discord.Embed(
        title=f"🏰 Casino Leaderboard - {_scope_label(scope)}",
        description="Players ranked by **net profit / loss** (UKPence won minus staked).",
        colour=ACCENT,
    )
    if not winners and not losers:
        embed.description = "No casino games have been played yet - be the first! 🎲"
        return embed

    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]

    def _rows(rows):
        out = []
        for i, (uid, net, games) in enumerate(rows):
            tag = medals[i] if i < len(medals) else f"{i + 1}."
            out.append(f"{tag} <@{uid}> · **{_fmt_net(net)}** · {games} game{'s' if games != 1 else ''}")
        return "\n".join(out) if out else "-"

    embed.add_field(name="📈 Biggest Winners", value=_rows(winners), inline=False)
    if losers:
        embed.add_field(name="📉 Biggest Losers", value=_rows(losers), inline=False)
    return embed


class CasinoLeaderboardView(discord.ui.View):
    """Public leaderboard with a dropdown to switch between Overall and each game.
    Persistent (timeout=None, fixed custom_id) so the dropdown keeps working on a
    public message and survives restarts - registered once globally in setup_hook."""
    def __init__(self, scope: str = SCOPE_OVERALL):
        super().__init__(timeout=None)
        self.scope = scope
        options = [discord.SelectOption(label="All Games", value=SCOPE_OVERALL, emoji="🏰",
                                        default=(scope == SCOPE_OVERALL))]
        for g in GAMES:
            options.append(discord.SelectOption(label=g["label"], value=g["key"], emoji=g["emoji"],
                                                default=(scope == g["key"])))
        self.select = discord.ui.Select(
            placeholder="Choose a game or overall…", options=options,
            custom_id="casino:lb:scope",
        )
        self.select.callback = self._on_select
        self.add_item(self.select)

    async def _on_select(self, interaction: Interaction):
        # Rebuild a fresh view for the chosen scope (no shared-state mutation - this
        # one view object is registered globally and handles every leaderboard message).
        scope = self.select.values[0]
        await interaction.response.edit_message(
            embed=build_leaderboard_embed(scope), view=CasinoLeaderboardView(scope),
        )


async def _leaderboard_cb(interaction: Interaction):
    await interaction.response.send_message(
        embed=build_leaderboard_embed(SCOPE_OVERALL),
        view=CasinoLeaderboardView(),
        allowed_mentions=discord.AllowedMentions.none(),
    )


class BetModal(discord.ui.Modal):
    """Asks for a bet, then hands off to the game's own handler (which validates the
    amount against that game's min/max and the player's balance)."""

    def __init__(self, game: dict):
        super().__init__(title=f"{game['label']} - place your bet")
        self.game = game
        self.amount = discord.ui.TextInput(
            label="Bet (UKPence)", placeholder="e.g. 100", required=True, max_length=12,
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
        if amount <= 0:
            await interaction.response.send_message(
                "Your bet must be greater than 0 UKPence.", ephemeral=True
            )
            return
        # The game handler does the rest (min/max limits, balance, maintenance gate).
        await self.game["handler"](interaction, amount)


class CasinoMenuView(discord.ui.View):
    """The /casino lobby as one compact dropdown of games plus a Leaderboard button.

    Collapsing the games into a select keeps the lobby tiny, and opening the dropdown gives
    Discord's native type-to-filter over the game list (the "search"). Public and persistent
    (timeout=None, fixed custom_ids) - registered once globally in setup_hook so it keeps
    working on the public message and survives restarts.
    """

    def __init__(self):
        super().__init__(timeout=None)
        options = [
            discord.SelectOption(
                label=g["label"], value=g["key"], emoji=g["emoji"],
                description=g["desc"][:100],
            )
            for g in GAMES
        ]
        self.select = discord.ui.Select(
            placeholder="🎲  Pick a game to play…", options=options,
            min_values=1, max_values=1, custom_id="casino:menu:pick",
        )
        self.select.callback = self._on_pick
        self.add_item(self.select)

        lb = discord.ui.Button(
            label="Leaderboard", emoji="🏆", style=discord.ButtonStyle.secondary,
            custom_id="casino:leaderboard",
        )
        lb.callback = _leaderboard_cb
        self.add_item(lb)

    async def _on_pick(self, interaction: Interaction):
        if getattr(interaction.client, "maintenance_mode", False):
            await interaction.response.send_message(
                "🔧 **Under maintenance** - the bot is restarting for an update. "
                "Hold on a minute before playing.", ephemeral=True)
            return
        key = self.select.values[0]
        game = next((g for g in GAMES if g["key"] == key), None)
        if game is None:
            await interaction.response.send_message("That game isn't available.", ephemeral=True)
            return
        # Most games ask for a stake first; bet-slip games (roulette) open straight away.
        if game.get("prompt_bet", True):
            await interaction.response.send_modal(BetModal(game))
        else:
            await game["handler"](interaction)


def build_casino_embed() -> discord.Embed:
    import config
    mn = getattr(config, "BLACKJACK_MIN_BET", 5)
    mx = getattr(config, "BLACKJACK_MAX_BET", 10_000)
    embed = discord.Embed(
        title="🎰 HMS Victory - Casino Royale",
        description=("Pick a table from the dropdown below and place your stake - wins are "
                     "paid from the house bank. Open the menu to browse or search the games."),
        colour=ACCENT,
    )
    embed.set_footer(text=f"Bets {mn:,} - {mx:,} UKPence. Please gamble responsibly. 🇬🇧")
    return embed


def build_casino_menu() -> "CasinoMenuView":
    """Return the persistent lobby view (used by /casino and the setup_hook registration)."""
    return CasinoMenuView()


async def handle_casino_command(interaction: Interaction):
    if getattr(interaction.client, "maintenance_mode", False):
        await interaction.response.send_message(
            "🔧 **Under maintenance** - the bot is restarting for an update. "
            "Hold on a minute before playing.", ephemeral=True
        )
        return
    # Public lobby: anyone can see it and pick a game to start their own hand. The dropdown
    # + leaderboard button are stable, custom-id'd components registered as a global
    # persistent view in setup_hook, so they keep working across restarts.
    await interaction.response.send_message(
        embed=build_casino_embed(), view=build_casino_menu(),
        allowed_mentions=discord.AllowedMentions.none(),
    )
