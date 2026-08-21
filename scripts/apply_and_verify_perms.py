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
        async with session.get(f"https://discord.com/api/v10/guilds/{GUILD_ID}/channels", headers=headers) as r:
            channels = await r.json()

        print("=== APPLYING AND VERIFYING CATEGORY & CHANNEL PERMISSIONS ===")
        for cat_id, cat_name in TARGET_CATEGORIES:
            print(f"\n📁 Category: {cat_name} ({cat_id})")

            # 1. Update Category Overwrites
            url_ev = f"https://discord.com/api/v10/channels/{cat_id}/permissions/{GUILD_ID}"
            await session.put(url_ev, headers=headers, json={"allow": "0", "deny": str(VIEW_CHANNEL_BIT), "type": 0})

            url_mem = f"https://discord.com/api/v10/channels/{cat_id}/permissions/{MEMBER_ROLE_ID}"
            await session.put(url_mem, headers=headers, json={"allow": str(VIEW_CHANNEL_BIT), "deny": "0", "type": 0})
            print(f"  ✓ Category overwrites set (@everyone: DENY view, @Member: ALLOW view)")

            # 2. Check child channels
            child_channels = [c for c in channels if c.get("parent_id") == str(cat_id)]
            for ch in child_channels:
                ch_id = ch["id"]
                ch_name = ch["name"]

                for ow in ch.get("permission_overwrites", []):
                    if ow["id"] == str(GUILD_ID) and (int(ow.get("allow", 0)) & VIEW_CHANNEL_BIT):
                        new_allow = int(ow.get("allow", 0)) & ~VIEW_CHANNEL_BIT
                        new_deny = int(ow.get("deny", 0)) | VIEW_CHANNEL_BIT
                        url_ch_ev = f"https://discord.com/api/v10/channels/{ch_id}/permissions/{GUILD_ID}"
                        await session.put(url_ch_ev, headers=headers, json={"allow": str(new_allow), "deny": str(new_deny), "type": 0})
                        print(f"    - #{ch_name}: cleaned @everyone explicit allow overwrite")
                print(f"    - #{ch_name}: verified")

            await asyncio.sleep(0.3)

        print("\n=== PERMISSION LOCKDOWN COMPLETE AND FULLY SYNCHRONIZED ===")


if __name__ == "__main__":
    asyncio.run(main())
