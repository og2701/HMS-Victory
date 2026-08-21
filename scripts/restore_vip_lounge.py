import asyncio
import json
import os
import aiohttp
from dotenv import load_dotenv

load_dotenv()
token = os.getenv("DISCORD_TOKEN")
headers = {"Authorization": f"Bot {token}"}
VIP_ID = "1333482774157590609"

with open("data/snapshots/permission_snapshot_latest.json") as f:
    snap = json.load(f)

orig_vip = next((c for c in snap["channels"] if c["id"] == VIP_ID), None)
expected_overwrites = orig_vip.get("permission_overwrites", [])


async def main():
    if not token:
        print("Error: DISCORD_TOKEN missing")
        return

    async with aiohttp.ClientSession() as session:
        async with session.get(f"https://discord.com/api/v10/channels/{VIP_ID}", headers=headers) as r:
            cur_ch = await r.json()

        cur_overwrites = cur_ch.get("permission_overwrites", [])
        expected_ids = {ow["id"] for ow in expected_overwrites}

        for ow in cur_overwrites:
            target_id = ow["id"]
            if target_id not in expected_ids:
                url = f"https://discord.com/api/v10/channels/{VIP_ID}/permissions/{target_id}"
                async with session.delete(url, headers=headers) as resp:
                    print(f"Deleted extra overwrite {target_id}: status {resp.status}")

        for ow in expected_overwrites:
            target_id = ow["id"]
            target_type = ow["type"]
            allow = ow["allow"]
            deny = ow["deny"]
            url = f"https://discord.com/api/v10/channels/{VIP_ID}/permissions/{target_id}"
            payload = {"allow": str(allow), "deny": str(deny), "type": target_type}
            async with session.put(url, headers=headers, json=payload) as resp:
                print(f"Restored overwrite for {target_id}: status {resp.status}")

        print("VIP Lounge successfully restored to exact pre-lockdown snapshot state!")


if __name__ == "__main__":
    asyncio.run(main())
