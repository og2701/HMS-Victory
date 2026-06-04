import os
import sys
import io
import random
from PIL import Image

sys.path.append(os.getcwd())

import database
database.init_db()

from commands.economy.slots import SlotMachine, build_slots_html, _KEYS
from lib.core.image_processing import _screenshot_html_sequence_sync

def pregenerate():
    output_dir = "data/slots_spinning"
    os.makedirs(output_dir, exist_ok=True)
    
    # We use a placeholder SlotMachine to build the HTML frames
    machine = SlotMachine(player_id=0, player_name="Player", channel_id=0, bet=5)
    
    print("Pre-generating 20 spinning slot machine GIFs...")
    
    for i in range(20):
        frames_html = []
        # Create a 4-frame fast loop of random symbols
        for _ in range(4):
            reels = [random.choice(_KEYS) for _ in range(3)]
            frames_html.append(build_slots_html(machine, reels=reels, mult=0, win=0, spinning=True))
            
        durations = [120, 120, 120, 120]  # Fast spinning frames
        gif_data = _screenshot_html_sequence_sync(
            frames_html, size=(820, 1000), element_selector=".cabinet", durations=durations, loop=0
        )
        
        target_path = f"{output_dir}/spin_{i}.gif"
        with open(target_path, "wb") as f:
            f.write(gif_data.getvalue())
        print(f"Generated {target_path} (size: {os.path.getsize(target_path) // 1024} KB)")
        
    print("Pre-generation complete!")

if __name__ == "__main__":
    pregenerate()
