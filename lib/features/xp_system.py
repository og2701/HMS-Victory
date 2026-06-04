import discord
import time
import random
import io
import asyncio
import logging
from database import DatabaseManager
from config import *
from lib.core.constants import CHAT_LEVEL_ROLE_THRESHOLDS, CUSTOM_RANK_BACKGROUNDS
from lib.economy.economy_manager import get_bb, add_bb
from lib.economy.bank_manager import BankManager
from lib.core.image_processing import screenshot_html, get_avatar_data_uri, encode_image_to_data_uri
import os
from lib.core.file_operations import read_html_template

logger = logging.getLogger(__name__)

# Channels where chatting earns no XP/UKP (keeps the rank ladder meaningful).
XP_EXCLUDED_CHANNELS = {CHANNELS.BOT_SPAM}

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
            try:
                self.image_cache[self.offset] = await self.image_cache[self.offset]
            except Exception as e:
                # A prefetch render failed (e.g. Chrome OOM). Drop the failed task so
                # this page isn't permanently broken, and regenerate inline.
                logger.warning(f"Cached leaderboard render at offset {self.offset} failed, regenerating: {e}")
                self.image_cache.pop(self.offset, None)
                self.image_cache[self.offset] = await self.xp_system.generate_leaderboard_image(
                    self.guild, self.get_slice(), self.offset
                )

        return discord.File(fp=io.BytesIO(self.image_cache[self.offset]), filename="leaderboard.png")

    async def _turn_page(self, interaction: discord.Interaction, new_offset: int):
        """Ack, render the target page and swap it in - tolerant of an interaction that
        already expired (10062) under event-loop congestion, so a slow ack never spams the
        logs with an unhandled view exception."""
        try:
            if not interaction.response.is_done():
                await interaction.response.defer()
        except discord.NotFound:
            # Interaction expired before we could ack it (loop was busy >3s). Nothing more
            # we can do with this click - drop it quietly rather than raising.
            logger.debug("Page turn dropped: interaction expired before defer")
            return
        except discord.HTTPException as e:
            logger.warning(f"Pagination defer failed: {e}")
            return

        self.offset = max(0, min(new_offset, max(0, len(self.sorted_data) - self.PAGE_SIZE)))
        self.previous_button.disabled = (self.offset == 0)
        self.next_button.disabled = (self.offset + self.PAGE_SIZE >= len(self.sorted_data))
        try:
            file = await self._get_or_generate_image()
            await interaction.edit_original_response(attachments=[file], view=self)
        except discord.NotFound:
            logger.debug("Page turn dropped: interaction gone before edit")
        except discord.HTTPException as e:
            logger.warning(f"Pagination edit failed: {e}")

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.blurple)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._turn_page(interaction, self.offset - self.PAGE_SIZE)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.blurple)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._turn_page(interaction, self.offset + self.PAGE_SIZE)

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
            try:
                self.image_cache[self.offset] = await self.image_cache[self.offset]
            except Exception as e:
                logger.warning(f"Cached richlist render at offset {self.offset} failed, regenerating: {e}")
                self.image_cache.pop(self.offset, None)
                self.image_cache[self.offset] = await self.xp_system.generate_richlist_image(
                    self.guild, self.get_slice(), self.offset
                )

        return discord.File(fp=io.BytesIO(self.image_cache[self.offset]), filename="richlist.png")

    async def _turn_page(self, interaction: discord.Interaction, new_offset: int):
        """Ack, render the target page and swap it in - tolerant of an interaction that
        already expired (10062) under event-loop congestion, so a slow ack never spams the
        logs with an unhandled view exception."""
        try:
            if not interaction.response.is_done():
                await interaction.response.defer()
        except discord.NotFound:
            # Interaction expired before we could ack it (loop was busy >3s). Nothing more
            # we can do with this click - drop it quietly rather than raising.
            logger.debug("Page turn dropped: interaction expired before defer")
            return
        except discord.HTTPException as e:
            logger.warning(f"Pagination defer failed: {e}")
            return

        self.offset = max(0, min(new_offset, max(0, len(self.sorted_data) - self.PAGE_SIZE)))
        self.previous_button.disabled = (self.offset == 0)
        self.next_button.disabled = (self.offset + self.PAGE_SIZE >= len(self.sorted_data))
        try:
            file = await self._get_or_generate_image()
            await interaction.edit_original_response(attachments=[file], view=self)
        except discord.NotFound:
            logger.debug("Page turn dropped: interaction gone before edit")
        except discord.HTTPException as e:
            logger.warning(f"Pagination edit failed: {e}")

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.blurple)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._turn_page(interaction, self.offset - self.PAGE_SIZE)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.blurple)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._turn_page(interaction, self.offset + self.PAGE_SIZE)

class XPSystem:
    UKP_COOLDOWN = 600  # 10 minutes between UKP chat rewards

    def __init__(self, client=None):
        self.client = client
        self._last_ukp_award: dict[str, float] = {}  # user_id -> timestamp

    def get_role_for_xp(self, xp):
        role_id = None
        for threshold, rid in CHAT_LEVEL_ROLE_THRESHOLDS:
            if xp >= threshold:
                role_id = rid
            else:
                break
        return role_id

    async def update_xp(self, message: discord.Message):
        # Don't reward system messages (joins/boosts/pins), near-empty one-character
        # messages, or chatter in excluded channels - all of which just pollute the
        # rank ladder. Image/sticker-only posts are still genuine activity, so allow them.
        if message.type not in (discord.MessageType.default, discord.MessageType.reply):
            return
        if message.channel.id in XP_EXCLUDED_CHANNELS:
            return
        if len(message.content.strip()) < 2 and not message.attachments and not message.stickers:
            return

        user_id = str(message.author.id)
        now = time.time()

        result = DatabaseManager.fetch_one("SELECT xp, last_xp_time FROM xp WHERE user_id = ?", (user_id,))
        
        current_xp = result[0] if result else 0
        last_xp_time = result[1] if result else 0

        if (now - last_xp_time) >= 120:
            gain = random.randint(10, 20)
            new_xp = current_xp + gain
            DatabaseManager.execute("INSERT OR REPLACE INTO xp (user_id, xp, last_xp_time) VALUES (?, ?, ?)", (user_id, new_xp, now))
            
            # Award UKP on a separate 10-min cooldown with wealth-based scaling.
            # Probability tapers to 0 at 10k UKP balance: rich users earn nothing from chat.
            # 0=100%, 500=50%, 1000=33%, 2000=20%, 5000≈9%, 9999≈5%, 10000+=0%
            last_ukp = self._last_ukp_award.get(user_id, 0)
            if (now - last_ukp) >= self.UKP_COOLDOWN:
                balance = get_bb(int(user_id))
                if balance >= 10000:
                    reward_chance = 0.0
                else:
                    reward_chance = 1.0 / (1.0 + balance / 500.0)
                if reward_chance > 0 and random.random() < reward_chance:
                    add_bb(int(user_id), 1, reason="Chatting activity reward")
                self._last_ukp_award[user_id] = now

            new_role_id = self.get_role_for_xp(new_xp)
            if new_role_id:
                guild = message.guild
                new_role = guild.get_role(new_role_id)
                if new_role:
                    rank_ids = [rid for _, rid in CHAT_LEVEL_ROLE_THRESHOLDS]
                    old_roles = [r for r in message.author.roles if r.id in rank_ids]
                    if new_role not in message.author.roles:
                        # Add the NEW role first, then remove superseded ones, so a
                        # permission error can never leave the member rankless (and
                        # re-failing every message). Both ops are guarded.
                        try:
                            await message.author.add_roles(new_role)
                        except (discord.Forbidden, discord.HTTPException) as e:
                            logger.warning(f"Failed to grant rank role {new_role.id} to {message.author.id}: {e}")
                            return
                        if old_roles:
                            try:
                                await message.author.remove_roles(*old_roles)
                            except (discord.Forbidden, discord.HTTPException) as e:
                                logger.warning(f"Failed to remove old rank roles from {message.author.id}: {e}")
                        embed = discord.Embed(
                            description=f"{message.author.mention} has progressed to **{new_role.name}**!",
                            color=discord.Color.green()
                        )
                        await message.channel.send(embed=embed)

    def get_rank(self, user_id: str, guild: discord.Guild = None):
        if guild:
            all_xp = self.get_all_sorted_xp(guild)
            for index, (uid, xp_val) in enumerate(all_xp):
                if str(uid) == str(user_id):
                    return index + 1, xp_val
            result = DatabaseManager.fetch_one("SELECT xp FROM xp WHERE user_id = ?", (user_id,))
            if result:
                return None, result[0]
            return None, 0
        
        # Optimized SQL query to find rank: count users with more XP + 1 (includes departed)
        query = """
            SELECT 
                (SELECT COUNT(*) + 1 FROM xp WHERE xp > (SELECT xp FROM xp WHERE user_id = ?)),
                (SELECT xp FROM xp WHERE user_id = ?)
        """
        result = DatabaseManager.fetch_one(query, (user_id, user_id))
        
        if result and result[1] is not None:
            return result[0], result[1]
        return None, 0

    def get_all_sorted_xp(self, guild: discord.Guild = None):
        sorted_xp = DatabaseManager.fetch_all("SELECT user_id, xp FROM xp ORDER BY xp DESC")
        if guild:
            member_ids = {m.id for m in guild.members}
            sorted_xp = [(uid, xp) for uid, xp in sorted_xp if int(uid) in member_ids]
        return sorted_xp

    async def generate_leaderboard_image(self, guild: discord.Guild, data_slice, offset):
        template = read_html_template("templates/leaderboard.html")

        left_html, right_html = "", ""
        half = len(data_slice) // 2

        # Pre-fetch missing members in bulk to improve performance on t3.micro
        missing_uids = [uid for uid, _ in data_slice if guild.get_member(int(uid)) is None]
        if missing_uids:
            # We use query_members to fetch several at once if they are missing from cache
            try:
                await guild.query_members(user_ids=[int(uid) for uid in missing_uids], cache=True)
            except:
                pass

        # Encode title banner texture
        title_banner_path = os.path.join(BASE_DIR, "data", "rank_cards", "title_banner_texture.png")
        title_bg_uri = ""
        if os.path.exists(title_banner_path):
            title_bg_uri = encode_image_to_data_uri(title_banner_path)

        user_ids = [str(uid) for uid, _ in data_slice]
        customizations = {}
        if user_ids:
            placeholders = ','.join('?' * len(user_ids))
            query = f"SELECT user_id, title, background FROM user_rank_customization WHERE user_id IN ({placeholders})"
            results = DatabaseManager.fetch_all(query, tuple(user_ids))
            customizations = {row[0]: {'title': row[1], 'background': row[2]} for row in results}

        for i, (uid, xp_val) in enumerate(data_slice):
            rank = offset + i + 1
            member = guild.get_member(int(uid))
            name = member.display_name if member else "Unknown"
            
            uid_str = str(uid)
            cust = customizations.get(uid_str, {})
            title = cust.get('title')
            db_bg = cust.get('background')
            
            bg_file = CUSTOM_RANK_BACKGROUNDS.get(uid_str)
            if db_bg:
                bg_file = db_bg
                
            has_custom_bg = bg_file is not None and bg_file != "unionjack.png"
            
            avatar_url = member.display_avatar.url if member else "https://cdn.discordapp.com/embed/avatars/0.png"
            avatar = await get_avatar_data_uri(self.client, avatar_url)

            # Determine rank class for specific styling (Gold, Silver, Bronze for top 3)
            rank_class = f"rank-{rank}" if rank <= 3 else ""
            
            box_style = ""
            if has_custom_bg:
                bg_path = os.path.join(BASE_DIR, "data", "rank_cards", bg_file)
                if os.path.exists(bg_path):
                    bg_uri = encode_image_to_data_uri(bg_path)
                    box_style = f"background: linear-gradient(90deg, rgba(0,0,0,0.95) 0%, rgba(0,0,0,0.4) 50%, rgba(0,0,0,0.8) 100%), url('{bg_uri}') no-repeat center center; background-size: cover; border: 1px solid rgba(255,255,255,0.3);"
            elif title and title_bg_uri:
                box_style = f"background: url('{title_bg_uri}') no-repeat center center; background-size: cover; border: 1px solid #D4AF37;"

            title_html = f'<div class="user-title">{title}</div>' if title else ""

            block = f"""
            <div class="leaderboard-item {rank_class}" style="{box_style}">
              <div class="rank-badge">#{rank}</div>
              <div class="avatar-container">
                <img src="{avatar}" class="avatar" />
              </div>
              <div class="user-info">
                <div class="user-name">{name}</div>
                {title_html}
                <div class="user-stats">XP: <span class="stat-highlight">{xp_val:,}</span></div>
              </div>
            </div>
            """
            if i < half:
                left_html += block
            else:
                right_html += block

        two_col = f"""
        <div class="flex gap-4 justify-center w-full">
          <div class="flex flex-col gap-2 w-full max-w-[380px]">{left_html}</div>
          <div class="flex flex-col gap-2 w-full max-w-[380px]">{right_html}</div>
        </div>
        """
        final_html = template.replace("{{ LEADERBOARD_ROWS }}", two_col).replace("{{ TITLE }}", "HMS Victory XP Leaderboard")
        image_buffer = await screenshot_html(final_html, size=(1000, 1400))
        return image_buffer.getvalue()

    async def handle_leaderboard_command(self, interaction: discord.Interaction):
        data = self.get_all_sorted_xp(interaction.guild)
        if not data:
            return await interaction.followup.send("No XP data found.")
        view = LeaderboardView(self, interaction.guild, data)
        first = data[: LeaderboardView.PAGE_SIZE]
        image_bytes = await self.generate_leaderboard_image(interaction.guild, first, 0)
        view.image_cache[0] = image_bytes

        file = discord.File(fp=io.BytesIO(image_bytes), filename="leaderboard.png")
        await interaction.followup.send(file=file, view=view)

    def get_all_balances(self):
        # The bot/bank (BOT_ID) is the house, not a player - keep it off the ranked list
        # (it's shown separately as a header on the richlist).
        from config import BOT_ID
        balances = DatabaseManager.fetch_all(
            "SELECT user_id, balance FROM ukpence WHERE user_id != ? ORDER BY balance DESC",
            (str(BOT_ID),))
        return balances

    async def generate_richlist_image(self, guild, data_slice, offset):
        template = read_html_template("templates/leaderboard.html")

        left_html, right_html = "", ""
        half = RichListView.PAGE_SIZE // 2

        # Pre-fetch missing members in bulk
        missing_uids = [uid for uid, _ in data_slice if guild.get_member(int(uid)) is None]
        if missing_uids:
            try:
                await guild.query_members(user_ids=[int(uid) for uid in missing_uids], cache=True)
            except:
                pass

        # Encode title banner texture
        title_banner_path = os.path.join(BASE_DIR, "data", "rank_cards", "title_banner_texture.png")
        title_bg_uri = ""
        if os.path.exists(title_banner_path):
            title_bg_uri = encode_image_to_data_uri(title_banner_path)

        user_ids = [str(uid) for uid, _ in data_slice]
        customizations = {}
        if user_ids:
            placeholders = ','.join('?' * len(user_ids))
            query = f"SELECT user_id, title, background FROM user_rank_customization WHERE user_id IN ({placeholders})"
            results = DatabaseManager.fetch_all(query, tuple(user_ids))
            customizations = {row[0]: {'title': row[1], 'background': row[2]} for row in results}

        for i, (uid, bal) in enumerate(data_slice):
            rank = offset + i + 1
            member = guild.get_member(int(uid))
            name = member.display_name if member else "Unknown"
            
            uid_str = str(uid)
            cust = customizations.get(uid_str, {})
            title = cust.get('title')
            db_bg = cust.get('background')
            
            bg_file = CUSTOM_RANK_BACKGROUNDS.get(uid_str)
            if db_bg:
                bg_file = db_bg
                
            has_custom_bg = bg_file is not None and bg_file != "unionjack.png"
            
            avatar_url = member.display_avatar.url if member else "https://cdn.discordapp.com/embed/avatars/0.png"
            avatar = await get_avatar_data_uri(self.client, avatar_url)

            # Determine rank class for specific styling
            rank_class = f"rank-{rank}" if rank <= 3 else ""
            
            box_style = ""
            if has_custom_bg:
                bg_path = os.path.join(BASE_DIR, "data", "rank_cards", bg_file)
                if os.path.exists(bg_path):
                    bg_uri = encode_image_to_data_uri(bg_path)
                    box_style = f"background: linear-gradient(90deg, rgba(0,0,0,0.95) 0%, rgba(0,0,0,0.4) 50%, rgba(0,0,0,0.8) 100%), url('{bg_uri}') no-repeat center center; background-size: cover; border: 1px solid rgba(255,255,255,0.3);"
            elif title and title_bg_uri:
                box_style = f"background: url('{title_bg_uri}') no-repeat center center; background-size: cover; border: 1px solid #D4AF37;"

            title_html = f'<div class="user-title">{title}</div>' if title else ""

            block = f"""
            <div class="leaderboard-item {rank_class}" style="{box_style}">
              <div class="rank-badge">#{rank}</div>
              <div class="avatar-container">
                <img src="{avatar}" class="avatar" />
              </div>
              <div class="user-info">
                <div class="user-name">{name}</div>
                {title_html}
                <div class="user-stats">UKPence: <span class="stat-highlight">{bal:,}</span></div>
              </div>
            </div>
            """
            if i < half:
                left_html += block
            else:
                right_html += block

        two_col = f"""
        <div class="flex gap-4 justify-center w-full">
          <div class="flex flex-col gap-2 w-full max-w-[380px]">{left_html}</div>
          <div class="flex flex-col gap-2 w-full max-w-[380px]">{right_html}</div>
        </div>
        """

        # House Bank header - the bot/bank isn't ranked among players, but its balance is
        # shown centred at the top of every page.
        from config import BOT_ID
        bank_row = DatabaseManager.fetch_one("SELECT balance FROM ukpence WHERE user_id = ?", (str(BOT_ID),))
        bank_bal = bank_row[0] if bank_row else 0
        bank_member = guild.get_member(int(BOT_ID))
        bank_avatar_url = bank_member.display_avatar.url if bank_member else "https://cdn.discordapp.com/embed/avatars/0.png"
        bank_avatar = await get_avatar_data_uri(self.client, bank_avatar_url)
        bank_html = f"""
        <div class="flex justify-center w-full" style="margin-bottom:18px">
          <div class="leaderboard-item" style="max-width:470px;width:100%;border:1.5px solid #D4AF37;box-shadow:0 0 22px rgba(212,175,55,.4);background:linear-gradient(90deg, rgba(0,0,0,.9), rgba(48,36,6,.7));">
            <div class="rank-badge" style="color:#D4AF37;font-size:22px">🏦</div>
            <div class="avatar-container"><img src="{bank_avatar}" class="avatar" /></div>
            <div class="user-info">
              <div class="user-name" style="color:#D4AF37">Victory Bank</div>
              <div class="user-stats">UKPence: <span class="stat-highlight">{bank_bal:,}</span></div>
            </div>
          </div>
        </div>
        """

        rows_html = f'<div class="flex flex-col w-full">{bank_html}{two_col}</div>'
        final_html = template.replace("{{ LEADERBOARD_ROWS }}", rows_html).replace("{{ TITLE }}", "HMS Victory UKPence Richlist")
        image_buffer = await screenshot_html(final_html, size=(1000, 1400))
        return image_buffer.getvalue()

    async def handle_richlist_command(self, interaction: discord.Interaction):
        data = self.get_all_balances()
        if not data:
            return await interaction.followup.send("No UKPence data found.")
        view = RichListView(self, interaction.guild, data)
        first = data[: RichListView.PAGE_SIZE]
        image_bytes = await self.generate_richlist_image(interaction.guild, first, 0)
        view.image_cache[0] = image_bytes

        file = discord.File(fp=io.BytesIO(image_bytes), filename="richlist.png")
        await interaction.followup.send(file=file, view=view)
