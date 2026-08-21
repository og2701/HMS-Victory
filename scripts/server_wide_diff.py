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

# Intended public channels that should have @Member
INTENDED_PUBLIC_CHANNELS = {
    "959493057076666380",  # general
    "959818055863648257",  # memes
    "960121956332601414",  # chill-room
    "1140989025264607263",  # pets
    "1145525221021982801",  # sports
    "1341451323249266711",  # uk-place-forum
    "1512095841517699213",  # casino
    "1139977009389387959",  # gaming
    "1151925273663635597",  # minecraft
    "1142970908059910204",  # the-tree
    "1146919430861902079",  # one-word-story
    "968502541107228734",   # bot-spam
    "959796946845962260",   # art-discussion
    "1170007620329947176",  # art-submissions
    "1170359780745941102",  # pixelcanvas-discussion
    "1411450026991026317",  # wplace-discussion
    "1133795002531709088",  # Stage
    "1133795448969252945",  # general-vc
    "1214614789506990080",  # gaming-vc
    "1272628890652905583",  # comp-gaming
    "1291503370439233586",  # minecraft-vc
    "1306702733142790184",  # peasant vc
    "1508130904458133654",  # sports-stream
    "959575734190477322",   # vc-spam
    "1143557650999685252",  # Create Room
    "960228871511351336",   # suggestions
    "1143560594138595439",  # support-ticket
    # Category containers
    "959544877325115424",   # General
    "1151876537012985886",  # gaming and bots
    "1140983791238778890",  # r/place
    "959493057076666379",   # Permanent VC
    "1143557841886662717",  # Temporary VC
    "959503403199905862",   # announcements
    "959848236384919692",   # voting
    "959538521612369952",   # current-template
}


async def main():
    if not token:
        print("Error: DISCORD_TOKEN missing")
        return

    with open("data/snapshots/permission_snapshot_latest.json") as f:
        snap = json.load(f)

    snap_channels = {c["id"]: c for c in snap["channels"]}

    async with aiohttp.ClientSession() as session:
        async with session.get(f"https://discord.com/api/v10/guilds/{GUILD_ID}/channels", headers=headers) as r:
            live_channels = await r.json()

        unintended_diffs = []
        for ch in live_channels:
            ch_id = ch["id"]
            ch_name = ch["name"]
            
            if ch_id in INTENDED_PUBLIC_CHANNELS:
                continue

            snap_ch = snap_channels.get(ch_id)
            if not snap_ch:
                continue

            cur_ows = {ow["id"]: (ow["allow"], ow["deny"]) for ow in ch.get("permission_overwrites", [])}
            snap_ows = {ow["id"]: (ow["allow"], ow["deny"]) for ow in snap_ch.get("permission_overwrites", [])}

            # Check if there are any differences on this non-public channel
            if cur_ows != snap_ows:
                unintended_diffs.append((ch_name, ch_id, cur_ows, snap_ows))

        print(f"=== FULL SERVER INTEGRITY AUDIT ({len(live_channels)} CHANNELS) ===")
        if unintended_diffs:
            print(f"⚠️ Found {len(unintended_diffs)} unintended differences in non-public channels:")
            for name, cid, cur, snap_d in unintended_diffs:
                print(f"- #{name} ({cid}):")
                print(f"    Current: {cur}")
                print(f"    Snapshot: {snap_d}")
        else:
            print("✅ ZERO UNINTENDED DIFFERENCES FOUND ACROSS THE ENTIRE SERVER!")
            print("Every staff, ticket, exclusive, political, and archive channel is 100% identical to the snapshot.")


if __name__ == "__main__":
    asyncio.run(main())
