import asyncio
import json
import os
import aiohttp
from dotenv import load_dotenv

load_dotenv()
token = os.getenv("DISCORD_TOKEN")
headers = {"Authorization": f"Bot {token}"}
CHANNELS_TO_RESTORE = [
    "1132279234858078239",  # roles
    "1132950771701395496",  # suggestions
    "1133386861033832448",  # minor-announcements
    "1139976595336069161",  # support category
]

with open("data/snapshots/permission_snapshot_latest.json") as f:
    snap = json.load(f)

snap_channels = {c["id"]: c for c in snap["channels"]}


async def main():
    if not token:
        print("Error: DISCORD_TOKEN missing")
        return

    async with aiohttp.ClientSession() as session:
        for cid in CHANNELS_TO_RESTORE:
            orig = snap_channels.get(cid)
            if not orig:
                continue

            expected_overwrites = orig.get("permission_overwrites", [])
            print(f"Restoring #{orig.get('name')} ({cid}) to exact snapshot...")

            # Fetch current
            async with session.get(f"https://discord.com/api/v10/channels/{cid}", headers=headers) as r:
                cur_ch = await r.json()

            cur_overwrites = cur_ch.get("permission_overwrites", [])
            expected_ids = {ow["id"] for ow in expected_overwrites}

            # Delete extra
            for ow in cur_overwrites:
                target_id = ow["id"]
                if target_id not in expected_ids:
                    url = f"https://discord.com/api/v10/channels/{cid}/permissions/{target_id}"
                    async with session.delete(url, headers=headers) as resp:
                        print(f"  Deleted extra overwrite {target_id}: status {resp.status}")

            # Re-apply exact snapshot overwrites
            for ow in expected_overwrites:
                target_id = ow["id"]
                target_type = ow["type"]
                allow = ow["allow"]
                deny = ow["deny"]
                url = f"https://discord.com/api/v10/channels/{cid}/permissions/{target_id}"
                payload = {"allow": str(allow), "deny": str(deny), "type": target_type}
                while True:
                    async with session.put(url, headers=headers, json=payload) as resp:
                        if resp.status == 429:
                            data = await resp.json()
                            await asyncio.sleep(data.get("retry_after", 1.0) + 0.1)
                            continue
                        print(f"  Applied {target_id}: status {resp.status}")
                        break
                await asyncio.sleep(0.15)

        print("\nAll targeted channels 100% restored!")


if __name__ == "__main__":
    asyncio.run(main())
