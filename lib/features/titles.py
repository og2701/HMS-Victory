import discord
from database import DatabaseManager

TITLES = [
    "The Grand Admiral",
    "Fleet Commander",
    "Rear Admiral",
    "Commodore",
    "Ship's Captain",
    "First Lieutenant",
    "Sailing Master",
    "Master at Arms",
    "Midshipman",
    "Able Seaman",
    "Master Gunner",
    "Ship's Surgeon",
    "Purser",
    "Boatswain",
    "Ship's Carpenter",
    "Cannon Fodder",
    "Ship's Rat",
    "Mutineer",
    "Barnacle",
    "Deck Scrubber"
]

class TitleLaunchView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=600)

    @discord.ui.button(label="Open Title Manager", style=discord.ButtonStyle.primary)
    async def open_manager(self, interaction: discord.Interaction, button: discord.ui.Button):
        from config import USERS, ROLES
        
        is_oggers = interaction.user.id == USERS.OGGERS
        is_deputy_pm = any(role.id == ROLES.DEPUTY_PM for role in interaction.user.roles)
        
        if not (is_oggers or is_deputy_pm):
            return await interaction.response.send_message("❌ This is for the Grand Admiral only.", ephemeral=True)
            
        view = UserSelectionView()
        await interaction.response.send_message("Select a user to give a title:", view=view, ephemeral=True)

class TitleSelectionView(discord.ui.View):
    def __init__(self, target_member: discord.Member):
        super().__init__(timeout=600)
        self.target_member = target_member
        
        # Fetch current title holders
        holders = DatabaseManager.fetch_all("SELECT user_id, title FROM user_rank_customization WHERE title IS NOT NULL")
        title_to_user = {title: user_id for user_id, title in holders}
        
        options = []
        for title in TITLES:
            holder_id = title_to_user.get(title)
            label = title
            if holder_id:
                holder = target_member.guild.get_member(int(holder_id))
                holder_name = holder.display_name if holder else f"ID: {holder_id}"
                label = f"{title} [ {holder_name} ]"
            
            options.append(discord.SelectOption(label=label, value=title))

        options.append(discord.SelectOption(label="[Remove Title]", value="REMOVE", description="Clears the user's title"))
        
        self.select = discord.ui.Select(
            placeholder=f"Choose a title for {target_member.display_name}...",
            options=options
        )
        self.select.callback = self.on_select
        self.add_item(self.select)

    async def on_select(self, interaction: discord.Interaction):
        title = self.select.values[0]
        user_id = str(self.target_member.id)
        
        if title == "REMOVE":
            DatabaseManager.execute(
                "UPDATE user_rank_customization SET title = NULL WHERE user_id = ?",
                (user_id,)
            )
            msg = f"✅ Removed title from **{self.target_member.display_name}**."
        else:
            # Enforce "one person per title" rule:
            # 1. Clear this specific title from anyone else who might have it
            DatabaseManager.execute(
                "UPDATE user_rank_customization SET title = NULL WHERE title = ?",
                (title,)
            )
            
            # 2. Check if the target user already has a record for customization
            exists = DatabaseManager.fetch_one("SELECT 1 FROM user_rank_customization WHERE user_id = ?", (user_id,))
            if exists:
                DatabaseManager.execute(
                    "UPDATE user_rank_customization SET title = ? WHERE user_id = ?",
                    (title, user_id)
                )
            else:
                DatabaseManager.execute(
                    "INSERT INTO user_rank_customization (user_id, title) VALUES (?, ?)",
                    (user_id, title)
                )
            msg = f"✅ Set title for **{self.target_member.display_name}** to: `{title}`.\n*(Note: This title has been removed from any previous holder)*"
        
        # Now we can just use send_message with ephemeral=True if we want a new message, 
        # or edit the ephemeral message.
        await interaction.response.edit_message(content=msg, view=None)

class UserSelectionView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=600)
        self.user_select = discord.ui.UserSelect(placeholder="Select a user to give a title...", max_values=1)
        self.user_select.callback = self.on_user_select
        self.add_item(self.user_select)

    async def on_user_select(self, interaction: discord.Interaction):
        selected_user = self.user_select.values[0]
        # Resolve to member if possible
        member = interaction.guild.get_member(selected_user.id)
        if not member:
            return await interaction.response.send_message("❌ User not found in this guild.", ephemeral=True)
            
        view = TitleSelectionView(member)
        # Edit the ephemeral message
        await interaction.response.edit_message(content=f"Selected **{member.display_name}**. Now choose a title:", view=view)
