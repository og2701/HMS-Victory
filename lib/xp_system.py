from config import CHROME_PATH
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
        return self.sorted_data[self.offset:self.offset + self.PAGE_SIZE]

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.blurple)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.offset = max(0, self.offset - self.PAGE_SIZE)
        file = await self.xp_system.generate_leaderboard_image(self.guild, self.get_slice(), self.offset)
        button.disabled = (self.offset == 0)
        self.next_button.disabled = (self.offset + self.PAGE_SIZE >= len(self.sorted_data))
        start = self.offset + 1
        end = min(self.offset + self.PAGE_SIZE, len(self.sorted_data))
        await interaction.response.edit_message(
            content=f"Showing ranks {start}–{end} of {len(self.sorted_data)}",
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
        start = self.offset + 1
        end = min(self.offset + self.PAGE_SIZE, len(self.sorted_data))
        await interaction.response.edit_message(
            content=f"Showing ranks {start}–{end} of {len(self.sorted_data)}",
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
            template = f.read()
        left_items = []
        right_items = []
        for i, (user_id, xp) in enumerate(data_slice):
            rank = offset + i + 1
            member = guild.get_member(int(user_id))
            if member:
                name = member.display_name
                avatar = member.avatar.url if member.avatar else member.default_avatar.url
            else:
                name = "Unknown"
                avatar = "https://cdn.discordapp.com/embed/avatars/0.png"
            if i < 10:
                left_items.append((rank, name, xp, avatar))
            else:
                right_items.append((rank, name, xp, avatar))
        left_html = ""
        for rank, name, xp_val, avatar_url in left_items:
            left_html += f"""
            <div style="display:flex; align-items:center; background:rgba(0,0,0,0.5); margin-bottom:8px; padding:8px; border-radius:8px;">
              <p style="margin-right:8px; font-weight:bold;">#{rank}</p>
              <div style="width:48px; height:48px; border-radius:9999px; overflow:hidden;">
                <img src="{avatar_url}" style="width:100%; height:100%; object-fit:cover;" />
              </div>
              <div style="margin-left:8px;">
                <p style="font-weight:bold;">{name}</p>
                <p style="color:#ccc; font-size:0.8rem;">XP: {xp_val}</p>
              </div>
            </div>
            """
        right_html = ""
        for rank, name, xp_val, avatar_url in right_items:
            right_html += f"""
            <div style="display:flex; align-items:center; background:rgba(0,0,0,0.5); margin-bottom:8px; padding:8px; border-radius:8px;">
              <p style="margin-right:8px; font-weight:bold;">#{rank}</p>
              <div style="width:48px; height:48px; border-radius:9999px; overflow:hidden;">
                <img src="{avatar_url}" style="width:100%; height:100%; object-fit:cover;" />
              </div>
              <div style="margin-left:8px;">
                <p style="font-weight:bold;">{name}</p>
                <p style="color:#ccc; font-size:0.8rem;">XP: {xp_val}</p>
              </div>
            </div>
            """
        final_html = f"""
        <div style="display:flex; gap:1.5rem;">
          <div style="display:flex; flex-direction:column;">{left_html}</div>
          <div style="display:flex; flex-direction:column;">{right_html}</div>
        </div>
        """
        html = template.replace("{{ LEADERBOARD_ROWS }}", final_html)
        path = f"{uuid.uuid4()}.png"
        hti.screenshot(html_str=html, save_as=path, size=(1600, 1000))
        image = Image.open(path)
        image = trim(image)
        image.save(path)
        with open(path, "rb") as f:
            img_bytes = io.BytesIO(f.read())
        os.remove(path)
        return discord.File(fp=img_bytes, filename="leaderboard.png")

    async def handle_leaderboard_command(self, interaction: discord.Interaction):
        all_data = self.get_all_sorted_xp()
        if not all_data:
            await interaction.response.send_message("No XP data found.")
            return
        view = LeaderboardView(self, interaction.guild, all_data)
        first_slice = all_data[:LeaderboardView.PAGE_SIZE]
        file = await self.generate_leaderboard_image(interaction.guild, first_slice, 0)
        total = len(all_data)
        showing = min(LeaderboardView.PAGE_SIZE, total)
        await interaction.response.send_message(
            content=f"Showing ranks 1–{showing} of {total}",
            file=file,
            view=view
        )
