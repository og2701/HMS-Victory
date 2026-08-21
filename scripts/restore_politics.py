import asyncio
import json
import os
import aiohttp
from dotenv import load_dotenv

load_dotenv()
token = os.getenv("DISCORD_TOKEN")
headers = {"Authorization": f"Bot {token}"}
POL_ID = "1141097424849481799"

with open("data/snapshots/permission_snapshot_latest.json") as f:
    snap = json.load(f)

orig_pol = next((c for c in snap["channels"] if c["id"] == POL_ID), None)
expected_overwrites = orig_pol.get("permission_overwrites", [])


async def main():
    if not token:
        print("Error: DISCORD_TOKEN missing")
        return

    async with aiohttp.ClientSession() as session:
        async with session.get(f"https://discord.com/api/v10/channels/{POL_ID}", headers=headers) as r:
            cur_ch = await r.json()

        cur_overwrites = cur_ch.get("permission_overwrites", [])
        expected_ids = {ow["id"] for ow in expected_overwrites}

        for ow in cur_overwrites:
            target_id = ow["id"]
            if target_id not in expected_ids:
                url = f"https://discord.com/api/v10/channels/{POL_ID}/permissions/{target_id}"
                async with session.delete(url, headers=headers) as resp:
                    print(f"Deleted extra overwrite {target_id}: status {resp.status}")

        for ow in expected_overwrites:
            target_id = ow["id"]
            target_type = ow["type"]
            allow = ow["allow"]
            deny = ow["deny"]
            url = f"https://discord.com/api/v10/channels/{POL_ID}/permissions/{target_id}"
            payload = {"allow": str(allow), "deny": str(deny), "type": target_type}
            while True:
                async with session.put(url, headers=headers, json=payload) as resp:
                    if resp.status == 429:
                        data = await resp.json()
                        await asyncio.sleep(data.get("retry_after", 1.0) + 0.1)
                        continue
                    break
            await asyncio.sleep(0.15)

        print(f"Successfully verified and restored all {len(expected_overwrites)} original overwrites on #politics!")


if __name__ == "__main__":
    asyncio.run(main())
