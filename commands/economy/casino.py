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

logger = logging.getLogger(__name__)
ACCENT = discord.Colour(0xD4AF37)  # brass

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
]


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


def _make_pick_cb(game: dict):
    async def _cb(interaction: Interaction):
        await interaction.response.send_modal(BetModal(game))
    return _cb


def build_casino_menu() -> discord.ui.LayoutView:
    import config
    mn = getattr(config, "BLACKJACK_MIN_BET", 10)
    mx = getattr(config, "BLACKJACK_MAX_BET", 10_000)

    view = discord.ui.LayoutView(timeout=None)
    container = discord.ui.Container(accent_colour=ACCENT)
    lines = [
        "## 🎰 HMS Victory - Casino Royale",
        "Pick a table below and enter your stake. Wins are paid from the house bank.",
        "",
    ]
    for g in GAMES:
        lines.append(f"{g['emoji']} **{g['label']}** - {g['desc']}")
    lines.append(f"\n-# Bets {mn:,} - {mx:,} UKPence. Please gamble responsibly. 🇬🇧")
    container.add_item(discord.ui.TextDisplay("\n".join(lines)))
    view.add_item(container)

    row = discord.ui.ActionRow()
    for g in GAMES:
        btn = discord.ui.Button(
            label=g["label"], emoji=g["emoji"], style=discord.ButtonStyle.success,
            custom_id=f"casino:pick:{g['key']}",
        )
        btn.callback = _make_pick_cb(g)
        row.add_item(btn)
    view.add_item(row)
    return view


async def handle_casino_command(interaction: Interaction):
    if getattr(interaction.client, "maintenance_mode", False):
        await interaction.response.send_message(
            "🔧 **Under maintenance** - the bot is restarting for an update. "
            "Hold on a minute before playing.", ephemeral=True
        )
        return
    # Ephemeral so the menu is private to the player (it self-expires); the launched
    # game posts publicly from the modal-submit interaction.
    await interaction.response.send_message(view=build_casino_menu(), ephemeral=True)
