import asyncio
import json
import os
import time
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

    async with aiohttp.ClientSession() as session:
        print("Fetching guild data...")
        async with session.get(f"https://discord.com/api/v10/guilds/{GUILD_ID}", headers=headers) as r:
            guild_data = await r.json()

        print("Fetching roles...")
        async with session.get(f"https://discord.com/api/v10/guilds/{GUILD_ID}/roles", headers=headers) as r:
            roles_data = await r.json()

        print("Fetching channels & overwrites...")
        async with session.get(f"https://discord.com/api/v10/guilds/{GUILD_ID}/channels", headers=headers) as r:
            channels_data = await r.json()

        print("Fetching members...")
        members_data = []
        after = 0
        while True:
            url = f"https://discord.com/api/v10/guilds/{GUILD_ID}/members?limit=1000"
            if after:
                url += f"&after={after}"
            async with session.get(url, headers=headers) as r:
                if r.status != 200:
                    print("Member fetch status:", r.status)
                    break
                batch = await r.json()
                if not batch:
                    break
                members_data.extend(batch)
                after = int(batch[-1]["user"]["id"])
                if len(batch) < 1000:
                    break

        snapshot = {
            "guild_id": GUILD_ID,
            "guild_name": guild_data.get("name"),
            "created_at": int(time.time()),
            "roles": roles_data,
            "channels": channels_data,
            "members": [
                {
                    "user_id": m["user"]["id"],
                    "username": m["user"].get("username"),
                    "nick": m.get("nick"),
                    "roles": m.get("roles", []),
                }
                for m in members_data
            ],
        }

        os.makedirs("data/snapshots", exist_ok=True)
        ts = int(time.time())
        filename = f"data/snapshots/permission_snapshot_{ts}.json"
        latest = "data/snapshots/permission_snapshot_latest.json"

        with open(filename, "w") as f:
            json.dump(snapshot, f, indent=2)

        with open(latest, "w") as f:
            json.dump(snapshot, f, indent=2)

        print(f"Snapshot created successfully!")
        print(f"File: {filename}")
        print(f"Roles: {len(roles_data)}")
        print(f"Channels & Categories: {len(channels_data)}")
        print(f"Members: {len(members_data)}")


if __name__ == "__main__":
    asyncio.run(main())
