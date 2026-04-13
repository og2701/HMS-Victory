import discord
from discord import Embed, Interaction
from discord.ui import View, Button

from database import DatabaseManager

PAGE_SIZE = 10


def _fetch_medal_table():
    """Return a list of (user_id, gold, silver, bronze, total) sorted olympic-style."""
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
    # Olympic ranking: gold, then silver, then bronze, then total as final tiebreak
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


def _build_embed(interaction: Interaction, table: list, page: int) -> Embed:
    total_pages = max(1, (len(table) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * PAGE_SIZE
    slice_ = table[start:start + PAGE_SIZE]

    name_width = 18
    lines = [
        f"{'#':>3}  {'Member':<{name_width}} {'🥇':>3} {'🥈':>3} {'🥉':>3} {'Σ':>4}",
        "─" * (3 + 2 + name_width + 1 + 3 + 1 + 3 + 1 + 3 + 1 + 4),
    ]
    for idx, (uid, g, s, b, total) in enumerate(slice_, start=start + 1):
        name = _resolve_name(interaction, uid)
        if len(name) > name_width:
            name = name[: name_width - 1] + "…"
        lines.append(f"{idx:>3}  {name:<{name_width}} {g:>3} {s:>3} {b:>3} {total:>4}")

    body = "```\n" + "\n".join(lines) + "\n```"

    embed = Embed(
        title="🏅 Badge Medal Table",
        description=body,
        color=0xFFD700,
    )
    embed.set_footer(text=f"Page {page + 1}/{total_pages} • {len(table)} ranked members")
    return embed


class MedalTableView(View):
    def __init__(self, interaction: Interaction, table: list):
        super().__init__(timeout=120)
        self.interaction = interaction
        self.table = table
        self.page = 0
        self.total_pages = max(1, (len(table) + PAGE_SIZE - 1) // PAGE_SIZE)
        self._sync_buttons()

    def _sync_buttons(self):
        self.prev_button.disabled = self.page <= 0
        self.next_button.disabled = self.page >= self.total_pages - 1

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: Interaction, button: Button):
        if interaction.user.id != self.interaction.user.id:
            return await interaction.response.send_message("Only the command user can navigate.", ephemeral=True)
        self.page -= 1
        self._sync_buttons()
        await interaction.response.edit_message(embed=_build_embed(self.interaction, self.table, self.page), view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: Interaction, button: Button):
        if interaction.user.id != self.interaction.user.id:
            return await interaction.response.send_message("Only the command user can navigate.", ephemeral=True)
        self.page += 1
        self._sync_buttons()
        await interaction.response.edit_message(embed=_build_embed(self.interaction, self.table, self.page), view=self)


async def handle_medal_table_command(interaction: Interaction):
    await interaction.response.defer()
    table = _fetch_medal_table()
    if not table:
        return await interaction.followup.send("No badges have been awarded yet.", ephemeral=True)

    embed = _build_embed(interaction, table, 0)
    view = MedalTableView(interaction, table) if len(table) > PAGE_SIZE else None
    await interaction.followup.send(embed=embed, view=view) if view else await interaction.followup.send(embed=embed)
