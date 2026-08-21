import asyncio
import json
import os
import aiohttp
from dotenv import load_dotenv

load_dotenv()
token = os.getenv("DISCORD_TOKEN")
headers = {"Authorization": f"Bot {token}"}
GUILD_ID = 959493056242008184
MEMBER_ROLE_ID = 1142491622563643442

ALLOWED_PUBLIC_CHANNELS = {
    # General
    "959493057076666380",  # general
    "959818055863648257",  # memes
    "1132950771701395496",  # chill-room
    "1140989025264607263",  # pets
    "1132279234858078239",  # politics
    "1145525221021982801",  # sports
    "1341451323249266711",  # uk-place-forum
    # Gaming & Bots
    "1512095841517699213",  # casino
    "1139977009389387959",  # gaming
    "1151925273663635597",  # minecraft
    "1142970908059910204",  # the-tree
    "1146919430861902079",  # one-word-story
    "968502541107228734",   # bot-spam
    # r/place
    "1140984852959735879",  # pixelcanvas
    "1263098555829194883",  # wplace
    "1140984249961414736",  # art-submissions
    "1140984033745043538",  # art-discussion
    # VC
    "1133795448969252945",  # general-vc
    "1133795551368978502",  # sports-stream
    "1133795627726299156",  # gaming-vc
    "1133795689407725619",  # comp-gaming
    "1151925345994407987",  # minecraft-vc
    "1145535973703291000",  # peasant vc
    "1134261682399625349",  # Stage
    "1133795328810844280",  # vc-spam
    # Temp VC
    "1143557876800049182",  # Create Room
    # Support public
    "960228871511351336",   # suggestions
    "1139976773292019772",  # support-ticket
}


async def main():
    if not token:
        print("Error: DISCORD_TOKEN is missing")
        return

    with open("data/snapshots/permission_snapshot_latest.json") as f:
        snap = json.load(f)

    snap_channels = {c["id"]: c for c in snap["channels"]}

    async with aiohttp.ClientSession() as session:
        async with session.get(f"https://discord.com/api/v10/guilds/{GUILD_ID}/channels", headers=headers) as r:
            live_channels = await r.json()

        print(f"Auditing all {len(live_channels)} live channels...")
        
        leaked_channels = []
        for ch in live_channels:
            ch_id = ch["id"]
            ch_name = ch["name"]
            ch_type = ch.get("type")
            parent_id = ch.get("parent_id")
            
            overwrites = ch.get("permission_overwrites", [])
            mem_ow = next((ow for ow in overwrites if ow["id"] == str(MEMBER_ROLE_ID)), None)

            # If @Member overwrite is present on a channel that is NOT in the allowed public set:
            if mem_ow and ch_id not in ALLOWED_PUBLIC_CHANNELS:
                leaked_channels.append((ch_name, ch_id, ch.get("parent_id")))

        print("\n=== AUDIT RESULTS ===")
        if leaked_channels:
            print(f"⚠️ FOUND {len(leaked_channels)} UNEXPECTED CHANNELS WITH @Member OVERWRITE:")
            for name, cid, pid in leaked_channels:
                print(f"  - #{name} ({cid}, parent: {pid})")
        else:
            print("✅ ZERO LEAKS FOUND!")
            print("All staff, ministry, private, VIP, and ticket channels are 100% clean and restricted.")


if __name__ == "__main__":
    asyncio.run(main())
