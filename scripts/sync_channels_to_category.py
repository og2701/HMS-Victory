import asyncio
import os
import aiohttp
from dotenv import load_dotenv

load_dotenv()
token = os.getenv("DISCORD_TOKEN")
headers = {"Authorization": f"Bot {token}"}
GUILD_ID = 959493056242008184
MEMBER_ROLE_ID = 1142491622563643442
VIEW_CHANNEL_BIT = 1024

TARGET_CAT_IDS = [
    "959544877325115424",   # General
    "1151876537012985886",  # gaming and bots
    "1140983791238778890",  # r/place
    "959493057076666379",   # Permanent VC
    "1143557841886662717",  # Temporary VC
]


async def main():
    if not token:
        print("Error: DISCORD_TOKEN is missing")
        return

    async with aiohttp.ClientSession() as session:
        async with session.get(f"https://discord.com/api/v10/guilds/{GUILD_ID}/channels", headers=headers) as r:
            channels = await r.json()

        print("=== SYNCING @everyone OVERWRITES TO INHERIT CATEGORY DENY ===")
        for ch in channels:
            parent_id = ch.get("parent_id")
            if parent_id in TARGET_CAT_IDS:
                ch_id = ch["id"]
                ch_name = ch["name"]
                url = f"https://discord.com/api/v10/channels/{ch_id}/permissions/{GUILD_ID}"
                async with session.delete(url, headers=headers) as resp:
                    print(f"#{ch_name}: synced to category (status {resp.status})")
                await asyncio.sleep(0.1)

        print("\nChecking exact visible channels for user with no roles (@everyone)...")
        # Re-fetch
        async with session.get(f"https://discord.com/api/v10/guilds/{GUILD_ID}/channels", headers=headers) as r:
            channels = await r.json()

        visible_to_guest = []
        for ch in channels:
            if ch["type"] == 4:
                continue
            ch_name = ch["name"]
            parent_id = ch.get("parent_id")
            parent_cat = next((c for c in channels if c["id"] == parent_id), None)

            ch_ev_ow = next((ow for ow in ch.get("permission_overwrites", []) if ow["id"] == str(GUILD_ID)), None)
            if ch_ev_ow:
                allow = int(ch_ev_ow.get("allow", 0))
                deny = int(ch_ev_ow.get("deny", 0))
                can_view = False if (deny & VIEW_CHANNEL_BIT) else (True if (allow & VIEW_CHANNEL_BIT) else True)
            elif parent_cat:
                cat_ev_ow = next((ow for ow in parent_cat.get("permission_overwrites", []) if ow["id"] == str(GUILD_ID)), None)
                if cat_ev_ow:
                    allow = int(cat_ev_ow.get("allow", 0))
                    deny = int(cat_ev_ow.get("deny", 0))
                    can_view = False if (deny & VIEW_CHANNEL_BIT) else True
                else:
                    can_view = True
            else:
                can_view = True

            if can_view:
                visible_to_guest.append((parent_cat.get("name", "Uncategorized") if parent_cat else "Uncategorized", ch_name))

        print("\n=== FINAL CHANNELS VISIBLE TO A USER WITH NO ROLES ===")
        for cat_name, ch_name in visible_to_guest:
            print(f"[{cat_name}] #{ch_name}")


if __name__ == "__main__":
    asyncio.run(main())
