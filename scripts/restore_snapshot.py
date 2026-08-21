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


async def main():
    if not token:
        print("Error: DISCORD_TOKEN is missing")
        return

    snapshot_file = sys.argv[1] if len(sys.argv) > 1 else "data/snapshots/permission_snapshot_latest.json"
    if not os.path.exists(snapshot_file):
        print(f"Error: Snapshot file {snapshot_file} not found")
        return

    with open(snapshot_file) as f:
        snapshot = json.load(f)

    print(f"Restoring permissions from snapshot: {snapshot_file} (taken {snapshot.get('created_at')})...")

    async with aiohttp.ClientSession() as session:
        # Restore channel overwrites
        for ch in snapshot.get("channels", []):
            ch_id = ch["id"]
            overwrites = ch.get("permission_overwrites", [])
            print(f"Restoring #{ch.get('name')} ({ch_id}) with {len(overwrites)} overwrites...")
            # Update channel overwrites
            for ow in overwrites:
                target_id = ow["id"]
                target_type = ow["type"]
                allow = ow["allow"]
                deny = ow["deny"]
                url = f"https://discord.com/api/v10/channels/{ch_id}/permissions/{target_id}"
                payload = {"allow": str(allow), "deny": str(deny), "type": target_type}
                async with session.put(url, headers=headers, json=payload) as r:
                    if r.status not in (200, 204):
                        print(f"  Failed overwrite on {ch_id} for {target_id}: status {r.status}")
                await asyncio.sleep(0.1)

    print("Permission restoration complete!")


if __name__ == "__main__":
    asyncio.run(main())
