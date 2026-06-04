"""Pre-render the roulette spin GIFs.

The ticker animation depends only on the winning number (0-36), so we render each of the
37 outcomes once here and the bot serves them instantly at spin time. Also bakes one
generic looping "spinner" used as the loader / fallback before the 37 are deployed.

Run on a machine with the bot's deps + headless Chrome (NOT this sandbox):

    python scripts/bake_roulette_results.py            # all 37 + spinner
    python scripts/bake_roulette_results.py --sample    # a handful to eyeball
    python scripts/bake_roulette_results.py --force      # re-render even if present
    python scripts/bake_roulette_results.py --spinner    # just the generic spinner

Output:
    data/roulette_results/<n>.gif        (one per winning number)
    data/roulette_spinning/spin.gif      (generic loader)
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from commands.economy import roulette  # noqa: E402


def _targets(sample: bool):
    if sample:
        return [0, 17, 32, 5, 26]
    return list(range(37))


async def main():
    import config
    sample = "--sample" in sys.argv
    force = "--force" in sys.argv
    spinner_only = "--spinner" in sys.argv

    spin_dir = os.path.join(config.DATA_DIR, "roulette_spinning")
    os.makedirs(spin_dir, exist_ok=True)
    spinner_path = os.path.join(spin_dir, "spin.gif")
    if spinner_only or force or not os.path.exists(spinner_path):
        print("Baking generic spinner ...")
        gif = await roulette.render_spinner_gif()
        with open(spinner_path, "wb") as fh:
            fh.write(gif.getbuffer())
        print(f"  -> {spinner_path}")
    if spinner_only:
        return

    out_dir = roulette.results_dir()
    os.makedirs(out_dir, exist_ok=True)
    targets = _targets(sample)
    print(f"Baking {len(targets)} roulette result GIF(s) into {out_dir} ...")

    done = skipped = 0
    for i, n in enumerate(targets, 1):
        path = roulette._result_gif_path(n)
        if os.path.exists(path) and not force:
            skipped += 1
            continue
        gif = await roulette.render_result_gif(n)
        with open(path, "wb") as fh:
            fh.write(gif.getbuffer())
        done += 1
        print(f"[{i}/{len(targets)}] {os.path.basename(path)}")

    print(f"Done. Rendered {done}, skipped {skipped} (already present). Files in {out_dir}")


if __name__ == "__main__":
    asyncio.run(main())
