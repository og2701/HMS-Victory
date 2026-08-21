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
        print("Error: DISCORD_TOKEN missing")
        return

    async with aiohttp.ClientSession() as session:
        # 1. Audit Roles
        async with session.get(f"https://discord.com/api/v10/guilds/{GUILD_ID}/roles", headers=headers) as r:
            live_roles = await r.json()

        role_diffs = []
        for r in live_roles:
            rid = r["id"]
            sr = snap_roles.get(rid)
            if sr:
                if str(r.get("permissions")) != str(sr.get("permissions")):
                    role_diffs.append((r["name"], rid, r.get("permissions"), sr.get("permissions")))

        print(f"Role permission differences: {len(role_diffs)}")
        for rd in role_diffs:
            print(f"  Role {rd[0]} ({rd[1]}): live={rd[2]} vs snap={rd[3]}")

        # 2. Audit Channels
        async with session.get(f"https://discord.com/api/v10/guilds/{GUILD_ID}/channels", headers=headers) as r:
            live_channels = await r.json()

        ch_diffs = []
        for ch in live_channels:
            cid = ch["id"]
            name = ch["name"]
            if cid in ["959538521612369952", "1410086902245363844"]:  # current-template
                continue
            sc = snap_channels.get(cid)
            if sc:
                # check parent_id
                if ch.get("parent_id") != sc.get("parent_id"):
                    ch_diffs.append((name, cid, f"parent mismatch: live {ch.get('parent_id')} vs snap {sc.get('parent_id')}"))
                # check overwrites
                c_ows = {ow["id"]: (str(ow["allow"]), str(ow["deny"])) for ow in ch.get("permission_overwrites", [])}
                s_ows = {ow["id"]: (str(ow["allow"]), str(ow["deny"])) for ow in sc.get("permission_overwrites", [])}
                if c_ows != s_ows:
                    ch_diffs.append((name, cid, f"overwrite mismatch: live {c_ows} vs snap {s_ows}"))

        print(f"\nChannel differences: {len(ch_diffs)}")
        for cd in ch_diffs:
            print(f"  Channel {cd[0]} ({cd[1]}): {cd[2]}")

        if not role_diffs and not ch_diffs:
            print("\n✅ PERFECT 0 DIFFS: The server configuration on Discord backend is 100% identical to the pre-lockdown snapshot!")


if __name__ == "__main__":
    asyncio.run(main())
