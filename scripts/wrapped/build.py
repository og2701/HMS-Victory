"""Bundle the Wrapped player and a wrapped.json into one self-contained HTML page.

    python3 scripts/wrapped/build.py wrapped.json out/wrapped.html

The page needs nothing beside it: the fonts and the data are inlined, and three.js and the pictures
(avatars, custom emoji) load from their CDNs. Open it in a browser to watch, or hand it to render.mjs.
"""
import base64
import sys
from pathlib import Path

PLAYER = Path(__file__).parent / "player"
FONTS = Path(__file__).resolve().parents[2] / "data" / "fonts"
ORDER = ["core.js", "icons.js", "world.js", "pieces.js", "scenes-a.js", "scenes-b.js", "main.js"]


def font(name):
    return "data:font/ttf;base64," + base64.b64encode((FONTS / name).read_bytes()).decode()


def main():
    data_path, out_path = Path(sys.argv[1]), Path(sys.argv[2])
    css = ((PLAYER / "style.css").read_text()
           .replace("__FONT_ARCHIVO__", font("Archivo.ttf"))
           .replace("__FONT_PIXEL__", font("PressStart2P.ttf")))
    js = "\n".join(f"// ==== {name}\n{(PLAYER / name).read_text()}" for name in ORDER)
    data = data_path.read_text().replace("</", "<\\/")
    page = (PLAYER / "index.html").read_text()
    page = page.replace("/*CSS*/", css, 1).replace("/*JS*/", js, 1).replace("/*DATA*/", data, 1)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(page)
    print(f"wrote {out_path} ({len(page) // 1024} KB)")


if __name__ == "__main__":
    main()
