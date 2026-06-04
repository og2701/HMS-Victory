import os
import sys
import io
import random
from PIL import Image
import itertools

sys.path.append(os.getcwd())

import database
database.init_db()

from commands.economy.slots import SlotMachine, build_slots_html, _KEYS
from lib.core.image_processing import _screenshot_html_sequence_sync

NUM_GIFS = 20
FRAMES_PER_GIF = 6  # More frames = smoother animation while real GIF loads

def _balanced_symbol_pool(n_frames: int) -> list[list[str]]:
    """
    Return n_frames rows of 3 symbols, guaranteed flat distribution.
    Each reel cycles through all 7 symbols in a different shuffled order.
    """
    cols = []
    for _ in range(3):
        # Build enough copies to cover all frames, shuffle each full deck
        pool = []
        while len(pool) < n_frames:
            deck = list(_KEYS)
            random.shuffle(deck)
            pool.extend(deck)
        cols.append(pool[:n_frames])
    # Transpose: list of [reel0, reel1, reel2] per frame
    return [[cols[r][f] for r in range(3)] for f in range(n_frames)]


def pregenerate():
    output_dir = "data/slots_spinning"
    os.makedirs(output_dir, exist_ok=True)

    machine = SlotMachine(player_id=0, player_name="Player", channel_id=0, bet=5)

    print(f"Pre-generating {NUM_GIFS} spinning slot machine GIFs ({FRAMES_PER_GIF} frames each)...")

    for i in range(NUM_GIFS):
        symbol_frames = _balanced_symbol_pool(FRAMES_PER_GIF)
        frames_html = [
            build_slots_html(machine, reels=reels, mult=0, win=0, spinning=True)
            for reels in symbol_frames
        ]

        durations = [110] * FRAMES_PER_GIF  # Fast spinning frames
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

