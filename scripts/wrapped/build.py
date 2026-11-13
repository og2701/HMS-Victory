"""Bundle the Wrapped player and a wrapped.json into one self-contained HTML page.

    python3 scripts/wrapped/build.py wrapped.json out/wrapped.html
    python3 scripts/wrapped/build.py personal.json out/personal.html --personal

The page needs nothing beside it: the fonts and the data are inlined, and three.js and the pictures
(avatars, custom emoji) load from their CDNs. Open it in a browser to watch, or hand it to render.mjs.
--personal builds the personal Wrapped page instead, from extract.py's personal.json: open it with
?u=<member id> for someone's video, and ?u=<id>&card=1 for their card.
"""
import base64
import sys
from pathlib import Path

PLAYER = Path(__file__).parent / "player"
DATA = Path(__file__).resolve().parents[2] / "data"
FONTS = DATA / "fonts"
ORDER = ["core.js", "icons.js", "world.js", "pieces.js", "scenes-a.js", "scenes-b.js", "main.js"]
PERSONAL = ["core.js", "icons.js", "world.js", "personal.js", "main.js"]


def inline(path, mime):
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()


def font(name):
    return inline(FONTS / name, "font/ttf")


def main():
    data_path, out_path = Path(sys.argv[1]), Path(sys.argv[2])
    css = ((PLAYER / "style.css").read_text()
           .replace("__FONT_ARCHIVO__", font("Archivo.ttf"))
           .replace("__FONT_PIXEL__", font("PressStart2P.ttf"))
           # the /iceberg's own picture and lettering, so the video's copy looks like the bot's
           .replace("__FONT_FLUFF__", inline(DATA / "fluff.ttf", "font/ttf"))
           .replace("__ICEBERG__", inline(DATA / "image.png", "image/png")))
    personal = "--personal" in sys.argv
    js = "\n".join(f"// ==== {name}\n{(PLAYER / name).read_text()}" for name in (PERSONAL if personal else ORDER))
    data = data_path.read_text()
    if personal:
        data = '{"personal":' + data + "}"
    data = data.replace("</", "<\\/")
    page = (PLAYER / "index.html").read_text()
    page = page.replace("/*CSS*/", css, 1).replace("/*JS*/", js, 1).replace("/*DATA*/", data, 1)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(page)
    print(f"wrote {out_path} ({len(page) // 1024} KB)")


if __name__ == "__main__":
    main()
