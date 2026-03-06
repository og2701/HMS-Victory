import sqlite3
import discord
from unittest.mock import MagicMock
import asyncio

class MockInteraction:
    def __init__(self, user_id):
        self.user = MagicMock()
        self.user.id = user_id
        self.response = MagicMock()

async def test():
    from database import init_db, DatabaseManager
    init_db()

    # Give user UKP
    user_id = 918873095325982750 # Owen's ID
    DatabaseManager.execute("INSERT OR REPLACE INTO ukpence (user_id, balance) VALUES (?, ?)", (str(user_id), 100))
    DatabaseManager.execute("INSERT OR IGNORE INTO xp (user_id, xp, last_xp_time) VALUES (?, ?, ?)", (str(user_id), 5000, 0))

if __name__ == "__main__":
    asyncio.run(test())
    print("Database testing complete.")
