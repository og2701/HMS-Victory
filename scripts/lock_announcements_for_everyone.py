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
READ_HISTORY_BIT = 65536

CHANNELS_TO_LOCK = [
    (959503403199905862, "announcements"),
    (1133386861033832448, "minor-announcements"),
    (959848236384919692, "voting"),
    (1132279234858078239, "roles"),
    (959538521612369952, "current-template"),
    (960228871511351336, "suggestions"),
]


async def main():
    if not token:
        print("Error: DISCORD_TOKEN is missing")
        return

    async with aiohttp.ClientSession() as session:
        print("=== HIDING ANNOUNCEMENT CHANNELS FROM @everyone ===")
        for ch_id, ch_name in CHANNELS_TO_LOCK:
            print(f"\nLocking #{ch_name} ({ch_id})...")

            # 1. Deny @everyone View
            url_ev = f"https://discord.com/api/v10/channels/{ch_id}/permissions/{GUILD_ID}"
            payload_ev = {"allow": "0", "deny": str(VIEW_CHANNEL_BIT), "type": 0}
            async with session.put(url_ev, headers=headers, json=payload_ev) as r:
                print(f"  @everyone -> View: DENY (Status: {r.status})")

            # 2. Allow @Member View & Read History
            url_mem = f"https://discord.com/api/v10/channels/{ch_id}/permissions/{MEMBER_ROLE_ID}"
            payload_mem = {"allow": str(VIEW_CHANNEL_BIT | READ_HISTORY_BIT), "deny": "0", "type": 0}
            async with session.put(url_mem, headers=headers, json=payload_mem) as r:
                print(f"  @Member   -> View: ALLOW (Status: {r.status})")

            await asyncio.sleep(0.2)

        print("\nChecking exact visible channels for user with no roles (@everyone)...")
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
                visible_to_guest.append(ch_name)

        print("\n=== TOTAL CHANNELS VISIBLE TO A GUEST / UNVERIFIED USER ===")
        for name in visible_to_guest:
            print(f"- #{name}")


if __name__ == "__main__":
    asyncio.run(main())
