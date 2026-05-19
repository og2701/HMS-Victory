import discord
from lib.economy.bank_manager import BankManager
from lib.economy.economy_manager import add_bb, get_bb
from config import CHANNELS
from datetime import datetime
from typing import Optional

async def handle_bank_status_command(interaction: discord.Interaction):
    """Show current bank status"""
    bank_info = BankManager.get_bank_info()

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
        value=f"{bank_info['total_tax_collected']:,} UKPence",
        inline=True
    )

    if bank_info['last_updated'] > 0:
        last_updated = datetime.fromtimestamp(bank_info['last_updated'])
        embed.add_field(
            name="⏰ Last Updated",
            value=last_updated.strftime("%Y-%m-%d %H:%M:%S"),
            inline=False
        )

    embed.set_footer(text="💡 Bank accumulates UKPence from shop purchases & inactivity tax")

    await interaction.response.send_message(embed=embed, ephemeral=True)

