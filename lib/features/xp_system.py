import discord
import time
import random
import io
import asyncio
from database import DatabaseManager
from config import *
from lib.core.constants import CHAT_LEVEL_ROLE_THRESHOLDS
from lib.economy.economy_manager import get_bb
from lib.core.image_processing import screenshot_html

class LeaderboardView(discord.ui.View):
    PAGE_SIZE = 20

    def __init__(self, xp_system, guild, sorted_data):
        super().__init__(timeout=None)
        self.xp_system = xp_system
        self.guild = guild
        self.sorted_data = sorted_data
        self.offset = 0
        self.image_cache = {}
        self.previous_button.disabled = True
        self.next_button.disabled = (len(self.sorted_data) <= self.PAGE_SIZE)

    def get_slice(self):
        return self.sorted_data[self.offset : self.offset + self.PAGE_SIZE]

    async def _get_or_generate_image(self):
        next_off = self.offset + self.PAGE_SIZE
        if next_off < len(self.sorted_data) and next_off not in self.image_cache:
            self.image_cache[next_off] = asyncio.create_task(
                self.xp_system.generate_leaderboard_image(self.guild, self.sorted_data[next_off : next_off + self.PAGE_SIZE], next_off)
            )

        prev_off = self.offset - self.PAGE_SIZE
        if prev_off >= 0 and prev_off not in self.image_cache:
            self.image_cache[prev_off] = asyncio.create_task(
                self.xp_system.generate_leaderboard_image(self.guild, self.sorted_data[prev_off : prev_off + self.PAGE_SIZE], prev_off)
            )

        if self.offset not in self.image_cache:
            self.image_cache[self.offset] = await self.xp_system.generate_leaderboard_image(
                self.guild, self.get_slice(), self.offset
            )
        elif isinstance(self.image_cache[self.offset], asyncio.Task):
            self.image_cache[self.offset] = await self.image_cache[self.offset]

        return discord.File(fp=io.BytesIO(self.image_cache[self.offset]), filename="leaderboard.png")

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.blurple)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.response.is_done():
            await interaction.response.defer()
        self.offset = max(0, self.offset - self.PAGE_SIZE)
        file = await self._get_or_generate_image()
        self.previous_button.disabled = (self.offset == 0)
        self.next_button.disabled = (self.offset + self.PAGE_SIZE >= len(self.sorted_data))
        await interaction.edit_original_response(attachments=[file], view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.blurple)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.response.is_done():
            await interaction.response.defer()
        max_off = max(0, len(self.sorted_data) - self.PAGE_SIZE)
        self.offset = min(max_off, self.offset + self.PAGE_SIZE)
        file = await self._get_or_generate_image()
        self.previous_button.disabled = (self.offset == 0)
        self.next_button.disabled = (self.offset + self.PAGE_SIZE >= len(self.sorted_data))
        await interaction.edit_original_response(attachments=[file], view=self)

class RichListView(discord.ui.View):
    PAGE_SIZE = 20

    def __init__(self, xp_system, guild, sorted_data):
        super().__init__(timeout=None)
        self.xp_system = xp_system
        self.guild = guild
        self.sorted_data = sorted_data
        self.offset = 0
        self.image_cache = {}
        self.previous_button.disabled = True
        self.next_button.disabled = (len(self.sorted_data) <= self.PAGE_SIZE)

    def get_slice(self):
        return self.sorted_data[self.offset : self.offset + self.PAGE_SIZE]

    async def _get_or_generate_image(self):
        next_off = self.offset + self.PAGE_SIZE
        if next_off < len(self.sorted_data) and next_off not in self.image_cache:
            self.image_cache[next_off] = asyncio.create_task(
                self.xp_system.generate_richlist_image(self.guild, self.sorted_data[next_off : next_off + self.PAGE_SIZE], next_off)
            )

        prev_off = self.offset - self.PAGE_SIZE
        if prev_off >= 0 and prev_off not in self.image_cache:
            self.image_cache[prev_off] = asyncio.create_task(
                self.xp_system.generate_richlist_image(self.guild, self.sorted_data[prev_off : prev_off + self.PAGE_SIZE], prev_off)
            )

        if self.offset not in self.image_cache:
            self.image_cache[self.offset] = await self.xp_system.generate_richlist_image(
                self.guild, self.get_slice(), self.offset
            )
        elif isinstance(self.image_cache[self.offset], asyncio.Task):
            self.image_cache[self.offset] = await self.image_cache[self.offset]

        return discord.File(fp=io.BytesIO(self.image_cache[self.offset]), filename="richlist.png")

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.blurple)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.response.is_done():
            await interaction.response.defer()
        self.offset = max(0, self.offset - self.PAGE_SIZE)
        file = await self._get_or_generate_image()
        self.previous_button.disabled = (self.offset == 0)
        self.next_button.disabled = (self.offset + self.PAGE_SIZE >= len(self.sorted_data))
        await interaction.edit_original_response(attachments=[file], view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.blurple)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.response.is_done():
            await interaction.response.defer()
        max_off = max(0, len(self.sorted_data) - self.PAGE_SIZE)
        self.offset = min(max_off, self.offset + self.PAGE_SIZE)
        file = await self._get_or_generate_image()
        self.previous_button.disabled = (self.offset == 0)
        self.next_button.disabled = (self.offset + self.PAGE_SIZE >= len(self.sorted_data))
        await interaction.edit_original_response(attachments=[file], view=self)

class XPSystem:
    def get_role_for_xp(self, xp):
        role_id = None
        for threshold, rid in CHAT_LEVEL_ROLE_THRESHOLDS:
            if xp >= threshold:
                role_id = rid
            else:
                break
        return role_id

    async def update_xp(self, message: discord.Message):
        user_id = str(message.author.id)
        now = time.time()
        
        result = DatabaseManager.fetch_one("SELECT xp, last_xp_time FROM xp WHERE user_id = ?", (user_id,))
        
        current_xp = result[0] if result else 0
        last_xp_time = result[1] if result else 0

        if (now - last_xp_time) >= 120:
            gain = random.randint(10, 20)
            new_xp = current_xp + gain
            DatabaseManager.execute("INSERT OR REPLACE INTO xp (user_id, xp, last_xp_time) VALUES (?, ?, ?)", (user_id, new_xp, now))
            
            new_role_id = self.get_role_for_xp(new_xp)
            if new_role_id:
                guild = message.guild
                new_role = guild.get_role(new_role_id)
                if new_role:
                    rank_ids = [rid for _, rid in CHAT_LEVEL_ROLE_THRESHOLDS]
                    old_roles = [r for r in message.author.roles if r.id in rank_ids]
                    if new_role not in message.author.roles:
                        if old_roles:
                            await message.author.remove_roles(*old_roles)
                        await message.author.add_roles(new_role)
                        embed = discord.Embed(
                            description=f"{message.author.mention} has progressed to **{new_role.name}**!",
                            color=discord.Color.green()
                        )
                        await message.channel.send(embed=embed)

    def get_rank(self, user_id: str):
        # Optimized SQL query to find rank: count users with more XP + 1
        query = """
            SELECT 
                (SELECT COUNT(*) + 1 FROM xp WHERE xp > (SELECT xp FROM xp WHERE user_id = ?)),
                (SELECT xp FROM xp WHERE user_id = ?)
        """
        result = DatabaseManager.fetch_one(query, (user_id, user_id))
        
        if result and result[1] is not None:
            return result[0], result[1]
        return None, 0

    def get_all_sorted_xp(self):
        sorted_xp = DatabaseManager.fetch_all("SELECT user_id, xp FROM xp ORDER BY xp DESC")
        return sorted_xp

    async def generate_leaderboard_image(self, guild: discord.Guild, data_slice, offset):
        with open("templates/leaderboard.html", "r", encoding="utf-8") as f:
            template = f.read()

        left_html, right_html = "", ""
        half = len(data_slice) // 2

        for i, (uid, xp_val) in enumerate(data_slice):
            rank = offset + i + 1
            member = guild.get_member(int(uid))
            if member:
                name = member.display_name
                avatar = member.display_avatar.url
            else:
                name = "Unknown"
                avatar = "https://cdn.discordapp.com/embed/avatars/0.png"
            block = f"""
            <div class="flex items-center mb-2 bg-black/50 rounded p-2">
              <p class="mr-3 font-bold">#{rank}</p>
              <div class="w-12 h-12 rounded-full overflow-hidden">
                <img src="{avatar}" class="w-full h-full object-cover" />
              </div>
              <div class="ml-3">
                <p class="font-bold">{name}</p>
                <p class="text-gray-300 text-sm">XP: {xp_val}</p>
              </div>
            </div>
            """
            if i < half:
                left_html += block
            else:
                right_html += block

        two_col = f"""
        <div class="flex space-x-6">
          <div class="flex flex-col">{left_html}</div>
          <div class="flex flex-col">{right_html}</div>
        </div>
        """
        final_html = template.replace("{{ LEADERBOARD_ROWS }}", two_col)
        image_buffer = screenshot_html(final_html, size=(1200, 1200))
        return image_buffer.getvalue()

    async def handle_leaderboard_command(self, interaction: discord.Interaction):
        data = self.get_all_sorted_xp()
        if not data:
            return await interaction.followup.send("No XP data found.")
        view = LeaderboardView(self, interaction.guild, data)
        first = data[: LeaderboardView.PAGE_SIZE]
        image_bytes = await self.generate_leaderboard_image(interaction.guild, first, 0)
        view.image_cache[0] = image_bytes
        
        if LeaderboardView.PAGE_SIZE < len(data):
            next_off = LeaderboardView.PAGE_SIZE
            slice_data = data[next_off : next_off + LeaderboardView.PAGE_SIZE]
            view.image_cache[next_off] = asyncio.create_task(
                self.generate_leaderboard_image(interaction.guild, slice_data, next_off)
            )

        file = discord.File(fp=io.BytesIO(image_bytes), filename="leaderboard.png")
        await interaction.followup.send(file=file, view=view)

    def get_all_balances(self):
        balances = DatabaseManager.fetch_all("SELECT user_id, balance FROM ukpence ORDER BY balance DESC")
        return balances

    async def generate_richlist_image(self, guild, data_slice, offset):
        with open("templates/leaderboard.html", "r", encoding="utf-8") as f:
            template = f.read()

        left_html, right_html = "", ""
        half = RichListView.PAGE_SIZE // 2

        for i, (uid, bal) in enumerate(data_slice):
            rank = offset + i + 1
            member = guild.get_member(int(uid))
            name = member.display_name if member else "Unknown"
            avatar = member.display_avatar.url if member else "https://cdn.discordapp.com/embed/avatars/0.png"
            block = f"""
            <div class="flex items-center mb-2 bg-black/50 rounded p-2">
              <p class="mr-3 font-bold">#{rank}</p>
              <div class="w-12 h-12 rounded-full overflow-hidden">
                <img src="{avatar}" class="w-full h-full object-cover" />
              </div>
              <div class="ml-3">
                <p class="font-bold">{name}</p>
                <p class="text-gray-300 text-sm">UKPence: {bal:,}</p>
              </div>
            </div>
            """
            if i < half:
                left_html += block
            else:
                right_html += block

        two_col = f"""
        <div class="flex space-x-6">
          <div class="flex flex-col">{left_html}</div>
          <div class="flex flex-col">{right_html}</div>
        </div>
        """
        final_html = template.replace("{{ LEADERBOARD_ROWS }}", two_col)
        image_buffer = screenshot_html(final_html, size=(1200, 1200))
        return image_buffer.getvalue()

    async def handle_richlist_command(self, interaction: discord.Interaction):
        data = self.get_all_balances()
        if not data:
            return await interaction.followup.send("No UKPence data found.")
        view = RichListView(self, interaction.guild, data)
        first = data[: RichListView.PAGE_SIZE]
        image_bytes = await self.generate_richlist_image(interaction.guild, first, 0)
        view.image_cache[0] = image_bytes
        
        if RichListView.PAGE_SIZE < len(data):
            next_off = RichListView.PAGE_SIZE
            slice_data = data[next_off : next_off + RichListView.PAGE_SIZE]
            view.image_cache[next_off] = asyncio.create_task(
                self.generate_richlist_image(interaction.guild, slice_data, next_off)
            )

        file = discord.File(fp=io.BytesIO(image_bytes), filename="richlist.png")
        await interaction.followup.send(file=file, view=view)
