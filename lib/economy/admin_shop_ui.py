import logging
import discord
from discord.ui import View, Select, Button, Modal, TextInput

from lib.economy.shop_inventory import ShopInventory
from lib.economy.shop_items import get_all_shop_items, get_shop_item_by_id

logger = logging.getLogger(__name__)


def _format_item_embed(item) -> discord.Embed:
    info = ShopInventory.get_item_info(item.item_id) or {}
    qty = info.get("quantity")
    max_qty = info.get("max_quantity")
    auto = info.get("auto_restock", False)
    restock_amt = info.get("restock_amount", 0)

    embed = discord.Embed(
        title=f"🛠 {item.name}",
        description=item.description or "_no description_",
        color=0x5865f2,
    )
    embed.add_field(name="Item ID", value=f"`{item.item_id}`", inline=True)
    embed.add_field(name="Price", value=f"{item.get_price()} UKP", inline=True)
    embed.add_field(name="Visible in shop", value="Yes" if item.show_in_shop else "No", inline=True)
    embed.add_field(name="Stock", value=str(qty) if qty is not None else "—", inline=True)
    embed.add_field(name="Max", value="∞" if max_qty is None else str(max_qty), inline=True)
    embed.add_field(name="Auto-restock", value=f"{'On' if auto else 'Off'} (+{restock_amt}/12h)", inline=True)
    return embed


class _IntModal(Modal):
    """Generic integer-input modal. Calls `on_value(interaction, value)` on submit."""
    value_input = TextInput(label="Value", required=True, max_length=10)

    def __init__(self, title: str, label: str, placeholder: str, allow_blank_for_none: bool, on_value):
        super().__init__(title=title)
        self.value_input.label = label
        self.value_input.placeholder = placeholder
        self.value_input.required = not allow_blank_for_none
        self.allow_blank_for_none = allow_blank_for_none
        self.on_value = on_value

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.value_input.value.strip()
        if not raw:
            if self.allow_blank_for_none:
                await self.on_value(interaction, None)
                return
            await interaction.response.send_message("❌ Value required.", ephemeral=True)
            return
        try:
            parsed = int(raw.replace(",", ""))
        except ValueError:
            await interaction.response.send_message("❌ Must be a whole number.", ephemeral=True)
            return
        if parsed < 0:
            await interaction.response.send_message("❌ Must be ≥ 0.", ephemeral=True)
            return
        await self.on_value(interaction, parsed)


class AdminShopItemView(View):
    def __init__(self, user_id: int, item_id: str):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.item_id = item_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Not for you.", ephemeral=True)
            return False
        return True

    def _item(self):
        return get_shop_item_by_id(self.item_id)

    async def _refresh(self, interaction: discord.Interaction):
        item = self._item()
        if not item:
            await interaction.response.edit_message(content="Item missing.", embed=None, view=None)
            return
        await interaction.response.edit_message(embed=_format_item_embed(item), view=self)

    @discord.ui.button(label="Set Stock", style=discord.ButtonStyle.primary, row=0)
    async def set_stock(self, interaction: discord.Interaction, button: Button):
        async def apply(modal_interaction: discord.Interaction, value: int):
            if not ShopInventory.set_stock(self.item_id, value):
                await modal_interaction.response.send_message("Item not initialized.", ephemeral=True)
                return
            await modal_interaction.response.edit_message(embed=_format_item_embed(self._item()), view=self)
        await interaction.response.send_modal(_IntModal(
            title=f"Set stock — {self.item_id}",
            label="New stock quantity",
            placeholder="e.g. 10",
            allow_blank_for_none=False,
            on_value=apply,
        ))

    @discord.ui.button(label="Set Max", style=discord.ButtonStyle.secondary, row=0)
    async def set_max(self, interaction: discord.Interaction, button: Button):
        async def apply(modal_interaction: discord.Interaction, value):
            ok = ShopInventory.update_settings(self.item_id, max_quantity=value)
            if not ok:
                await modal_interaction.response.send_message("Item not initialized.", ephemeral=True)
                return
            await modal_interaction.response.edit_message(embed=_format_item_embed(self._item()), view=self)
        await interaction.response.send_modal(_IntModal(
            title=f"Set max — {self.item_id}",
            label="Max quantity (blank = unlimited)",
            placeholder="e.g. 60 or leave blank",
            allow_blank_for_none=True,
            on_value=apply,
        ))

    @discord.ui.button(label="Set Restock +N", style=discord.ButtonStyle.secondary, row=0)
    async def set_restock(self, interaction: discord.Interaction, button: Button):
        async def apply(modal_interaction: discord.Interaction, value: int):
            ok = ShopInventory.update_settings(self.item_id, restock_amount=value)
            if not ok:
                await modal_interaction.response.send_message("Item not initialized.", ephemeral=True)
                return
            await modal_interaction.response.edit_message(embed=_format_item_embed(self._item()), view=self)
        await interaction.response.send_modal(_IntModal(
            title=f"Set restock — {self.item_id}",
            label="Amount added per 12h tick",
            placeholder="e.g. 1",
            allow_blank_for_none=False,
            on_value=apply,
        ))

    @discord.ui.button(label="Toggle Auto-Restock", style=discord.ButtonStyle.secondary, row=1)
    async def toggle_auto(self, interaction: discord.Interaction, button: Button):
        info = ShopInventory.get_item_info(self.item_id) or {}
        new_val = not info.get("auto_restock", False)
        ok = ShopInventory.update_settings(self.item_id, auto_restock=new_val)
        if not ok:
            await interaction.response.send_message("Item not initialized.", ephemeral=True)
            return
        await self._refresh(interaction)

    @discord.ui.button(label="Initialize", style=discord.ButtonStyle.success, row=1)
    async def initialize(self, interaction: discord.Interaction, button: Button):
        info = ShopInventory.get_item_info(self.item_id)
        if info:
            await interaction.response.send_message("Already initialized — use the other buttons.", ephemeral=True)
            return
        ShopInventory.initialize_item(self.item_id, 0, None, False, 0)
        await self._refresh(interaction)

    @discord.ui.button(label="◀ Back", style=discord.ButtonStyle.danger, row=1)
    async def back(self, interaction: discord.Interaction, button: Button):
        view = AdminShopLaunchView(self.user_id)
        await interaction.response.edit_message(content="**Shop Admin** — pick an item:", embed=None, view=view)


class _ItemSelect(Select):
    def __init__(self, user_id: int):
        self.user_id = user_id
        options = []
        for item in get_all_shop_items()[:25]:
            info = ShopInventory.get_item_info(item.item_id)
            qty_label = f"{info['quantity']}" + (f"/{info['max_quantity']}" if info and info["max_quantity"] is not None else "") if info else "uninit"
            options.append(discord.SelectOption(
                label=item.name[:100],
                value=item.item_id,
                description=f"id={item.item_id} · stock={qty_label}"[:100],
            ))
        super().__init__(placeholder="Select a shop item to manage…", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Not for you.", ephemeral=True)
            return
        item = get_shop_item_by_id(self.values[0])
        if not item:
            await interaction.response.send_message("Item not found.", ephemeral=True)
            return
        view = AdminShopItemView(self.user_id, item.item_id)
        await interaction.response.edit_message(content=None, embed=_format_item_embed(item), view=view)


class AdminShopLaunchView(View):
    def __init__(self, user_id: int):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.add_item(_ItemSelect(user_id))
