"""Pre-render every slot-machine result GIF into data/slots_results/.

The reel-slide picture depends only on the 3 final symbols (7x7x7 = 343 outcomes), so
we render each one once here and the bot serves them instantly at spin time instead of
rendering a 16-screenshot GIF on every spin.

Run once on a machine with the bot's deps + headless Chrome (NOT this sandbox):

    python scripts/bake_slots_results.py            # all 343
    python scripts/bake_slots_results.py --sample    # ~6 representative ones to eyeball
    python scripts/bake_slots_results.py --force     # re-render even if the file exists

Output: data/slots_results/<a>_<b>_<c>.gif  (e.g. crown_crown_crown.gif)
"""

import asyncio
import itertools
import os
import sys

# Make the project root importable when run as `python scripts/bake_slots_results.py`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from commands.economy import slots  # noqa: E402


def _combos(sample: bool):
    everything = list(itertools.product(slots._KEYS, repeat=3))
    if not sample:
        return everything
    # A spread that exercises the look: jackpot, plain trips, two-cherry, all-different.
    return [
        ("crown", "crown", "crown"),
        ("cherry", "cherry", "cherry"),
        ("cherry", "cherry", "lion"),
        ("lion", "rose", "anchor"),
        ("pound", "pound", "union"),
        ("union", "lion", "cherry"),
    ]


async def main():
    sample = "--sample" in sys.argv
    force = "--force" in sys.argv
    out_dir = slots.results_dir()
    os.makedirs(out_dir, exist_ok=True)

    combos = _combos(sample)
    total = len(combos)
    print(f"Baking {total} slot result GIF(s) into {out_dir} ...")

    done = skipped = 0
    for i, reels in enumerate(combos, 1):
        path = slots._result_gif_path(list(reels))
        if os.path.exists(path) and not force:
            skipped += 1
            continue
        # Deterministic lead-in per outcome so a re-bake is byte-identical.
        seed = (hash(reels) & 0xFFFF)
        gif = await slots.render_slots_result_gif(list(reels), seed=seed)
        with open(path, "wb") as fh:
            fh.write(gif.getbuffer())
        done += 1
        print(f"[{i}/{total}] {os.path.basename(path)}")

    print(f"Done. Rendered {done}, skipped {skipped} (already present). Files in {out_dir}")


if __name__ == "__main__":
    asyncio.run(main())
