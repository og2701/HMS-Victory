"""Bake the extracted season data into one standalone HTML replay.

    python3 scripts/bb_chronicle/build.py --data scratch/big_brother_chronicle/data \
        --avatars scratch/big_brother_chronicle/avatars --image fridge=fridge.webp --image items=items.webp \
        --out scratch/big_brother_chronicle/big_brother_chronicle.html

--data is extract.py's output folder (it must include iconic_chats.json, i.e. extract.py was run with
--scenes). --avatars holds <user_id>.webp files; --portraits, if given, holds <user_id>.webp pictures of the
celebrity each housemate played, which replace their avatar, plus a credits.json shown on the page.
--voice is voiceover.py's folder; clips are re-encoded to mono AAC with macOS afconvert to keep the page small. Custom Discord emoji quoted in the scenes are fetched
from Discord's CDN once and cached next to the avatars, so a rebuild works offline.

Everything (data, avatars, emoji, photos) is inlined, so the result opens from disk with no server;
only the Google Fonts stylesheet is remote, and the page falls back to system fonts without it.
"""
import argparse, base64, json, os, shutil, subprocess, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))

# Name colour per housemate in the chat replay, like Discord role colours.
COLORS = {
    "404634271861571584": "#f47fff", "1283837687551361117": "#faa61a", "1226934751588519997": "#ff73a8",
    "883005215471722546": "#ff5c7a", "447010711936303115": "#f9a8d4", "1131019240485425242": "#c084fc",
    "1185370569382838447": "#60a5fa", "655342755521757195": "#a3e635", "479207279850291221": "#fbbf24",
    "285860055570579457": "#38bdf8", "1146144337302917242": "#fde047", "1398652914737741956": "#f87171",
    "1449906006220996738": "#34d399", "1022210566871322754": "#fb923c", "792139113587277835": "#93c5fd",
    "828728237789020240": "#f97316", "716917080116428821": "#a78bfa", "860098855621623809": "#94a3b8",
    "1204435534416580679": "#ec4899", "692814294609559553": "#fcd34d", "797207976548499518": "#2dd4bf",
    "969768729548300288": "#f0abfc", "1504560326488756309": "#9ca3af", "276119377395449856": "#facc15",
    "1377248229154095194": "#c4b5fd", "412850506747215872": "#cbd5e1",
}
MIME = {".webp": "image/webp", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
        ".mp3": "audio/mpeg", ".m4a": "audio/mp4"}


def data_uri(path):
    ext = os.path.splitext(path)[1].lower()
    with open(path, "rb") as f:
        return f"data:{MIME.get(ext, 'application/octet-stream')};base64," + base64.b64encode(f.read()).decode()


def emoji_uris(story, cache_dir):
    os.makedirs(cache_dir, exist_ok=True)
    out = {}
    scenes = [s for c in story.get("chapters", []) for s in c.get("scenes", [])] + story.get("scenes", [])
    wanted = {e[1] for s in scenes for m in s.get("messages", []) for e in m.get("emoji", [])}
    for eid in sorted(wanted):
        path = os.path.join(cache_dir, f"{eid}.webp")
        if not os.path.exists(path):
            try:
                req = urllib.request.Request(f"https://cdn.discordapp.com/emojis/{eid}.webp?size=64&quality=lossless",
                                             headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=15) as r, open(path, "wb") as f:
                    f.write(r.read())
            except Exception as e:
                print(f"emoji {eid}: {e}")
                continue
        out[eid] = data_uri(path)
    return out


def voice_uris(story, voice_dir):
    """The clips the story's scenes and chapters point at, as data URIs with their length in seconds."""
    manifest = json.load(open(os.path.join(voice_dir, "voice.json")))
    keys = {c.get("voice") for c in story.get("chapters", [])}
    keys |= {s.get(k) for c in story.get("chapters", []) for s in c.get("scenes", []) for k in ("voice", "voice_say")}
    out = {}
    for k in sorted(x for x in keys if x and x in manifest):
        src = os.path.join(voice_dir, manifest[k]["file"])
        small = os.path.splitext(src)[0] + ".m4a"
        if shutil.which("afconvert") and not os.path.exists(small):
            subprocess.run(["afconvert", "-f", "m4af", "-d", "aac", "-b", "40000", "-c", "1", src, small], check=True)
        out[k] = {"src": data_uri(small if os.path.exists(small) else src), "seconds": manifest[k]["seconds"]}
    return out


def attachment_uris(story, folder):
    """Only the images a scene actually shows; there are hundreds more in the folder."""
    scenes = [s for c in story.get("chapters", []) for s in c.get("scenes", [])] + story.get("scenes", [])
    wanted = {f for s in scenes for m in s.get("messages", []) for f in m.get("images", [])}
    return {f: data_uri(os.path.join(folder, f)) for f in sorted(wanted) if os.path.exists(os.path.join(folder, f))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--avatars", required=True)
    ap.add_argument("--image", action="append", default=[], help="name=path, a photo the scenes refer to by name")
    ap.add_argument("--portraits", help="folder of <user_id>.webp celebrity pictures and their credits.json")
    ap.add_argument("--voice", help="voiceover.py's output folder")
    ap.add_argument("--attachments", help="folder of images posted in the chat; scene messages name the files they show")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    def load(name):
        with open(os.path.join(a.data, name)) as f:
            return json.load(f)

    cast = load("housemates.json")
    story = load("iconic_chats.json")
    avatars = {os.path.splitext(f)[0]: data_uri(os.path.join(a.avatars, f))
               for f in os.listdir(a.avatars) if f.endswith(".webp")}
    credits = {}
    if a.portraits:
        avatars.update({os.path.splitext(f)[0]: data_uri(os.path.join(a.portraits, f))
                        for f in os.listdir(a.portraits) if f.endswith(".webp")})
        credits = json.load(open(os.path.join(a.portraits, "credits.json")))
    bundle = {
        "meta": cast["meta"], "housemates": cast["housemates"], "days": load("timeline.json")["days"],
        "rounds": load("nominations_and_votes.json"), "challenges": load("challenges.json"), "story": story,
        "avatars": avatars, "colors": COLORS,
        "emoji": emoji_uris(story, os.path.join(a.avatars, "emoji")),
        "images": {k: data_uri(v) for k, v in (i.split("=", 1) for i in a.image)},
        "image_credits": credits,
        "voice": voice_uris(story, a.voice) if a.voice else {},
        "attachments": attachment_uris(story, a.attachments) if a.attachments else {},
    }
    payload = json.dumps(bundle, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    with open(os.path.join(HERE, "template.html")) as f:
        html = f.read()
    marker = "/*__DATA__*/null"
    assert marker in html, "template is missing the data marker"
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w") as f:
        f.write(html.replace(marker, payload, 1))
    print(f"wrote {a.out} ({os.path.getsize(a.out) / 1e6:.1f} MB, {len(story.get('chapters', []))} chapters, "
          f"{sum(len(c['scenes']) for c in story.get('chapters', []))} scenes, {len(bundle['emoji'])} emoji, "
          f"{len(bundle['voice'])} voice clips, {len(bundle['attachments'])} chat images)")


if __name__ == "__main__":
    main()
