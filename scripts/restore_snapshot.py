import asyncio
import json
import os
import sys
import aiohttp
from dotenv import load_dotenv

load_dotenv()
token = os.getenv("DISCORD_TOKEN")
headers = {"Authorization": f"Bot {token}"}
GUILD_ID = 959493056242008184
MEMBER_ROLE_ID = "1142491622563643442"


async def main():
    if not token:
        print("Error: DISCORD_TOKEN is missing", flush=True)
        return

    snapshot_file = sys.argv[1] if len(sys.argv) > 1 else "data/snapshots/permission_snapshot_latest.json"
    if not os.path.exists(snapshot_file):
        print(f"Error: Snapshot file {snapshot_file} not found", flush=True)
        return

    with open(snapshot_file) as f:
        snapshot = json.load(f)

    print(f"Restoring permissions from snapshot: {snapshot_file} (taken {snapshot.get('created_at')})...", flush=True)

    async with aiohttp.ClientSession() as session:
        # Fetch current live channels
        async with session.get(f"https://discord.com/api/v10/guilds/{GUILD_ID}/channels", headers=headers) as r:
            live_channels = await r.json()

        # 1. First delete @Member overwrites that were added during lockdown
        print("Cleaning up added @Member overwrites...", flush=True)
        for ch in live_channels:
            ch_id = ch["id"]
            overwrites = ch.get("permission_overwrites", [])
            for ow in overwrites:
                if ow["id"] == MEMBER_ROLE_ID:
                    # Check if @Member was originally in snapshot for this channel
                    snap_ch = next((c for c in snapshot.get("channels", []) if c["id"] == ch_id), None)
                    orig_mem = next((o for o in snap_ch.get("permission_overwrites", []) if o["id"] == MEMBER_ROLE_ID), None) if snap_ch else None
                    if not orig_mem:
                        url = f"https://discord.com/api/v10/channels/{ch_id}/permissions/{MEMBER_ROLE_ID}"
                        async with session.delete(url, headers=headers) as del_resp:
                            print(f"  Removed added @Member overwrite from #{ch.get('name')}: {del_resp.status}", flush=True)
                        await asyncio.sleep(0.1)

        # 2. Restore all channel overwrites from snapshot
        print("\nRestoring all channels & categories to exact snapshot state...", flush=True)
        for ch in snapshot.get("channels", []):
            ch_id = ch["id"]
            overwrites = ch.get("permission_overwrites", [])
            for ow in overwrites:
                target_id = ow["id"]
                target_type = ow["type"]
                allow = ow["allow"]
                deny = ow["deny"]
                url = f"https://discord.com/api/v10/channels/{ch_id}/permissions/{target_id}"
                payload = {"allow": str(allow), "deny": str(deny), "type": target_type}
                while True:
                    async with session.put(url, headers=headers, json=payload) as put_resp:
                        if put_resp.status == 429:
                            data = await put_resp.json()
                            await asyncio.sleep(data.get("retry_after", 1.0) + 0.1)
                            continue
                        break
                await asyncio.sleep(0.05)

        print("\nFull server permission restoration 100% complete!", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
