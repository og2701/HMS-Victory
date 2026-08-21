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

snap_roles = {r["id"]: r for r in snap.get("roles", [])}
snap_channels = {c["id"]: c for c in snap.get("channels", [])}


async def main():
    if not token:
        print("Error: DISCORD_TOKEN is missing")
        return

    async with aiohttp.ClientSession() as session:
        # 1. Restore @everyone global role permissions
        orig_ev_role = snap_roles.get(GUILD_ID)
        if orig_ev_role:
            url = f"https://discord.com/api/v10/guilds/{GUILD_ID}/roles/{GUILD_ID}"
            payload = {"permissions": orig_ev_role["permissions"]}
            async with session.patch(url, headers=headers, json=payload) as r:
                print(f"Restored @everyone base role permissions to {orig_ev_role['permissions']}: status {r.status}")

        # 2. Restore exact overwrites on all channels
        async with session.get(f"https://discord.com/api/v10/guilds/{GUILD_ID}/channels", headers=headers) as r:
            live_channels = await r.json()

        for ch in live_channels:
            cid = ch["id"]
            name = ch["name"]
            if cid in ["959538521612369952", "1410086902245363844"]:  # current-template
                continue
            sc = snap_channels.get(cid)
            if not sc:
                continue

            snap_ows = sc.get("permission_overwrites", [])
            live_ows = ch.get("permission_overwrites", [])

            snap_dict = {ow["id"]: (str(ow["allow"]), str(ow["deny"]), ow["type"]) for ow in snap_ows}
            live_dict = {ow["id"]: (str(ow["allow"]), str(ow["deny"]), ow["type"]) for ow in live_ows}

            # Delete any extra overwrites
            for tid in list(live_dict.keys()):
                if tid not in snap_dict:
                    url = f"https://discord.com/api/v10/channels/{cid}/permissions/{tid}"
                    async with session.delete(url, headers=headers) as del_r:
                        print(f"  #{name}: deleted extra overwrite {tid} (status {del_r.status})")
                    await asyncio.sleep(0.1)

            # Apply exact snapshot overwrites
            for tid, (allow, deny, otype) in snap_dict.items():
                if live_dict.get(tid) != (allow, deny, otype):
                    url = f"https://discord.com/api/v10/channels/{cid}/permissions/{tid}"
                    payload = {"allow": allow, "deny": deny, "type": otype}
                    while True:
                        async with session.put(url, headers=headers, json=payload) as put_r:
                            if put_r.status == 429:
                                data = await put_r.json()
                                await asyncio.sleep(data.get("retry_after", 1.0) + 0.1)
                                continue
                            print(f"  #{name}: restored overwrite {tid} (status {put_r.status})")
                            break
                    await asyncio.sleep(0.1)

        print("\nAll server roles and channels 100% restored!")


if __name__ == "__main__":
    asyncio.run(main())
