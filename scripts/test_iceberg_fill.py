import os
import sys
import random
import asyncio

# Add parent directory to sys.path to import modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import DatabaseManager
from commands.creative.iceberg.add_to_iceberg import add_iceberg_text, LEVEL_BOUNDS

# A list of random HMS Victory / Pirate / Ocean themed lore bits
TEST_LORE = [
    "Lorem", "Ipsum", "Dolor", "Sit", "Amet", "Consectetur", "Adipiscing", "Elit",
    "Sed", "Do", "Eiusmod", "Tempor", "Incididunt", "Ut", "Labore", "Et",
    "Dolore", "Magna", "Aliqua", "Ut", "Enim", "Ad", "Minim", "Veniam",
    "Quis", "Nostrud", "Exercitation", "Ullamco", "Laboris", "Nisi",
    "Ut", "Aliquip", "Ex", "Ea", "Commodo", "Consequat", "Duis", "Aute",
    "Irure", "Dolor", "In", "Reprehenderit", "In", "Voluptate", "Velit"
]

async def fill_iceberg(num_entries=20):
    print(f"🚀 Filling iceberg with {num_entries} random entries...")
    
    # Mock interaction for the async function
    class MockInteraction:
        def __init__(self):
            self.response = type('obj', (object,), {'is_done': lambda: True})()
            self.followup = type('obj', (object,), {'send': lambda *args, **kwargs: print(f"  [Bot]: {args[0] if args else kwargs.get('content', '')}")})()

    interaction = MockInteraction()

    for i in range(num_entries):
        text = random.choice(TEST_LORE)
        level = random.randint(1, 6)
        print(f"[{i+1}/{num_entries}] Adding '{text}' to Level {level}...")
        
        # We call add_iceberg_text directly. 
        # Note: We pass show_image=False to avoid "sending" to a non-existent channel
        await add_iceberg_text(interaction, text, level, show_image=False)

    print("\n✅ Finished adding test entries. The cache image should now be updated in data/iceberg_cache.png.")

if __name__ == "__main__":
    count = 20
    if len(sys.argv) > 1:
        try:
            count = int(sys.argv[1])
        except ValueError:
            pass
    
    asyncio.run(fill_iceberg(count))
