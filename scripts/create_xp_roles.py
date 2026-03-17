import discord
import os
import asyncio
from config import ROLES, GUILD_ID

# Manual .env loading
def load_env():
    if os.path.exists('.env'):
        with open('.env') as f:
            for line in f:
                if '=' in line:
                    key, value = line.strip().split('=', 1)
                    os.environ[key] = value

load_env()

class RoleCreator(discord.Client):
    async def on_ready(self):
        print(f'Logged in as {self.user}')
        guild = self.get_guild(GUILD_ID)
        if not guild:
            print(f'Guild with ID {GUILD_ID} not found.')
            await self.close()
            return

        duke_role = guild.get_role(ROLES.DUKE)
        if not duke_role:
            print("Duke role not found in guild. Please ensure ROLES.DUKE is correct.")
            await self.close()
            return

        # Roles to create in order of precedence: lowest to highest XP
        # We create them in this order so we can easily stack them above Duke.
        new_roles_config = [
            ("Viceroy", 350000),
            ("Lord High Chancellor", 500000),
            ("Lord High Steward", 750000),
            ("Grand Duke", 1100000),
            ("Archduke", 1750000),
            ("Royal Duke", 3000000)
        ]

        print(f"\nTarget Guild: {guild.name}")
        print(f"Reference Role: Duke (Position: {duke_role.position}, Color: {duke_role.color})")
        print("-" * 40)

        created_roles = []
        base_position = duke_role.position
        
        # We created these one after the other. 
        # Position X+1, X+2 etc.
        
        for i, (name, xp) in enumerate(new_roles_config):
            print(f"\n[PROMPT] Prepare to create role: {name}")
            print(f"Threshold: {xp:,} XP")
            print(f"Target Position: {base_position + i + 1} (Above Duke)")
            
            # Using loop for sync input in async
            ans = await asyncio.to_thread(input, "Create this role? (y/n): ")
            
            if ans.lower() == 'y':
                try:
                    # Create with Duke's appearance settings
                    new_role = await guild.create_role(
                        name=name,
                        color=duke_role.color,
                        reason=f"XP Expansion: {xp} XP milestone",
                        hoist=duke_role.hoist,
                        mentionable=duke_role.mentionable
                    )
                    
                    # Edit position to be above the previously placed role
                    # Note: Discord positions can be finicky, but setting +1 relative to Duke's original pos
                    # works well for a batch creation.
                    await new_role.edit(position=base_position + i + 1)
                    
                    print(f"✅ Success: {name} created with ID {new_role.id}")
                    created_roles.append((name, new_role.id, xp))
                except Exception as e:
                    print(f"❌ Failed to create {name}: {e}")
            else:
                print(f"⏭️ Skipped {name}")

        print("\n" + "="*40)
        print("FINAL ROLE CONFIGURATION (Copy these to config.py)")
        print("="*40)
        for name, rid, xp in created_roles:
            key_name = name.upper().replace(" ", "_")
            print(f"    {key_name} = {rid}")
        
        print("\nXP Thresholds (Copy these to constants.py):")
        for name, rid, xp in created_roles:
            key_name = name.upper().replace(" ", "_")
            print(f"    ({xp}, ROLES.{key_name}),")
            
        print("\nAll done. Closing connection...")
        await self.close()

async def main():
    intents = discord.Intents.default()
    intents.members = True
    client = RoleCreator(intents=intents)
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("Error: DISCORD_TOKEN not found in .env")
        return
    
    try:
        await client.start(token)
    except KeyboardInterrupt:
        await client.close()

if __name__ == "__main__":
    asyncio.run(main())
