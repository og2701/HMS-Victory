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

def trim(im):
    bg = Image.new(im.mode, im.size, im.getpixel((0, 0)))
    diff = ImageChops.difference(im, bg)
    diff = ImageChops.add(diff, diff, 2.0, -100)
    bbox = diff.getbbox()
    if bbox:
        return im.crop(bbox)
    return im

class LeaderboardView(discord.ui.View):
    def __init__(self, xp_system, guild, sorted_data):
        super().__init__(timeout=None)
        self.xp_system = xp_system
        self.guild = guild
        self.sorted_data = sorted_data
        self.offset = 0
        self.previous_button.disabled = self.offset == 0
        self.next_button.disabled = self.offset + 30 >= len(self.sorted_data)

    def get_slice(self):
        return self.sorted_data[self.offset:self.offset+30]

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.blurple)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.offset = max(0, self.offset - 30)
        button.disabled = self.offset == 0
        self.next_button.disabled = self.offset + 30 >= len(self.sorted_data)
        file = await self.xp_system.generate_leaderboard_image(self.guild, self.get_slice())
        await interaction.response.edit_message(
            content=f"Showing ranks {self.offset+1}–{min(self.offset+30,len(self.sorted_data))} of {len(self.sorted_data)}",
            attachments=[file],
            view=self
        )

    @discord.ui.button(label="Next", style=discord.ButtonStyle.blurple)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.offset = min(len(self.sorted_data) - 30, self.offset + 30)
        self.previous_button.disabled = self.offset == 0
        button.disabled = self.offset + 30 >= len(self.sorted_data)
        file = await self.xp_system.generate_leaderboard_image(self.guild, self.get_slice())
        await interaction.response.edit_message(
            content=f"Showing ranks {self.offset+1}–{min(self.offset+30,len(self.sorted_data))} of {len(self.sorted_data)}",
            attachments=[file],
            view=self
        )

class XPSystem:
    def __init__(self):
        self.xp_data = {}
        self.load_data()
        self.last_xp_time = {}

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
                    role_ids = [rid for _, rid in CHAT_LEVEL_ROLE_THRESHOLDS]
                    roles_to_remove = [r for r in message.author.roles if r.id in role_ids]
                    if roles_to_remove:
                        pass
                        # await message.author.remove_roles(*roles_to_remove)
                    if new_role not in message.author.roles:
                        pass
                        # await message.author.add_roles(new_role)

            self.save_data()

    def get_rank(self, user_id: str):
        sorted_xp = sorted(self.xp_data.items(), key=lambda x: x[1], reverse=True)
        for i, (uid, score) in enumerate(sorted_xp, start=1):
            if uid == user_id:
                return i, score
        return None, 0

    def get_all_sorted_xp(self):
        return sorted(self.xp_data.items(), key=lambda x: x[1], reverse=True)

    async def generate_leaderboard_image(self, guild: discord.Guild, data_slice):
        with open("templates/leaderboard.html", "r", encoding="utf-8") as f:
            html_template = f.read()
        rows_html = ""
        for user_id, xp in data_slice:
            member = guild.get_member(int(user_id))
            if member:
                display_name = member.display_name
                avatar_url = member.avatar.url if member.avatar else member.default_avatar.url
            else:
                display_name = "Unknown"
                avatar_url = "https://cdn.discordapp.com/embed/avatars/0.png"
            rows_html += f"""
            <div class="flex items-center p-2 bg-gray-800 bg-opacity-70 rounded-lg mb-2">
              <div class="w-12 h-12 rounded-full overflow-hidden">
                <img src="{avatar_url}" class="w-full h-full object-cover" />
              </div>
              <div class="ml-3">
                <p class="text-sm font-bold">{display_name}</p>
                <p class="text-xs text-gray-300">XP: {xp}</p>
              </div>
            </div>
            """
        html_content = html_template.replace("{{ LEADERBOARD_ROWS }}", rows_html)
        output_path = f"{uuid.uuid4()}.png"
        hti.screenshot(html_str=html_content, save_as=output_path, size=(800, 1000))
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
        data_slice = sorted_data[:30]
        file = await self.generate_leaderboard_image(interaction.guild, data_slice)
        await interaction.response.send_message(
            content=f"Showing ranks 1–{min(30, len(sorted_data))} of {len(sorted_data)}",
            file=file,
            view=view
        )
