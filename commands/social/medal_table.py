import html
import io

import discord
from discord import Embed, File, Interaction
from discord.ui import View, Button

from database import DatabaseManager
from lib.core.file_operations import read_html_template
from lib.core.image_processing import screenshot_html

PAGE_SIZE = 10


def _fetch_medal_table():
    rows = DatabaseManager.fetch_all(
        """
        SELECT ub.user_id, b.rarity, COUNT(*)
        FROM user_badges ub
        JOIN badges b ON ub.badge_id = b.id
        WHERE b.rarity IN ('Gold', 'Silver', 'Bronze')
        GROUP BY ub.user_id, b.rarity
        """
    )
    tallies: dict[str, dict[str, int]] = {}
    for user_id, rarity, count in rows:
        t = tallies.setdefault(user_id, {"Gold": 0, "Silver": 0, "Bronze": 0})
        t[rarity] = count

    table = [
        (uid, t["Gold"], t["Silver"], t["Bronze"], t["Gold"] + t["Silver"] + t["Bronze"])
        for uid, t in tallies.items()
    ]
    table.sort(key=lambda r: (r[1], r[2], r[3], r[4]), reverse=True)
    return table


def _resolve_name(interaction: Interaction, user_id: str) -> str:
    try:
        uid_int = int(user_id)
    except ValueError:
        return f"User {user_id}"
    member = interaction.guild.get_member(uid_int) if interaction.guild else None
    if member:
        return member.display_name
    user = interaction.client.get_user(uid_int)
    if user:
        return user.name
    return f"User {user_id}"


def _rank_cell(rank: int) -> str:
    if rank == 1:
        return '<span class="rank-medal gold">1</span>'
    if rank == 2:
        return '<span class="rank-medal silver">2</span>'
    if rank == 3:
        return '<span class="rank-medal bronze">3</span>'
    return str(rank)


def _render_html(interaction: Interaction, table: list, page: int) -> str:
    total_pages = max(1, (len(table) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * PAGE_SIZE
    slice_ = table[start:start + PAGE_SIZE]

    rows_html = []
    for i, (uid, g, s, b, total) in enumerate(slice_):
        rank = start + i + 1
        name = html.escape(_resolve_name(interaction, uid))
        row_class = f"top-{rank}" if rank <= 3 else ""
        rows_html.append(
            f'<tr class="{row_class}">'
            f'<td class="rank">{_rank_cell(rank)}</td>'
            f'<td class="name">{name}</td>'
            f'<td class="count gold-count">{g}</td>'
            f'<td class="count silver-count">{s}</td>'
            f'<td class="count bronze-count">{b}</td>'
            f'<td class="total">{total}</td>'
            f'</tr>'
        )

    template = read_html_template("templates/medal_table.html")
    return (
        template
        .replace("{{ ROWS }}", "\n".join(rows_html))
        .replace("{{ SUBTITLE }}", f"{len(table):,} ranked members")
        .replace("{{ FOOTER_LEFT }}", f"Page {page + 1} of {total_pages}")
        .replace("{{ FOOTER_RIGHT }}", "Ranked by Gold \u203a Silver \u203a Bronze")
    )


async def render_page_bytes(interaction: Interaction, table: list, page: int) -> bytes:
    html_str = _render_html(interaction, table, page)
    buf = await screenshot_html(html_str, size=(900, 1000), element_selector=".container")
    return buf.getvalue()


class MedalTableView(View):
    def __init__(self, interaction: Interaction, table: list):
        super().__init__(timeout=180)
        self.interaction = interaction
        self.table = table
        self.page = 0
        self.total_pages = max(1, (len(table) + PAGE_SIZE - 1) // PAGE_SIZE)
        self.cache: dict[int, bytes] = {}
        self._sync_buttons()

    def _sync_buttons(self):
        self.prev_button.disabled = self.page <= 0
        self.next_button.disabled = self.page >= self.total_pages - 1

    async def _get_page(self) -> bytes:
        if self.page not in self.cache:
            self.cache[self.page] = await render_page_bytes(self.interaction, self.table, self.page)
        return self.cache[self.page]

    async def _update(self, interaction: Interaction):
        await interaction.response.defer()
        self._sync_buttons()
        image_bytes = await self._get_page()
        file = File(fp=io.BytesIO(image_bytes), filename="medal_table.png")
        embed = Embed(color=0xD4AF37).set_image(url="attachment://medal_table.png")
        await interaction.edit_original_response(embed=embed, attachments=[file], view=self)

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: Interaction, button: Button):
        if interaction.user.id != self.interaction.user.id:
            return await interaction.response.send_message("Only the command user can navigate.", ephemeral=True)
        self.page = max(0, self.page - 1)
        await self._update(interaction)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: Interaction, button: Button):
        if interaction.user.id != self.interaction.user.id:
            return await interaction.response.send_message("Only the command user can navigate.", ephemeral=True)
        self.page = min(self.total_pages - 1, self.page + 1)
        await self._update(interaction)


async def handle_medal_table_command(interaction: Interaction):
    await interaction.response.defer()
    table = _fetch_medal_table()
    if not table:
        return await interaction.followup.send("No badges have been awarded yet.", ephemeral=True)

    view = MedalTableView(interaction, table)
    image_bytes = await view._get_page()
    file = File(fp=io.BytesIO(image_bytes), filename="medal_table.png")
    embed = Embed(color=0xD4AF37).set_image(url="attachment://medal_table.png")
    send_view = view if view.total_pages > 1 else None
    await interaction.followup.send(embed=embed, file=file, view=send_view)
