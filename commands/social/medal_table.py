import io
import os
from functools import lru_cache

import discord
from discord import Embed, File, Interaction
from discord.ui import View, Button
from PIL import Image, ImageDraw, ImageFont

from database import DatabaseManager

PAGE_SIZE = 10

# Colour palette
BG_COLOR = (24, 26, 32)
CARD_COLOR = (34, 38, 47)
CARD_ALT_COLOR = (40, 44, 54)
HEADER_COLOR = (48, 52, 64)
TEXT_COLOR = (235, 238, 245)
MUTED_COLOR = (160, 170, 185)
GOLD = (255, 196, 54)
SILVER = (196, 204, 214)
BRONZE = (205, 127, 70)
TOP_ROW_ACCENT = (52, 58, 74)

# Layout
IMG_W = 760
ROW_H = 66
HEADER_H = 60
TITLE_H = 86
FOOTER_H = 46
PADDING = 20

FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]
MONO_FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/Monaco.ttf",
]


@lru_cache(maxsize=16)
def _load_font(size: int, mono: bool = False) -> ImageFont.FreeTypeFont:
    paths = MONO_FONT_PATHS if mono else FONT_PATHS
    for path in paths:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


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


def _rounded_rect(draw: ImageDraw.ImageDraw, xy, radius, fill):
    draw.rounded_rectangle(xy, radius=radius, fill=fill)


def _truncate(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    if draw.textlength(text, font=font) <= max_width:
        return text
    ellipsis = "…"
    while text and draw.textlength(text + ellipsis, font=font) > max_width:
        text = text[:-1]
    return text + ellipsis


def render_page(interaction: Interaction, table: list, page: int) -> bytes:
    total_pages = max(1, (len(table) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * PAGE_SIZE
    slice_ = table[start:start + PAGE_SIZE]

    rows = len(slice_)
    img_h = TITLE_H + HEADER_H + rows * ROW_H + FOOTER_H + PADDING
    img = Image.new("RGB", (IMG_W, img_h), BG_COLOR)
    draw = ImageDraw.Draw(img)

    title_font = _load_font(40)
    header_font = _load_font(24)
    row_font = _load_font(28)
    num_font = _load_font(28, mono=True)
    small_font = _load_font(18)

    # Title bar
    _rounded_rect(draw, (PADDING, PADDING // 2, IMG_W - PADDING, TITLE_H), radius=14, fill=CARD_COLOR)
    # Gold accent bar on left
    draw.rectangle((PADDING + 10, PADDING // 2 + 16, PADDING + 16, TITLE_H - 4), fill=GOLD)
    draw.text((PADDING + 32, PADDING // 2 + 20), "Badge Medal Table", fill=TEXT_COLOR, font=title_font)

    # Column geometry
    col_rank_x = PADDING + 20
    col_name_x = PADDING + 80
    col_total_x = IMG_W - PADDING - 44
    col_bronze_x = col_total_x - 80
    col_silver_x = col_bronze_x - 70
    col_gold_x = col_silver_x - 70
    col_name_max = col_gold_x - col_name_x - 40

    # Header
    header_y = TITLE_H + PADDING // 2
    _rounded_rect(draw, (PADDING, header_y, IMG_W - PADDING, header_y + HEADER_H - 8), radius=10, fill=HEADER_COLOR)
    header_text_y = header_y + 18
    draw.text((col_rank_x, header_text_y), "#", fill=MUTED_COLOR, font=header_font, anchor="lm")
    draw.text((col_name_x, header_text_y + 10), "Member", fill=MUTED_COLOR, font=header_font, anchor="lm")

    # Coloured medal column headers (circle + letter)
    for cx, color, letter in [
        (col_gold_x, GOLD, "G"),
        (col_silver_x, SILVER, "S"),
        (col_bronze_x, BRONZE, "B"),
    ]:
        r = 16
        cy = header_text_y + 10
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color)
        draw.text((cx, cy), letter, fill=(30, 30, 30), font=header_font, anchor="mm")
    draw.text((col_total_x, header_text_y + 10), "Total", fill=MUTED_COLOR, font=header_font, anchor="mm")

    # Rows
    row_top = header_y + HEADER_H
    for i, (uid, g, s, b, total) in enumerate(slice_):
        rank = start + i + 1
        y0 = row_top + i * ROW_H
        y1 = y0 + ROW_H - 6
        row_bg = CARD_ALT_COLOR if i % 2 == 0 else CARD_COLOR
        if rank <= 3:
            row_bg = TOP_ROW_ACCENT
        _rounded_rect(draw, (PADDING, y0, IMG_W - PADDING, y1), radius=8, fill=row_bg)

        # Rank (medal circle for top 3, number otherwise)
        mid_y = (y0 + y1) // 2
        medal_color = {1: GOLD, 2: SILVER, 3: BRONZE}.get(rank)
        if medal_color:
            r = 20
            cx = col_rank_x + 12
            draw.ellipse((cx - r, mid_y - r, cx + r, mid_y + r), fill=medal_color)
            draw.text((cx, mid_y), str(rank), fill=(30, 30, 30), font=header_font, anchor="mm")
        else:
            draw.text((col_rank_x, mid_y), str(rank), fill=MUTED_COLOR, font=num_font, anchor="lm")

        # Name
        name = _truncate(draw, _resolve_name(interaction, uid), row_font, col_name_max)
        draw.text((col_name_x, mid_y), name, fill=TEXT_COLOR, font=row_font, anchor="lm")

        # Medal counts (coloured text for emphasis)
        draw.text((col_gold_x, mid_y), str(g), fill=GOLD, font=row_font, anchor="mm")
        draw.text((col_silver_x, mid_y), str(s), fill=SILVER, font=row_font, anchor="mm")
        draw.text((col_bronze_x, mid_y), str(b), fill=BRONZE, font=row_font, anchor="mm")
        draw.text((col_total_x, mid_y), str(total), fill=TEXT_COLOR, font=row_font, anchor="mm")

    # Footer
    footer_y = row_top + rows * ROW_H + 4
    footer_text = f"Page {page + 1}/{total_pages}   \u2022   {len(table):,} ranked members   \u2022   Ranked by Gold > Silver > Bronze"
    draw.text((IMG_W // 2, footer_y + FOOTER_H // 2), footer_text, fill=MUTED_COLOR, font=small_font, anchor="mm")

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False, compress_level=1)
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

    def _get_page(self) -> bytes:
        if self.page not in self.cache:
            self.cache[self.page] = render_page(self.interaction, self.table, self.page)
        return self.cache[self.page]

    async def _update(self, interaction: Interaction):
        self._sync_buttons()
        image_bytes = self._get_page()
        file = File(fp=io.BytesIO(image_bytes), filename="medal_table.png")
        embed = Embed(color=0xFFD700).set_image(url="attachment://medal_table.png")
        await interaction.response.edit_message(embed=embed, attachments=[file], view=self)

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
    image_bytes = view._get_page()
    file = File(fp=io.BytesIO(image_bytes), filename="medal_table.png")
    embed = Embed(color=0xFFD700).set_image(url="attachment://medal_table.png")
    send_view = view if view.total_pages > 1 else None
    await interaction.followup.send(embed=embed, file=file, view=send_view)
