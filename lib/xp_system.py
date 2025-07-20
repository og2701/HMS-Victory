from config import CHROME_PATH
import discord
import os
import io
import uuid
import time
import random
import json
from PIL import Image, ImageChops
from html2image import Html2Image
from config import *
from config import *
from lib.ukpence import get_bb, _load

hti = Html2Image(output_path=".", browser_executable=CHROME_PATH)

def load_json(filename):
    if os.path.exists(filename):
        with open(filename, "r") as f:
            return json.load(f)
    return {}

def save_json(filename, data):
    with open(filename, "w") as f:
        json.dump(data, f, indent=4)

def trim(im: Image.Image) -> Image.Image:
    bg = Image.new(im.mode, im.size, im.getpixel((0, 0)))
    diff = ImageChops.difference(im, bg)
    diff = ImageChops.add(diff, diff, 2.0, -100)
    bbox = diff.getbbox()
    return im.crop(bbox) if bbox else im

class LeaderboardView(discord.ui.View):
    PAGE_SIZE = 20

    def __init__(self, xp_system, guild, sorted_data):
        super().__init__(timeout=None)
        self.xp_system = xp_system
        self.guild = guild
        self.sorted_data = sorted_data
        self.offset = 0
        self.previous_button.disabled = True
        self.next_button.disabled = (len(self.sorted_data) <= self.PAGE_SIZE)

    def get_slice(self):
        return self.sorted_data[self.offset : self.offset + self.PAGE_SIZE]

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.blurple)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.offset = max(0, self.offset - self.PAGE_SIZE)
        file = await self.xp_system.generate_leaderboard_image(
            self.guild, self.get_slice(), self.offset
        )
        self.previous_button.disabled = (self.offset == 0)
        self.next_button.disabled = (self.offset + self.PAGE_SIZE >= len(self.sorted_data))
        await interaction.response.edit_message(attachments=[file], view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.blurple)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        max_off = max(0, len(self.sorted_data) - self.PAGE_SIZE)
        self.offset = min(max_off, self.offset + self.PAGE_SIZE)
        file = await self.xp_system.generate_leaderboard_image(
            self.guild, self.get_slice(), self.offset
        )
        self.previous_button.disabled = (self.offset == 0)
        self.next_button.disabled = (self.offset + self.PAGE_SIZE >= len(self.sorted_data))
        await interaction.response.edit_message(attachments=[file], view=self)

class RichListView(discord.ui.View):
    PAGE_SIZE = 20

    def __init__(self, xp_system, guild, sorted_data):
        super().__init__(timeout=None)
        self.xp_system = xp_system
        self.guild = guild
        self.sorted_data = sorted_data
        self.offset = 0
        self.previous_button.disabled = True
        self.next_button.disabled = (len(self.sorted_data) <= self.PAGE_SIZE)

    def get_slice(self):
        return self.sorted_data[self.offset : self.offset + self.PAGE_SIZE]

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.blurple)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.offset = max(0, self.offset - self.PAGE_SIZE)
        file = await self.xp_system.generate_richlist_image(
            self.guild, self.get_slice(), self.offset
        )
        self.previous_button.disabled = (self.offset == 0)
        self.next_button.disabled = (self.offset + self.PAGE_SIZE >= len(self.sorted_data))
        await interaction.response.edit_message(attachments=[file], view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.blurple)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        max_off = max(0, len(self.sorted_data) - self.PAGE_SIZE)
        self.offset = min(max_off, self.offset + self.PAGE_SIZE)
        file = await self.xp_system.generate_richlist_image(
            self.guild, self.get_slice(), self.offset
        )
        self.previous_button.disabled = (self.offset == 0)
        self.next_button.disabled = (self.offset + self.PAGE_SIZE >= len(self.sorted_data))
        await interaction.response.edit_message(attachments=[file], view=self)

class XPSystem:
    def __init__(self):
        self.xp_data = {}
        self.last_xp_time = {}
        self.load_data()

    def load_data(self):
        data = load_json(XP_FILE)
        if "rankings" in data:
            self.xp_data = {e["user_id"]: e["score"] for e in data["rankings"]}
        else:
            self.xp_data = {}

    def save_data(self):
        rankings = []
        for i, (uid, score) in enumerate(sorted(self.xp_data.items(), key=lambda x: x[1], reverse=True), start=1):
            rankings.append({"rank": i, "score": score, "user_id": uid})
        save_json(XP_FILE, {"rankings": rankings})

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
        if user_id not in self.last_xp_time or (now - self.last_xp_time[user_id]) >= 120:
            gain = random.randint(10, 20)
            self.xp_data[user_id] = self.xp_data.get(user_id, 0) + gain
            self.last_xp_time[user_id] = now
            new_role_id = self.get_role_for_xp(self.xp_data[user_id])
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
            self.save_data()

    def get_rank(self, user_id: str):
        sorted_xp = sorted(self.xp_data.items(), key=lambda x: x[1], reverse=True)
        for i, (uid, score) in enumerate(sorted_xp, start=1):
            if uid == user_id:
                return i, score
        return None, 0

    def get_all_sorted_xp(self):
        return sorted(self.xp_data.items(), key=lambda x: x[1], reverse=True)

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
        path = f"{uuid.uuid4()}.png"
        hti.screenshot(html_str=final_html, save_as=path, size=(1200, 1200))

        img = Image.open(path)
        img = trim(img)
        img.save(path)

        buf = io.BytesIO(open(path, "rb").read())
        os.remove(path)
        return discord.File(fp=buf, filename="leaderboard.png")

    async def handle_leaderboard_command(self, interaction: discord.Interaction):
        data = self.get_all_sorted_xp()
        if not data:
            return await interaction.followup.send("No XP data found.")
        view = LeaderboardView(self, interaction.guild, data)
        first = data[: LeaderboardView.PAGE_SIZE]
        file = await self.generate_leaderboard_image(interaction.guild, first, 0)
        await interaction.followup.send(file=file, view=view)

    def get_all_balances(self):
        data = _load()
        return sorted(data.items(), key=lambda x: x[1], reverse=True)

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
        path = f"{uuid.uuid4()}.png"
        hti.screenshot(html_str=final_html, save_as=path, size=(1200, 1200))

        img = Image.open(path)
        img = trim(img)
        img.save(path)

        buf = io.BytesIO(open(path, "rb").read())
        os.remove(path)
        return discord.File(fp=buf, filename="richlist.png")

    async def handle_richlist_command(self, interaction: discord.Interaction):
        data = self.get_all_balances()
        if not data:
            return await interaction.followup.send("No UKPence data found.")
        view = RichListView(self, interaction.guild, data)
        first = data[: RichListView.PAGE_SIZE]
        file = await self.generate_richlist_image(interaction.guild, first, 0)
        await interaction.followup.send(file=file, view=view)
