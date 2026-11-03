import discord
from typing import List, Optional

from config import ROLES
from lib.economy.bank_manager import BankManager

ACCENT = discord.Colour.gold()
# A role handout pays everyone in it, so cap it before a big role empties the bank by accident.
ROLE_HANDOUT_CAP = 250
NO_PINGS = discord.AllowedMentions.none()


def _is_deputy_pm(user) -> bool:
    roles = getattr(user, "roles", None)
    if not roles:
        return False
    return any(role.id == ROLES.DEPUTY_PM for role in roles)

class UKPAddAmountModal(discord.ui.Modal, title="UKPence Handout"):
    amount = discord.ui.TextInput(
        label="Amount per user",
        placeholder="e.g. 500",
        required=True,
        min_length=1,
        max_length=7
    )

    def __init__(self, selected_members: List[discord.Member], role: Optional[discord.Role] = None):
        super().__init__()
        self.selected_members = selected_members
        self.role = role

    async def on_submit(self, interaction: discord.Interaction):
        if not _is_deputy_pm(interaction.user):
            await interaction.response.send_message(
                "❌ Only the Deputy PM can hand out UKPence.", ephemeral=True
            )
            return
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

        # The bank debit, every recipient credit, balance history, and statement
        # entries commit together. A failure for any recipient rolls back all of it.
        success = BankManager.transfer_to_users(
            [member.id for member in self.selected_members],
            amount_val,
            description=f"Handout by {interaction.user.name}" + (f" to {self.role.name}" if self.role else ""),
            user_reason="ukpadd (Deputy PM grant)",
        )
        
        if success:
            new_balance = BankManager.get_balance()
            
            users_list_str = ", ".join(m.mention for m in self.selected_members[:10])
            if len(self.selected_members) > 10:
                users_list_str += f" and {len(self.selected_members) - 10} others"

            embed = discord.Embed(
                title="✅ UKPence Successfully Distributed",
                description=f"Successfully distributed **{amount_val:,} UKPence** each to " + (
                    f"the **{len(self.selected_members)}** members of {self.role.mention}." if self.role
                    else f"**{len(self.selected_members)}** users."),
                color=0x00FF00
            )
            embed.add_field(name="Total Withdrawn", value=f"{total_cost:,} UKPence", inline=True)
            embed.add_field(name="Bank Balance Remaining", value=f"{new_balance:,} UKPence", inline=True)
            embed.add_field(name="Recipients", value=users_list_str, inline=False)
            embed.set_footer(text=f"Authorised by Deputy PM {interaction.user.display_name}")

            await interaction.response.send_message(embed=embed, ephemeral=False)

        else:
            await interaction.response.send_message("❌ A database error occurred while trying to withdraw from the bank.", ephemeral=True)

def _role_recipients(role: discord.Role) -> List[discord.Member]:
    return [m for m in role.members if not m.bot]


class _PeopleSelect(discord.ui.UserSelect):
    def __init__(self):
        super().__init__(placeholder="Pick people", min_values=1, max_values=25)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(UKPAddAmountModal(list(self.values)))
        try:
            await interaction.message.delete()
        except discord.NotFound:
            pass


class _RoleSelect(discord.ui.RoleSelect):
    def __init__(self, author_id: int, role: Optional[discord.Role] = None):
        super().__init__(placeholder="Or pick a role to pay everyone in it", min_values=1, max_values=1,
                         default_values=[discord.Object(id=role.id, type=discord.Role)] if role else [])
        self.author_id = author_id

    async def callback(self, interaction: discord.Interaction):
        role = self.values[0]
        count = len(_role_recipients(role))
        if count == 0:
            await interaction.response.send_message(
                f"❌ Nobody has {role.mention}, apart from bots.", ephemeral=True, allowed_mentions=NO_PINGS)
            return
        if count > ROLE_HANDOUT_CAP:
            await interaction.response.send_message(
                f"❌ {role.mention} has {count:,} people. A handout can cover up to {ROLE_HANDOUT_CAP}.",
                ephemeral=True, allowed_mentions=NO_PINGS)
            return
        await interaction.response.edit_message(view=UKPAddPickerView(self.author_id, role))


class _RoleAmountButton(discord.ui.Button):
    def __init__(self, role: discord.Role):
        super().__init__(label="Enter amount", style=discord.ButtonStyle.primary, emoji="🪙")
        self.role = role

    async def callback(self, interaction: discord.Interaction):
        members = _role_recipients(self.role)
        # The role can change between picking it and pressing this, so check again.
        if not members or len(members) > ROLE_HANDOUT_CAP:
            await interaction.response.send_message(
                f"❌ {self.role.mention} now has {len(members):,} people, so pick it again.",
                ephemeral=True, allowed_mentions=NO_PINGS)
            return
        await interaction.response.send_modal(UKPAddAmountModal(members, role=self.role))
        try:
            await interaction.message.delete()
        except discord.NotFound:
            pass


class UKPAddPickerView(discord.ui.LayoutView):
    """The plain 'ukpadd' card: pick people, or pick a role and pay everyone in it."""

    def __init__(self, author_id: int, role: Optional[discord.Role] = None):
        super().__init__(timeout=180)
        self.author_id = author_id
        card = discord.ui.Container(accent_colour=ACCENT)
        if role is None:
            card.add_item(discord.ui.TextDisplay(
                "## 🪙 UKPence handout\nPick the people to pay, or pick a role to pay everyone in it."))
        else:
            count = len(_role_recipients(role))
            who = "person has" if count == 1 else "people have"
            card.add_item(discord.ui.TextDisplay(
                f"## 🪙 Pay everyone in {role.mention}\n**{count:,}** {who} this role. You'll set the amount each next."))
        card.add_item(discord.ui.ActionRow(_PeopleSelect()))
        card.add_item(discord.ui.ActionRow(_RoleSelect(author_id, role)))
        if role is not None:
            card.add_item(discord.ui.ActionRow(_RoleAmountButton(role)))
        card.add_item(discord.ui.TextDisplay("-# Paid from the server bank. Bots are skipped."))
        self.add_item(card)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ Only the command initiator can use this.", ephemeral=True)
            return False
        if not _is_deputy_pm(interaction.user):
            await interaction.response.send_message("❌ Only the Deputy PM can hand out UKPence.", ephemeral=True)
            return False
        return True


class UKPAddAmountLaunchView(discord.ui.View):
    def __init__(self, author_id: int, selected_members: List[discord.Member]):
        super().__init__(timeout=180)
        self.author_id = author_id
        self.selected_members = selected_members

    @discord.ui.button(label="Enter amount", style=discord.ButtonStyle.primary)
    async def enter_amount(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ Only the command initiator can use this.", ephemeral=True)
            return
        if not _is_deputy_pm(interaction.user):
            await interaction.response.send_message(
                "❌ Only the Deputy PM can hand out UKPence.", ephemeral=True
            )
            return

        await interaction.response.send_modal(UKPAddAmountModal(self.selected_members))

        try:
            await interaction.message.delete()
        except discord.NotFound:
            pass
        self.stop()
