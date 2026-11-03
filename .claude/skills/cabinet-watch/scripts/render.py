"""Local helper: render an HTML/CSS file to a PNG with this Mac's Chrome, for charts, diagrams and data cards.

  uv run --no-project -q --with playwright python render.py page.html out.png [width]

The page is laid out at `width` CSS pixels (default 820, portrait reads best in Discord) and shot at 2x. If the
page has an element with id="card", only that element is captured; otherwise the whole page. Scripts and web fonts
load before the shot, so Chart.js or Google Fonts from a CDN work; the page can set window.renderReady = false and
flip it to true when it has finished drawing."""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

src, out = Path(sys.argv[1]).resolve(), Path(sys.argv[2])
width = int(sys.argv[3]) if len(sys.argv) > 3 else 820

with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome")
    page = browser.new_page(viewport={"width": width, "height": 800}, device_scale_factor=2)
    page.goto(src.as_uri(), wait_until="networkidle")
    page.evaluate("document.fonts.ready")
    page.wait_for_function("window.renderReady !== false", timeout=15000)
    page.wait_for_timeout(300)  # let chart animations settle
    card = page.locator("#card")
    if card.count():
        card.first.screenshot(path=str(out), omit_background=True)
    else:
        page.screenshot(path=str(out), full_page=True)
    browser.close()
print("rendered", out, f"{out.stat().st_size // 1024} KB")
