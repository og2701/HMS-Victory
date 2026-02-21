import discord
from discord import app_commands, Interaction, Member
from config import CHANNELS, ROLES
from lib.economy.economy_manager import get_bb, remove_bb, add_bb
from lib.core.discord_helpers import has_any_role
from lib.core.file_operations import load_persistent_views, save_persistent_views

class WagerDecisionView(discord.ui.View):
    def __init__(self, challenger_id: int, opponent_id: int, amount: int, topic: str, challenger_name: str, opponent_name: str):
        super().__init__(timeout=None)
        self.challenger_id = challenger_id
        self.opponent_id = opponent_id
        self.amount = amount
        self.topic = topic
        self.challenger_name = challenger_name
        self.opponent_name = opponent_name
        # Ensure unique persistent IDs for these buttons
        base_id = f"wager_{self.challenger_id}_{self.opponent_id}_{self.amount}"
        self.btn_challenger.custom_id = f"{base_id}_win_challenger"
        self.btn_challenger.label = f"Winner: {str(self.challenger_name)[:15]}"
        self.btn_opponent.custom_id = f"{base_id}_win_opponent"
        self.btn_opponent.label = f"Winner: {str(self.opponent_name)[:15]}"
        self.btn_draw.custom_id = f"{base_id}_draw"

    async def check_permissions(self, interaction: Interaction) -> bool:
        allowed_roles = [ROLES.PCSO, ROLES.CABINET, ROLES.DEPUTY_PM, ROLES.MINISTER]
        if not has_any_role(interaction, allowed_roles):
            await interaction.response.send_message("Only authorised moderators can resolve wagers.", ephemeral=True)
            return False
        return True

    async def resolve(self, interaction: Interaction, winner_id: int | None):
        pot = self.amount * 2

        if winner_id is None:
            # Draw - refund both
            add_bb(self.challenger_id, self.amount)
            add_bb(self.opponent_id, self.amount)
            result_msg = f"🤝 **Wager Cancelled/Draw!** The pot of {pot:,} UKPence has been refunded to both <@{self.challenger_id}> and <@{self.opponent_id}>."
        else:
            # Winner takes all
            add_bb(winner_id, pot)
            result_msg = f"🏆 **Wager Resolved!** <@{winner_id}> has won the {pot:,} UKPence pot against their opponent for: *{self.topic}*"

        # Disable all buttons
        for child in self.children:
            child.disabled = True

        embed = interaction.message.embeds[0]
        embed.color = 0x2ECC71 if winner_id else 0x95A5A6
        embed.title = "Wager Resolved"
        embed.add_field(name="Resolution", value=result_msg, inline=False)
        embed.set_footer(text=f"Resolved by {interaction.user.display_name}")

        await interaction.response.edit_message(embed=embed, view=self)

        # Notify the original users
        try:
            challenger = interaction.guild.get_member(self.challenger_id) or await interaction.guild.fetch_member(self.challenger_id)
            opponent = interaction.guild.get_member(self.opponent_id) or await interaction.guild.fetch_member(self.opponent_id)
            
            notification = f"The moderator {interaction.user.mention} has resolved your wager regarding: *{self.topic}*.\n{result_msg}"
            
            if challenger: await challenger.send(notification)
            if opponent: await opponent.send(notification)
        except Exception as e:
            await interaction.followup.send("Failed to DM the users the result, but balances were updated.", ephemeral=True)

        # Clean up persistent view
        persistent_views = load_persistent_views()
        if str(interaction.message.id) in persistent_views:
            del persistent_views[str(interaction.message.id)]
            save_persistent_views(persistent_views)

    @discord.ui.button(label="Winner: User A", style=discord.ButtonStyle.success)
    async def btn_challenger(self, interaction: Interaction, button: discord.ui.Button):
        if not await self.check_permissions(interaction): return
        button.label = f"Winner: <@{self.challenger_id}>"
        await self.resolve(interaction, self.challenger_id)

    @discord.ui.button(label="Winner: User B", style=discord.ButtonStyle.success)
    async def btn_opponent(self, interaction: Interaction, button: discord.ui.Button):
        if not await self.check_permissions(interaction): return
        button.label = f"Winner: <@{self.opponent_id}>"
        await self.resolve(interaction, self.opponent_id)

    @discord.ui.button(label="Draw / Cancel", style=discord.ButtonStyle.secondary)
    async def btn_draw(self, interaction: Interaction, button: discord.ui.Button):
        if not await self.check_permissions(interaction): return
        await self.resolve(interaction, None)

class WagerProposalView(discord.ui.View):
    def __init__(self, challenger: Member, opponent: Member, amount: int, topic: str):
        super().__init__(timeout=300) # 5 min timeout
        self.challenger = challenger
        self.opponent = opponent
        self.amount = amount
        self.topic = topic

    @discord.ui.button(label="Accept Wager", style=discord.ButtonStyle.success)
    async def btn_accept(self, interaction: Interaction, button: discord.ui.Button):
        if interaction.user.id != self.opponent.id:
            await interaction.response.send_message("Only the challenged user can accept this wager.", ephemeral=True)
            return

        # Double check balances again just in case
        if get_bb(self.challenger.id) < self.amount:
            await interaction.response.send_message(f"The challenger no longer has {self.amount:,} UKPence.", ephemeral=True)
            return
        if get_bb(self.opponent.id) < self.amount:
            await interaction.response.send_message(f"You no longer have {self.amount:,} UKPence.", ephemeral=True)
            return

        # Deduct from both
        remove_bb(self.challenger.id, self.amount)
        remove_bb(self.opponent.id, self.amount)

        # Send to Community Management
        cabinet_channel = interaction.guild.get_channel(CHANNELS.COMMUNITY_MANAGEMENT)
        if not cabinet_channel:
            # Fallback
            add_bb(self.challenger.id, self.amount)
            add_bb(self.opponent.id, self.amount)
            await interaction.response.send_message("Failed to find the moderation channel. Wager cancelled.", ephemeral=True)
            return

        pot = self.amount * 2
        decision_view = WagerDecisionView(self.challenger.id, self.opponent.id, self.amount, self.topic, self.challenger.display_name, self.opponent.display_name)
        
        embed = discord.Embed(
            title="⚔️ New Wager Needs Resolution",
            description=f"A wager between {self.challenger.mention} and {self.opponent.mention} has been accepted.\n\n**{self.challenger.display_name}** (*Challenger*) has bet against **{self.opponent.display_name}** (*Opponent*) on the following topic:",
            color=0xE67E22
        )
        embed.add_field(name="Topic", value=self.topic, inline=False)
        embed.add_field(name="Total Pot", value=f"{pot:,} UKPence ({self.amount:,} each)", inline=False)
        embed.set_footer(text="Please click a button below to decide the outcome. Winning takes the full pot.")

        msg = await cabinet_channel.send(
            content="New Wager Escrow!",
            embed=embed, 
            view=decision_view
        )

        persistent_views = load_persistent_views()
        persistent_views[str(msg.id)] = {
            "type": "wager",
            "challenger_id": self.challenger.id,
            "opponent_id": self.opponent.id,
            "amount": self.amount,
            "topic": self.topic,
            "challenger_name": self.challenger.display_name,
            "opponent_name": self.opponent.display_name
        }
        save_persistent_views(persistent_views)
        interaction.client.add_view(decision_view, message_id=msg.id)

        # Update the original message
        for child in self.children:
            child.disabled = True
        
        orig_embed = interaction.message.embeds[0]
        orig_embed.color = 0x2ECC71
        orig_embed.title = "Wager Accepted!"
        orig_embed.description = f"The wager has been accepted! {pot:,} UKPence has been placed into Escrow.\n\nWaiting for a moderator to resolve the outcome in the Community Management channel."

        await interaction.response.edit_message(embed=orig_embed, view=self)

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger)
    async def btn_decline(self, interaction: Interaction, button: discord.ui.Button):
        if interaction.user.id != self.opponent.id and interaction.user.id != self.challenger.id:
            await interaction.response.send_message("Only the participants can decline/cancel this wager.", ephemeral=True)
            return

        for child in self.children:
            child.disabled = True
            
        orig_embed = interaction.message.embeds[0]
        orig_embed.color = 0xE74C3C
        
        if interaction.user.id == self.challenger.id:
            orig_embed.title = "Wager Retracted"
            orig_embed.description = f"{self.challenger.mention} retracted their wager against {self.opponent.mention}."
        else:
            orig_embed.title = "Wager Declined"
            orig_embed.description = f"{self.opponent.mention} declined the wager from {self.challenger.mention}."
        
        await interaction.response.edit_message(embed=orig_embed, view=self)

async def handle_wager_command(interaction: Interaction, opponent: Member, amount: int, topic: str):
    if amount <= 0:
        await interaction.response.send_message("Wager amount must be greater than 0 UKPence.", ephemeral=True)
        return
        
    if opponent.bot or opponent.id == interaction.user.id:
        await interaction.response.send_message("You cannot wager against yourself or a bot.", ephemeral=True)
        return

    # Check balances
    challenger_bb = get_bb(interaction.user.id)
    if challenger_bb < amount:
        await interaction.response.send_message(f"You don't have enough UKPence. You only have {challenger_bb:,}.", ephemeral=True)
        return
        
    opponent_bb = get_bb(opponent.id)
    if opponent_bb < amount:
        await interaction.response.send_message(f"Your opponent doesn't have enough UKPence. They only have {opponent_bb:,}.", ephemeral=True)
        return

    embed = discord.Embed(
        title="⚔️ Wager Proposal",
        description=f"{interaction.user.mention} has challenged {opponent.mention} to a wager!",
        color=0xF1C40F
    )
    embed.add_field(name="Topic", value=topic, inline=False)
    embed.add_field(name="Amount", value=f"{amount:,} UKPence (Pot: {amount*2:,})", inline=False)
    embed.set_footer(text=f"{opponent.display_name} has 5 minutes to accept.")

    view = WagerProposalView(interaction.user, opponent, amount, topic)
    await interaction.response.send_message(content=opponent.mention, embed=embed, view=view)
