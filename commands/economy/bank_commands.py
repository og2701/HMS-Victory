import discord
from lib.bank_manager import BankManager
from lib.economy_manager import add_bb, get_bb
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

    if bank_info['last_updated'] > 0:
        last_updated = datetime.fromtimestamp(bank_info['last_updated'])
        embed.add_field(
            name="⏰ Last Updated",
            value=last_updated.strftime("%Y-%m-%d %H:%M:%S"),
            inline=False
        )

    embed.set_footer(text="💡 Bank accumulates UKPence from all shop purchases")

    await interaction.response.send_message(embed=embed, ephemeral=True)

async def handle_bank_withdraw_command(interaction: discord.Interaction, amount: int):
    """Withdraw UKPence from bank to user balance (admin only)"""

    if amount <= 0:
        await interaction.response.send_message("❌ Amount must be positive!", ephemeral=True)
        return

    bank_balance = BankManager.get_balance()

    if amount > bank_balance:
        await interaction.response.send_message(
            f"❌ Insufficient bank funds! Bank has {bank_balance:,} UKPence, but {amount:,} requested.",
            ephemeral=True
        )
        return

    # Withdraw from bank and add to user
    success = BankManager.withdraw(amount, f"Admin withdrawal to {interaction.user.display_name}")

    if success:
        add_bb(interaction.user.id, amount)

        embed = discord.Embed(
            title="✅ Bank Withdrawal Complete",
            description=f"Withdrew **{amount:,} UKPence** from bank",
            color=0x00ff00
        )

        user_balance = get_bb(interaction.user.id)
        new_bank_balance = BankManager.get_balance()

        embed.add_field(name="Your New Balance", value=f"{user_balance:,} UKPence", inline=True)
        embed.add_field(name="Bank Balance", value=f"{new_bank_balance:,} UKPence", inline=True)

        # Log the withdrawal
        log_channel = interaction.guild.get_channel(CHANNELS.BOT_USAGE_LOG)
        if log_channel:
            log_embed = discord.Embed(
                title="🏦 Bank Withdrawal",
                color=0x0099ff
            )
            log_embed.add_field(name="Admin", value=interaction.user.mention, inline=True)
            log_embed.add_field(name="Amount", value=f"{amount:,} UKPence", inline=True)
            log_embed.add_field(name="Remaining Bank Balance", value=f"{new_bank_balance:,} UKPence", inline=True)
            await log_channel.send(embed=log_embed)

        await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message("❌ Failed to withdraw from bank!", ephemeral=True)

async def handle_bank_deposit_command(interaction: discord.Interaction, amount: int):
    """Deposit UKPence from user balance to bank (admin only)"""

    if amount <= 0:
        await interaction.response.send_message("❌ Amount must be positive!", ephemeral=True)
        return

    user_balance = get_bb(interaction.user.id)

    if amount > user_balance:
        await interaction.response.send_message(
            f"❌ Insufficient funds! You have {user_balance:,} UKPence, but {amount:,} requested.",
            ephemeral=True
        )
        return

    # Remove from user and deposit to bank
    from lib.economy_manager import remove_bb
    if remove_bb(interaction.user.id, amount):
        BankManager.deposit(amount, f"Admin deposit from {interaction.user.display_name}")

        embed = discord.Embed(
            title="✅ Bank Deposit Complete",
            description=f"Deposited **{amount:,} UKPence** to bank",
            color=0x00ff00
        )

        user_balance = get_bb(interaction.user.id)
        bank_balance = BankManager.get_balance()

        embed.add_field(name="Your New Balance", value=f"{user_balance:,} UKPence", inline=True)
        embed.add_field(name="Bank Balance", value=f"{bank_balance:,} UKPence", inline=True)

        # Log the deposit
        log_channel = interaction.guild.get_channel(CHANNELS.BOT_USAGE_LOG)
        if log_channel:
            log_embed = discord.Embed(
                title="🏦 Bank Deposit",
                color=0x0099ff
            )
            log_embed.add_field(name="Admin", value=interaction.user.mention, inline=True)
            log_embed.add_field(name="Amount", value=f"{amount:,} UKPence", inline=True)
            log_embed.add_field(name="New Bank Balance", value=f"{bank_balance:,} UKPence", inline=True)
            await log_channel.send(embed=log_embed)

        await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message("❌ Failed to remove UKPence from your balance!", ephemeral=True)

async def handle_bank_set_command(interaction: discord.Interaction, amount: int):
    """Set bank balance to specific amount (admin only)"""

    if amount < 0:
        await interaction.response.send_message("❌ Amount cannot be negative!", ephemeral=True)
        return

    old_balance = BankManager.get_balance()
    success = BankManager.set_balance(amount)

    if success:
        embed = discord.Embed(
            title="✅ Bank Balance Set",
            description=f"Bank balance set to **{amount:,} UKPence**",
            color=0x00ff00
        )

        embed.add_field(name="Previous Balance", value=f"{old_balance:,} UKPence", inline=True)
        embed.add_field(name="New Balance", value=f"{amount:,} UKPence", inline=True)

        # Log the change
        log_channel = interaction.guild.get_channel(CHANNELS.BOT_USAGE_LOG)
        if log_channel:
            log_embed = discord.Embed(
                title="🏦 Bank Balance Changed",
                color=0x0099ff
            )
            log_embed.add_field(name="Admin", value=interaction.user.mention, inline=True)
            log_embed.add_field(name="Old Balance", value=f"{old_balance:,} UKPence", inline=True)
            log_embed.add_field(name="New Balance", value=f"{amount:,} UKPence", inline=True)
            await log_channel.send(embed=log_embed)

        await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message("❌ Failed to set bank balance!", ephemeral=True)