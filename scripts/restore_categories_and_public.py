import asyncio
import json
import os
import aiohttp
from dotenv import load_dotenv

load_dotenv()
token = os.getenv("DISCORD_TOKEN")
headers = {"Authorization": f"Bot {token}"}
GUILD_ID = "959493056242008184"

with open("data/snapshots/permission_snapshot_latest.json") as f:
    snap = json.load(f)


async def main():
    if not token:
        print("Error: DISCORD_TOKEN missing")
        return

    async with aiohttp.ClientSession() as session:
        for ch in snap["channels"]:
            cid = ch["id"]
            name = ch["name"]
            if ch["type"] == 4 or cid in ["959503403199905862", "959538521612369952", "959848236384919692", "960228871511351336"]:
                for ow in ch.get("permission_overwrites", []):
                    if ow["id"] == GUILD_ID:
                        url = f"https://discord.com/api/v10/channels/{cid}/permissions/{GUILD_ID}"
                        payload = {"allow": str(ow["allow"]), "deny": str(ow["deny"]), "type": 0}
                        async with session.put(url, headers=headers, json=payload) as r:
                            print(f"Restored @everyone on {name}: {r.status}")
                        await asyncio.sleep(0.1)

        print("\nCategories and public channels successfully restored!")


if __name__ == "__main__":
    asyncio.run(main())
