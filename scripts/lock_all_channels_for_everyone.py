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
]


async def main():
    if not token:
        print("Error: DISCORD_TOKEN is missing")
        return

    async with aiohttp.ClientSession() as session:
        async with session.get(f"https://discord.com/api/v10/guilds/{GUILD_ID}/channels", headers=headers) as r:
            channels = await r.json()

        print("=== LOCKING ALL TARGET CHANNELS FOR @everyone ===")
        for cat_id, cat_name in TARGET_CATEGORIES:
            print(f"\n📁 Locking {cat_name} ({cat_id})...")

            # 1. Lock category
            url_cat = f"https://discord.com/api/v10/channels/{cat_id}/permissions/{GUILD_ID}"
            await session.put(url_cat, headers=headers, json={"allow": "0", "deny": str(VIEW_CHANNEL_BIT), "type": 0})

            # 2. Lock each child channel
            child_channels = [c for c in channels if c.get("parent_id") == str(cat_id)]
            for ch in child_channels:
                ch_id = ch["id"]
                ch_name = ch["name"]

                # Find existing @everyone overwrite
                ev_ow = next((ow for ow in ch.get("permission_overwrites", []) if ow["id"] == str(GUILD_ID)), None)
                cur_allow = int(ev_ow["allow"]) if ev_ow else 0
                cur_deny = int(ev_ow["deny"]) if ev_ow else 0

                new_allow = cur_allow & ~VIEW_CHANNEL_BIT
                new_deny = cur_deny | VIEW_CHANNEL_BIT

                url_ch = f"https://discord.com/api/v10/channels/{ch_id}/permissions/{GUILD_ID}"
                payload = {
                    "allow": str(new_allow),
                    "deny": str(new_deny),
                    "type": 0
                }
                async with session.put(url_ch, headers=headers, json=payload) as resp:
                    print(f"  - #{ch_name}: @everyone View DENIED (status {resp.status})")

                await asyncio.sleep(0.1)

        print("\n=== LOCKDOWN COMPLETE ===")


if __name__ == "__main__":
    asyncio.run(main())
