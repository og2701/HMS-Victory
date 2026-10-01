import discord
import time
import random
import io
import asyncio
import logging
from database import DatabaseManager
from config import *
from lib.core.constants import CHAT_LEVEL_ROLE_THRESHOLDS
from lib.economy.economy_manager import get_bb, add_bb
from lib.economy.bank_manager import BankManager

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

    async def _get_or_generate_image(self):
        next_off = self.offset + self.PAGE_SIZE
        if next_off < len(self.sorted_data) and next_off not in self.image_cache:
            self.image_cache[next_off] = asyncio.create_task(
                self.xp_system.generate_leaderboard_image(self.guild, self.sorted_data, next_off)
            )

        prev_off = self.offset - self.PAGE_SIZE
        if prev_off >= 0 and prev_off not in self.image_cache:
            self.image_cache[prev_off] = asyncio.create_task(
                self.xp_system.generate_leaderboard_image(self.guild, self.sorted_data, prev_off)
            )

        if self.offset not in self.image_cache:
            self.image_cache[self.offset] = await self.xp_system.generate_leaderboard_image(
                self.guild, self.sorted_data, self.offset
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
                    self.guild, self.sorted_data, self.offset
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

    async def _get_or_generate_image(self):
        next_off = self.offset + self.PAGE_SIZE
        if next_off < len(self.sorted_data) and next_off not in self.image_cache:
            self.image_cache[next_off] = asyncio.create_task(
                self.xp_system.generate_richlist_image(self.guild, self.sorted_data, next_off)
            )

        prev_off = self.offset - self.PAGE_SIZE
        if prev_off >= 0 and prev_off not in self.image_cache:
            self.image_cache[prev_off] = asyncio.create_task(
                self.xp_system.generate_richlist_image(self.guild, self.sorted_data, prev_off)
            )

        if self.offset not in self.image_cache:
            self.image_cache[self.offset] = await self.xp_system.generate_richlist_image(
                self.guild, self.sorted_data, self.offset
            )
        elif isinstance(self.image_cache[self.offset], asyncio.Task):
            try:
                self.image_cache[self.offset] = await self.image_cache[self.offset]
            except Exception as e:
                logger.warning(f"Cached richlist render at offset {self.offset} failed, regenerating: {e}")
                self.image_cache.pop(self.offset, None)
                self.image_cache[self.offset] = await self.xp_system.generate_richlist_image(
                    self.guild, self.sorted_data, self.offset
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
            #
            # These are funded by the demurrage dividend pot: the weekly charge on hoarded
            # balances is earmarked here, so money reclaimed from the richest players pays
            # the most active non-wealthy ones. An empty pot means no chat rewards until
            # the next demurrage run refills it, and a thin pot slows the rate rather than
            # stopping it dead.
            last_ukp = self._last_ukp_award.get(user_id, 0)
            if (now - last_ukp) >= self.UKP_COOLDOWN:
                balance = get_bb(int(user_id))
                if balance >= 10000:
                    reward_chance = 0.0
                else:
                    reward_chance = 1.0 / (1.0 + balance / 500.0)
                try:
                    from lib.economy.reserve_policy import dividend_rate, spend_dividend
                    reward_chance *= dividend_rate()
                except Exception:
                    logger.error("dividend rate lookup failed; paying at the base rate",
                                 exc_info=True)
                    spend_dividend = None
                if reward_chance > 0 and random.random() < reward_chance:
                    # Claim from the pot BEFORE paying, so a dry pot can't be overdrawn by
                    # two messages landing at once.
                    if spend_dividend is None or spend_dividend(1):
                        add_bb(int(user_id), 1, reason="Chatting activity reward",
                               discretionary=True)
                        try:
                            from lib.features.income_badges import record_income_source, bump_daily_income
                            bump_daily_income("chat_activity_total", 1)
                            await record_income_source(self.client, int(user_id), "chat")
                        except Exception:
                            pass
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

    async def generate_leaderboard_image(self, guild: discord.Guild, sorted_data, offset):
        """Render one 20-entry page of the XP leaderboard (page one opens with a podium)."""
        from lib.features.leaderboard_cards import render_xp_page
        return await render_xp_page(self.client, guild, sorted_data, offset)

    async def handle_leaderboard_command(self, interaction: discord.Interaction):
        data = self.get_all_sorted_xp(interaction.guild)
        if not data:
            return await interaction.followup.send("No XP data found.")
        view = LeaderboardView(self, interaction.guild, data)
        image_bytes = await self.generate_leaderboard_image(interaction.guild, data, 0)
        view.image_cache[0] = image_bytes

        file = discord.File(fp=io.BytesIO(image_bytes), filename="leaderboard.png")
        await interaction.followup.send(file=file, view=view)

    def get_all_balances(self):
        # The bot/bank (BOT_ID) is the house, not a player - keep it off the ranked list
        # (it's shown separately as a header on the richlist).
        from config import BOT_ID
        # Empty wallets are left off so the page count matches the holder count on the card.
        balances = DatabaseManager.fetch_all(
            "SELECT user_id, balance FROM ukpence WHERE user_id != ? AND balance > 0 ORDER BY balance DESC",
            (str(BOT_ID),))
        return balances

    async def generate_richlist_image(self, guild, sorted_data, offset):
        """Render one 20-entry page of the UKPence rich list (the bank is shown, not ranked)."""
        from lib.features.leaderboard_cards import render_rich_page
        return await render_rich_page(self.client, guild, sorted_data, offset)

    async def handle_richlist_command(self, interaction: discord.Interaction):
        data = self.get_all_balances()
        if not data:
            return await interaction.followup.send("No UKPence data found.")
        view = RichListView(self, interaction.guild, data)
        image_bytes = await self.generate_richlist_image(interaction.guild, data, 0)
        view.image_cache[0] = image_bytes

        file = discord.File(fp=io.BytesIO(image_bytes), filename="richlist.png")
        await interaction.followup.send(file=file, view=view)
