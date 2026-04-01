import discord
from typing import List

from lib.economy.bank_manager import BankManager
from lib.economy.economy_manager import add_bb, ensure_bb

class UKPAddAmountModal(discord.ui.Modal, title="UKPence Handout"):
    amount = discord.ui.TextInput(
        label="Amount per user",
        placeholder="e.g. 500",
        required=True,
        min_length=1,
        max_length=7
    )

    def __init__(self, selected_members: List[discord.Member]):
        super().__init__()
        self.selected_members = selected_members

    async def on_submit(self, interaction: discord.Interaction):
        try:
            amount_str = self.amount.value.strip()
            # remove commas if someone entered 1,000
            amount_str = amount_str.replace(",", "")
            amount_val = int(amount_str)
        except ValueError:
            await interaction.response.send_message("❌ Please enter a valid number.", ephemeral=True)
            return

        if amount_val <= 0:
            await interaction.response.send_message("❌ Amount must be greater than 0.", ephemeral=True)
            return

        total_cost = amount_val * len(self.selected_members)
        
        # Check bank balance
        current_bank_balance = BankManager.get_balance()
        if current_bank_balance < total_cost:
            await interaction.response.send_message(
                f"❌ The server bank does not have enough funds for this transaction.\n"
                f"**Required:** {total_cost:,} UKPence\n"
                f"**Available:** {current_bank_balance:,} UKPence",
                ephemeral=True
            )
            return

        # Perform the transaction
        success = BankManager.withdraw(total_cost, description=f"Handout by {interaction.user.name}")
        
        if success:
            from config import CHANNELS
            for member in self.selected_members:
                ensure_bb(member.id)
                add_bb(member.id, amount_val, reason="ukpadd (Deputy PM grant)", from_bank=False)
                
            new_balance = BankManager.get_balance()
            
            users_list_str = ", ".join(m.mention for m in self.selected_members[:10])
            if len(self.selected_members) > 10:
                users_list_str += f" and {len(self.selected_members) - 10} others"

            embed = discord.Embed(
                title="✅ UKPence Successfully Distributed",
                description=f"Successfully distributed **{amount_val:,} UKPence** each to **{len(self.selected_members)}** users.",
                color=0x00FF00
            )
            embed.add_field(name="Total Withdrawn", value=f"{total_cost:,} UKPence", inline=True)
            embed.add_field(name="Bank Balance Remaining", value=f"{new_balance:,} UKPence", inline=True)
            embed.add_field(name="Recipients", value=users_list_str, inline=False)
            embed.set_footer(text=f"Authorized by Deputy PM {interaction.user.display_name}")

            await interaction.response.send_message(embed=embed, ephemeral=False)

        else:
            await interaction.response.send_message("❌ A database error occurred while trying to withdraw from the bank.", ephemeral=True)

class UKPAddUserSelectView(discord.ui.View):
    def __init__(self, author_id: int):
        super().__init__(timeout=180)
        self.author_id = author_id

    @discord.ui.select(
        cls=discord.ui.UserSelect,
        placeholder="Select users to receive UKPence",
        min_values=1,
        max_values=25
    )
    async def select_users(self, interaction: discord.Interaction, select: discord.ui.UserSelect):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ Only the command initiator can use this menu.", ephemeral=True)
            return
            
        selected_members = select.values
        # Open a modal to ask for the amount to dispense to each
        await interaction.response.send_modal(UKPAddAmountModal(selected_members))
        
        # We can safely delete the original message to keep chat clean
        try:
            await interaction.message.delete()
        except discord.NotFound:
            pass
        self.stop()
