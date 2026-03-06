import discord
from discord.ui import View, Select
from typing import List
from database import DatabaseManager
from lib.economy.shop_items import get_shop_items, RankBackgroundItem, RankColorThemeItem

class RankEquipSelect(Select):
    """Dropdown to select an equipped customisation."""
    def __init__(self, items: set, item_type: str, user_id: int):
        self.item_type = item_type
        self.user_id = user_id
        
        # Get shop items to lookup names based on DB values
        self.all_shop_items = get_shop_items()

        options = []
        if item_type == "background":
            placeholder = "Select a Background to equip..."
            for bg in items:
                # Find the shop item that gave this background
                shop_item = next((item for item in self.all_shop_items if isinstance(item, RankBackgroundItem) and item.bg_filename == bg), None)
                label = shop_item.name if shop_item else bg
                options.append(discord.SelectOption(label=label, value=bg, emoji="🖼️"))
        else:
            placeholder = "Select a Colour Theme to equip..."
            for theme in items:
                # Theme in DB is a concatenated string "primary,secondary,tertiary", we unpack to find shop item
                primary, secondary, tertiary = theme.split(",")
                shop_item = next((item for item in self.all_shop_items if isinstance(item, RankColorThemeItem) and item.primary == primary and item.secondary == secondary and item.tertiary == tertiary), None)
                label = shop_item.name if shop_item else "Custom Theme"
                options.append(discord.SelectOption(label=label, value=theme, emoji="🎨"))
        
        super().__init__(placeholder=placeholder, options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("This menu is not for you!", ephemeral=True)

        selected_value = self.values[0]
        user_id_str = str(self.user_id)
        
        # Ensure user row exists in user_rank_customization
        exists = DatabaseManager.fetch_one("SELECT 1 FROM user_rank_customization WHERE user_id = ?", (user_id_str,))
        
        if self.item_type == "background":
            if exists:
                DatabaseManager.execute("UPDATE user_rank_customization SET background = ? WHERE user_id = ?", (selected_value, user_id_str))
            else:
                DatabaseManager.execute("INSERT INTO user_rank_customization (user_id, background) VALUES (?, ?)", (user_id_str, selected_value))
            await interaction.response.send_message(f"✅ Background updated! Use `/rank` to see it.", ephemeral=True)
            
        elif self.item_type == "theme":
            primary, secondary, tertiary = selected_value.split(",")
            if exists:
                DatabaseManager.execute("UPDATE user_rank_customization SET primary_color = ?, secondary_color = ?, tertiary_color = ? WHERE user_id = ?", (primary, secondary, tertiary, user_id_str))
            else:
                DatabaseManager.execute("INSERT INTO user_rank_customization (user_id, primary_color, secondary_color, tertiary_color) VALUES (?, ?, ?, ?)", (user_id_str, primary, secondary, tertiary))
            await interaction.response.send_message(f"✅ Colour Theme updated! Use `/rank` to see it.", ephemeral=True)


class RankEquipView(View):
    """View holding the equipment dropdowns."""
    def __init__(self, user_id: int, owned_backgrounds: set, owned_themes: set):
        super().__init__(timeout=300)
        self.user_id = user_id
        
        if owned_backgrounds:
             self.add_item(RankEquipSelect(owned_backgrounds, "background", user_id))
        
        if owned_themes:
             self.add_item(RankEquipSelect(owned_themes, "theme", user_id))

async def handle_rank_equip_command(interaction: discord.Interaction):
    """Handle the /rank-equip command."""
    user_id_str = str(interaction.user.id)
    
    # Check shop_purchases for what the user owns
    purchases = DatabaseManager.fetch_all("SELECT item_id FROM shop_purchases WHERE user_id = ?", (user_id_str,))
    if not purchases:
        return await interaction.response.send_message("You haven't purchased any rank customisations yet. Visit the `/shop`!", ephemeral=True)

    owned_item_ids = [p[0] for p in purchases]
    
    all_shop_items = get_shop_items()
    owned_backgrounds = set()
    owned_themes = set()
    
    for item_id in owned_item_ids:
        shop_item = next((item for item in all_shop_items if item.id == item_id), None)
        if isinstance(shop_item, RankBackgroundItem):
            owned_backgrounds.add(shop_item.bg_filename)
        elif isinstance(shop_item, RankColorThemeItem):
            theme_str = f"{shop_item.primary},{shop_item.secondary},{shop_item.tertiary}"
            owned_themes.add(theme_str)

    if not owned_backgrounds and not owned_themes:
        return await interaction.response.send_message("You don't own any eligible rank customisations. Visit the `/shop`!", ephemeral=True)

    # Let the user know they can equip defaults too
    owned_backgrounds.add("unionjack.png")
    owned_themes.add("#CF142B,#00247D,#FFFFFF")

    view = RankEquipView(interaction.user.id, owned_backgrounds, owned_themes)
    
    embed = discord.Embed(
        title="🎨 Equip Rank Customisations",
        description="Select a background or colour theme from the dropdowns below to equip to your `/rank` card.",
        color=0x2b2d31
    )
    
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
