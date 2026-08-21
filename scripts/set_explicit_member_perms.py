import asyncio
import os
import aiohttp
from dotenv import load_dotenv

load_dotenv()
token = os.getenv("DISCORD_TOKEN")
headers = {"Authorization": f"Bot {token}"}
GUILD_ID = 959493056242008184
MEMBER_ROLE_ID = 1142491622563643442

# Common permission bitmasks
VIEW_CHANNEL = 1 << 10             # 1024
SEND_MESSAGES = 1 << 11            # 2048
EMBED_LINKS = 1 << 14              # 16384
ATTACH_FILES = 1 << 15             # 32768
READ_MESSAGE_HISTORY = 1 << 16     # 65536
USE_EXTERNAL_EMOJIS = 1 << 18      # 262144
ADD_REACTIONS = 1 << 6             # 64
CONNECT = 1 << 20                  # 1048576
SPEAK = 1 << 21                    # 2097152
STREAM = 1 << 9                    # 512

TEXT_ALLOW = (
    VIEW_CHANNEL
    | SEND_MESSAGES
    | EMBED_LINKS
    | ATTACH_FILES
    | READ_MESSAGE_HISTORY
    | USE_EXTERNAL_EMOJIS
    | ADD_REACTIONS
)

VOICE_ALLOW = (
    VIEW_CHANNEL
    | CONNECT
    | SPEAK
    | STREAM
    | SEND_MESSAGES
    | READ_MESSAGE_HISTORY
    | ADD_REACTIONS
)

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

        print("=== EXPLICITLY SETTING @Member PERMISSIONS ON EVERY CHANNEL ===")
        for cat_id, cat_name in TARGET_CATEGORIES:
            print(f"\n📁 Category: {cat_name} ({cat_id})")

            # Category overwrite for @Member
            url_mem_cat = f"https://discord.com/api/v10/channels/{cat_id}/permissions/{MEMBER_ROLE_ID}"
            cat_payload = {"allow": str(VIEW_CHANNEL | SEND_MESSAGES | CONNECT | SPEAK), "deny": "0", "type": 0}
            await session.put(url_mem_cat, headers=headers, json=cat_payload)

            child_channels = [c for c in channels if c.get("parent_id") == str(cat_id)]
            for ch in child_channels:
                ch_id = ch["id"]
                ch_name = ch["name"]
                ch_type = ch.get("type", 0)

                # Determine allow bitmask based on channel type
                allow_bits = VOICE_ALLOW if ch_type in (2, 13) else TEXT_ALLOW

                url_mem_ch = f"https://discord.com/api/v10/channels/{ch_id}/permissions/{MEMBER_ROLE_ID}"
                payload = {
                    "allow": str(allow_bits),
                    "deny": "0",
                    "type": 0  # 0 for role
                }
                async with session.put(url_mem_ch, headers=headers, json=payload) as resp:
                    print(f"  - {'🔊' if ch_type in (2, 13) else '#'} {ch_name}: @Member perms applied (status {resp.status})")

                await asyncio.sleep(0.1)

        print("\n=== ALL @Member PERMISSIONS EXPLICITLY APPLIED ===")


if __name__ == "__main__":
    asyncio.run(main())
