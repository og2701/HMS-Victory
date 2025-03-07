import discord
import os
import io
import uuid
import time
import random
from PIL import Image, ImageChops
from html2image import Html2Image
from lib.utils import load_json, save_json
from lib.settings import *
from lib.rank_constants import *
from config import CHROME_PATH
hti = Html2Image(output_path=".", browser_executable=CHROME_PATH)

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
        self.previous_button.disabled = (self.offset == 0)
        self.next_button.disabled = (self.offset + self.PAGE_SIZE >= len(self.sorted_data))

    def get_slice(self):
        start = self.offset
        end = start + self.PAGE_SIZE
        return self.sorted_data[start:end]

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.blurple)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.offset = max(0, self.offset - self.PAGE_SIZE)
        file = await self.xp_system.generate_leaderboard_image(self.guild, self.get_slice(), self.offset)
        button.disabled = (self.offset == 0)
        self.next_button.disabled = (self.offset + self.PAGE_SIZE >= len(self.sorted_data))
        start_rank = self.offset + 1
        end_rank = min(self.offset + self.PAGE_SIZE, len(self.sorted_data))
        await interaction.response.edit_message(
            content=f"Showing ranks {start_rank}–{end_rank} of {len(self.sorted_data)}",
            attachments=[file],
            view=self
        )

    @discord.ui.button(label="Next", style=discord.ButtonStyle.blurple)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        max_offset = max(0, len(self.sorted_data) - self.PAGE_SIZE)
        self.offset = min(max_offset, self.offset + self.PAGE_SIZE)
        file = await self.xp_system.generate_leaderboard_image(self.guild, self.get_slice(), self.offset)
        button.disabled = (self.offset + self.PAGE_SIZE >= len(self.sorted_data))
        self.previous_button.disabled = (self.offset == 0)
        start_rank = self.offset + 1
        end_rank = min(self.offset + self.PAGE_SIZE, len(self.sorted_data))
        await interaction.response.edit_message(
            content=f"Showing ranks {start_rank}–{end_rank} of {len(self.sorted_data)}",
            attachments=[file],
            view=self
        )

class XPSystem:
    def __init__(self):
        self.xp_data = {}
        self.last_xp_time = {}
        self.load_data()

    def load_data(self):
        data = load_json(XP_FILE)
        if "rankings" in data:
            self.xp_data = {entry["user_id"]: entry["score"] for entry in data["rankings"]}
        else:
            self.xp_data = {}

    def save_data(self):
        rankings = []
        sorted_xp = sorted(self.xp_data.items(), key=lambda x: x[1], reverse=True)
        for i, (uid, score) in enumerate(sorted_xp, start=1):
            rankings.append({"rank": i, "score": score, "user_id": uid})
        data = {"rankings": rankings}
        save_json(XP_FILE, data)

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
            xp_gain = random.randint(10, 20)
            self.xp_data[user_id] = self.xp_data.get(user_id, 0) + xp_gain
            self.last_xp_time[user_id] = now
            new_role_id = self.get_role_for_xp(self.xp_data[user_id])
            if new_role_id:
                guild = message.guild
                new_role = guild.get_role(new_role_id)
                if new_role:
                    pass
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
            html_template = f.read()

        columns = [[] for _ in range(4)]
        for i, (user_id, xp) in enumerate(data_slice):
            col_index = i // 5
            rank = offset + (i + 1)
            member = guild.get_member(int(user_id))
            if member:
                display_name = member.display_name
                avatar_url = member.avatar.url if member.avatar else member.default_avatar.url
            else:
                display_name = "Unknown"
                avatar_url = "https://cdn.discordapp.com/embed/avatars/0.png"
            columns[col_index].append((rank, display_name, xp, avatar_url))

        # Instead of a grid, use a single row of four flex columns
        # so items won't overlap
        full_html = '<div class="flex justify-center space-x-8">'
        for col_list in columns:
            col_html = ""
            for rank, display_name, xp, avatar_url in col_list:
                col_html += f"""
                <div class="flex items-center p-2 bg-gray-800 bg-opacity-70 rounded-lg mb-2">
                  <p class="text-sm font-bold mr-3">#{rank}</p>
                  <div class="w-12 h-12 rounded-full overflow-hidden">
                    <img src="{avatar_url}" class="w-full h-full object-cover" />
                  </div>
                  <div class="ml-3">
                    <p class="text-sm font-bold">{display_name}</p>
                    <p class="text-xs text-gray-300">XP: {xp}</p>
                  </div>
                </div>
                """
            full_html += f'<div class="flex flex-col">{col_html}</div>'
        full_html += '</div>'

        html_content = html_template.replace("{{ LEADERBOARD_ROWS }}", full_html)

        output_path = f"{uuid.uuid4()}.png"
        hti.screenshot(html_str=html_content, save_as=output_path, size=(1400, 900))
        image = Image.open(output_path)
        image = trim(image)
        image.save(output_path)

        with open(output_path, "rb") as f:
            image_bytes = io.BytesIO(f.read())
        os.remove(output_path)
        return discord.File(fp=image_bytes, filename="leaderboard.png")

    async def handle_leaderboard_command(self, interaction: discord.Interaction):
        sorted_data = self.get_all_sorted_xp()
        if not sorted_data:
            await interaction.response.send_message("No XP data found.")
            return
        view = LeaderboardView(self, interaction.guild, sorted_data)
        first_slice = sorted_data[:LeaderboardView.PAGE_SIZE]
        file = await self.generate_leaderboard_image(interaction.guild, first_slice, 0)
        total_count = len(sorted_data)
        showing_count = min(LeaderboardView.PAGE_SIZE, total_count)
        await interaction.response.send_message(
            content=f"Showing ranks 1–{showing_count} of {total_count}",
            file=file,
            view=view
        )
