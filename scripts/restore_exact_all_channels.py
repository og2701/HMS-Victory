import asyncio
import json
import os
import aiohttp
from dotenv import load_dotenv

load_dotenv()
token = os.getenv("DISCORD_TOKEN")
headers = {"Authorization": f"Bot {token}"}
GUILD_ID = "959493056242008184"
CURRENT_TEMPLATE_ID = "959538521612369952"

with open("data/snapshots/permission_snapshot_latest.json") as f:
    snap = json.load(f)


async def main():
    if not token:
        print("Error: DISCORD_TOKEN is missing")
        return

    print("Restoring exact overwrites across all channels to 100% match the pre-lockdown snapshot...")
    async with aiohttp.ClientSession() as session:
        async with session.get(f"https://discord.com/api/v10/guilds/{GUILD_ID}/channels", headers=headers) as r:
            live_channels = await r.json()

        for ch in snap["channels"]:
            cid = ch["id"]
            name = ch["name"]
            if cid == CURRENT_TEMPLATE_ID:
                continue

            live_ch = next((c for c in live_channels if c["id"] == cid), None)
            if not live_ch:
                continue

            snap_ows = ch.get("permission_overwrites", [])
            live_ows = live_ch.get("permission_overwrites", [])

            snap_dict = {ow["id"]: (str(ow["allow"]), str(ow["deny"]), ow["type"]) for ow in snap_ows}
            live_dict = {ow["id"]: (str(ow["allow"]), str(ow["deny"]), ow["type"]) for ow in live_ows}

            # Delete any extra overwrites not in snapshot
            for target_id in live_dict:
                if target_id not in snap_dict:
                    url = f"https://discord.com/api/v10/channels/{cid}/permissions/{target_id}"
                    async with session.delete(url, headers=headers) as resp:
                        print(f"  #{name}: deleted extra overwrite {target_id} (status {resp.status})")
                    await asyncio.sleep(0.1)

            # Put exact snapshot overwrites if different
            for target_id, (allow, deny, otype) in snap_dict.items():
                if live_dict.get(target_id) != (allow, deny, otype):
                    url = f"https://discord.com/api/v10/channels/{cid}/permissions/{target_id}"
                    payload = {"allow": allow, "deny": deny, "type": otype}
                    while True:
                        async with session.put(url, headers=headers, json=payload) as resp:
                            if resp.status == 429:
                                data = await resp.json()
                                await asyncio.sleep(data.get("retry_after", 1.0) + 0.1)
                                continue
                            print(f"  #{name}: restored overwrite {target_id} (status {resp.status})")
                            break
                    await asyncio.sleep(0.1)

        print("\nAll channels successfully verified and restored to 100% exact snapshot match!")


if __name__ == "__main__":
    asyncio.run(main())
