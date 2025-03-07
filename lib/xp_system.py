import discord
import time
import random
from lib.utils import load_json, save_json
from lib.settings import *
from lib.rank_constants import *

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
