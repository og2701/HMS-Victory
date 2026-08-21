import asyncio
import os
import aiohttp
from dotenv import load_dotenv

load_dotenv()
token = os.getenv("DISCORD_TOKEN")
headers = {"Authorization": f"Bot {token}"}
GUILD_ID = 959493056242008184
MEMBER_ROLE_ID = 1142491622563643442
VIEW_CHANNEL_BIT = 1024  # 1 << 10

TARGET_CATEGORIES = [
    (959544877325115424, "General"),
    (1151876537012985886, "gaming and bots"),
    (1140983791238778890, "r/place"),
    (959493057076666379, "Permanent VC"),
    (1143557841886662717, "Temporary VC - Make Your Own!"),
    (1139976595336069161, "support"),
]


async def main():
    if not token:
        print("Error: DISCORD_TOKEN is missing")
        return

    async with aiohttp.ClientSession() as session:
        print("Applying Category Permission Lockdown...")
        for cat_id, cat_name in TARGET_CATEGORIES:
            print(f"\nLocking Category: {cat_name} ({cat_id})...")

            # 1. Deny @everyone View Channel
            url_ev = f"https://discord.com/api/v10/channels/{cat_id}/permissions/{GUILD_ID}"
            payload_ev = {
                "allow": "0",
                "deny": str(VIEW_CHANNEL_BIT),
                "type": 0  # 0 for role
            }
            async with session.put(url_ev, headers=headers, json=payload_ev) as r:
                print(f"  @everyone -> View: DENY (Status: {r.status})")

            # 2. Allow @Member View Channel
            url_mem = f"https://discord.com/api/v10/channels/{cat_id}/permissions/{MEMBER_ROLE_ID}"
            payload_mem = {
                "allow": str(VIEW_CHANNEL_BIT),
                "deny": "0",
                "type": 0  # 0 for role
            }
            async with session.put(url_mem, headers=headers, json=payload_mem) as r:
                print(f"  @Member   -> View: ALLOW (Status: {r.status})")

            await asyncio.sleep(0.5)

        print("\nAll target categories successfully locked down!")


if __name__ == "__main__":
    asyncio.run(main())
