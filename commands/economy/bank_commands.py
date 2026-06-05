import discord
from lib.economy.bank_manager import BankManager
from lib.economy.economy_manager import add_bb, get_bb
from config import CHANNELS
from datetime import datetime
from typing import Optional

async def handle_bank_status_command(interaction: discord.Interaction):
    """Show current bank status"""
    bank_info = BankManager.get_bank_info()
    # Tax + blackjack P/L are derived from the transaction ledger so they include all
    # history and can't drift from a counter (the old stored tax counter read 0).
    ledger = BankManager.get_ledger_stats()

    embed = discord.Embed(
        title="🏦 Server Bank Status",
        color=0x00aa00
    )

    embed.add_field(
        name="💰 Current Balance",
        value=f"{bank_info['balance']:,} UKPence",
        inline=True
    )

    embed.add_field(
        name="📈 Total Revenue",
        value=f"{bank_info['total_revenue']:,} UKPence",
        inline=True
    )

    embed.add_field(
        name="🏛️ Total Tax Collected",
        value=f"{ledger['tax_collected']:,} UKPence",
        inline=True
    )

    def _short_pl(net):
        sign = "+" if net >= 0 else "-"
        note = "house ahead" if net > 0 else ("players ahead" if net < 0 else "even")
        return f"{sign}{abs(net):,} ({note})"

    # Per-game house P/L (positive = the bank is ahead), three across.
    embed.add_field(name="🎴 Blackjack", value=_short_pl(ledger['blackjack_net']), inline=True)
    embed.add_field(name="🔼 Higher/Lower", value=_short_pl(ledger['higherlower_net']), inline=True)
    embed.add_field(name="🎰 Fruit Machine", value=_short_pl(ledger['slots_net']), inline=True)
    embed.add_field(name="🃏 Video Poker", value=_short_pl(ledger['videopoker_net']), inline=True)
    embed.add_field(name="🐕 Red Dog", value=_short_pl(ledger['reddog_net']), inline=True)
    embed.add_field(name="♣️ 3-Card Poker", value=_short_pl(ledger['tcp_net']), inline=True)
    embed.add_field(name="🎡 Roulette", value=_short_pl(ledger['roulette_net']), inline=True)

    casino_net = ledger['casino_net']
    casino_sign = "+" if casino_net >= 0 else "-"
    casino_note = "house ahead" if casino_net > 0 else ("players ahead" if casino_net < 0 else "even")
    embed.add_field(
        name="🏰 Total Casino (House P/L)",
        value=(
            f"{casino_sign}{abs(casino_net):,} UKPence ({casino_note})\n"
            f"`{ledger['casino_in']:,}` staked in · `{ledger['casino_out']:,}` paid out"
        ),
        inline=False
    )

    if bank_info['last_updated'] > 0:
        last_updated = datetime.fromtimestamp(bank_info['last_updated'])
        embed.add_field(
            name="⏰ Last Updated",
            value=last_updated.strftime("%Y-%m-%d %H:%M:%S"),
            inline=False
        )

    embed.set_footer(text="💡 Bank accumulates UKPence from shop purchases, wealth tax & the blackjack edge")

    from lib.economy.bonds import BondOverviewView
    await interaction.response.send_message(embed=embed, view=BondOverviewView(), ephemeral=True)

