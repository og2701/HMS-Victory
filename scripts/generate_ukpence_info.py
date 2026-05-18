"""One-off generator for the /ukpence guide image.

Run from the repo root:

    python -m scripts.generate_ukpence_info

Reads templates/ukpence_info.html, renders it, writes data/ukpence_info.png.
Re-run this script whenever the template changes.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.core.image_processing import screenshot_html


TEMPLATE_PATH = "templates/ukpence_info.html"
OUTPUT_PATH = "data/ukpence_info.png"


async def main():
    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        html = f.read()

    buf = await screenshot_html(html, size=(2450, 3400), element_selector=".page")

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "wb") as f:
        f.write(buf.getvalue())

    print(f"Wrote {OUTPUT_PATH} ({os.path.getsize(OUTPUT_PATH):,} bytes)")


if __name__ == "__main__":
    asyncio.run(main())
