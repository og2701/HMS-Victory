import asyncio
import os
import aiohttp
from dotenv import load_dotenv

load_dotenv()
token = os.getenv("DISCORD_TOKEN")
headers = {"Authorization": f"Bot {token}"}
GUILD_ID = 959493056242008184
MEMBER_ROLE_ID = 1142491622563643442


async def main():
    if not token:
        print("Error: DISCORD_TOKEN is missing")
        return

    async with aiohttp.ClientSession() as session:
        print(f"Fetching all members in guild {GUILD_ID}...")
        members = []
        after = 0
        while True:
            url = f"https://discord.com/api/v10/guilds/{GUILD_ID}/members?limit=1000"
            if after:
                url += f"&after={after}"
            async with session.get(url, headers=headers) as r:
                if r.status != 200:
                    print(f"Error fetching members: {r.status}")
                    break
                batch = await r.json()
                if not batch:
                    break
                members.extend(batch)
                after = int(batch[-1]["user"]["id"])
                if len(batch) < 1000:
                    break

        print(f"Total members found: {len(members)}")
        
        # Filter members who don't have the Member role and are not bots
        needs_role = []
        for m in members:
            if m["user"].get("bot", False):
                continue
            role_ids = [int(r) for r in m.get("roles", [])]
            if MEMBER_ROLE_ID not in role_ids:
                needs_role.append(m["user"]["id"])

        print(f"Members needing @Member role: {len(needs_role)}")
        if not needs_role:
            print("All non-bot members already have the @Member role!")
            return

        success_count = 0
        error_count = 0

        for i, user_id in enumerate(needs_role, 1):
            url = f"https://discord.com/api/v10/guilds/{GUILD_ID}/members/{user_id}/roles/{MEMBER_ROLE_ID}"
            async with session.put(url, headers=headers) as r:
                if r.status in (200, 204):
                    success_count += 1
                else:
                    error_count += 1
                    print(f"Failed to add role to {user_id}: status {r.status}")

            if i % 100 == 0 or i == len(needs_role):
                print(f"Progress: {i}/{len(needs_role)} members processed (Success: {success_count}, Errors: {error_count})")

            # Respect Discord rate limits (approx ~10-20 role updates per second)
            await asyncio.sleep(0.05)

        print(f"\nBackfill complete! Assigned @Member to {success_count} members ({error_count} errors).")


if __name__ == "__main__":
    asyncio.run(main())
