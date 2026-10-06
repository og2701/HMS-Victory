"""Voice the replay's narration with OpenAI text-to-speech, and mark each scene with its clip.

    OPENAI_API_KEY=... python3 scripts/bb_chronicle/voiceover.py story.json voice/

Reads the story file assemble.py writes, voices every line the narrator says (scene narration,
chapter cards, the eviction door, the crowning), and writes the clips plus voice/voice.json
(key -> file, text, seconds). The story file is updated in place with "voice" keys for build.py.

Clips are named by a hash of the spoken text and voice settings, so re-running only pays for
lines that changed. The key is read from the environment and never written anywhere.
"""
import concurrent.futures, hashlib, json, os, re, subprocess, sys, urllib.request

MODEL = "gpt-4o-mini-tts"
VOICE = "fable"
INSTRUCTIONS = (
    "Voice: the narrator of a British reality TV show, a British man with a warm, slightly northern English "
    "accent. Tone: dry, deadpan and quietly amused, as if he has seen every row in the house and loves it. "
    "Delivery: brisk and clear, like a TV presenter reading over footage, with a small pause after the day "
    "and time at the start of a line. Comic timing: these lines are jokes, so land the last few words with a "
    "beat of pause before them and a completely straight face. Never shout, never sound like an advert."
)
NAMES = {}


def spoken(text):
    """Written captions to something a narrator would say out loud."""
    t = text
    t = re.sub(r"\b(\d{1,2})\.(\d{2})\s?(am|pm)\b", lambda m: f"{m.group(1)}{'' if m.group(2) == '00' else ':' + m.group(2)} {m.group(3)}", t)
    t = re.sub(r"\b(\d+)\s?-\s?(\d+)\b", r"\1, \2", t)
    t = re.sub(r"\b(\d+)/(\d+)\b", r"\1 out of \2", t)
    t = t.replace("UKPlace", "U K Place").replace("UKP", "U K P").replace("BB ", "Big Brother ")
    return " ".join(t.split())


def key_for(text):
    return hashlib.sha1(f"{MODEL}|{VOICE}|{INSTRUCTIONS}|{text}".encode()).hexdigest()[:16]


def eviction_line(scene):
    if scene.get("nodoor") or not scene.get("evicted"):
        return None
    out = [NAMES.get(i, "") for i in scene["evicted"]]
    who = out[0] if len(out) == 1 else ", ".join(out[:-1]) + " and " + out[-1]
    if scene.get("full_line"):  # the classic line, used once
        return f"{who}, you have been evicted from the Big Brother house. Please leave the house."
    return scene.get("say") or f"{who}, you have been evicted."


def lines(story):
    """(where, text) for everything the narrator says, attaching keys to the story as it goes."""
    want = []
    for ci, ch in enumerate(story["chapters"]):
        text = spoken(f"Chapter {ci + 1}. {ch['title']}.")
        ch["voice"] = key_for(text); want.append((ch["voice"], text))
        for s in ch["scenes"]:
            said = None
            if s["type"] == "open":
                said = f"{s.get('title', '')}. {s.get('sub', '')}"
            elif s.get("narration"):
                said = s["narration"]
            if said:
                text = spoken(said)
                s["voice"] = key_for(text); want.append((s["voice"], text))
            if s["type"] == "evict" and (line := eviction_line(s)):
                text = spoken(line)
                s["voice_say"] = key_for(text); want.append((s["voice_say"], text))
            if s["type"] == "crown":
                text = spoken(f"{NAMES.get(s['winner'], '')}, you have won Big Brother.")
                s["voice_say"] = key_for(text); want.append((s["voice_say"], text))
    return want


def synth(key, text, out_dir):
    path = os.path.join(out_dir, f"{key}.mp3")
    if not os.path.exists(path):
        body = json.dumps({"model": MODEL, "voice": VOICE, "input": text, "instructions": INSTRUCTIONS,
                           "response_format": "mp3"}).encode()
        req = urllib.request.Request("https://api.openai.com/v1/audio/speech", data=body, headers={
            "Authorization": "Bearer " + os.environ["OPENAI_API_KEY"], "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r, open(path + ".part", "wb") as f:
            f.write(r.read())
        os.replace(path + ".part", path)
    info = subprocess.run(["afinfo", path], capture_output=True, text=True).stdout
    secs = float(re.search(r"estimated duration: ([\d.]+)", info).group(1))
    return key, {"file": os.path.basename(path), "text": text, "seconds": round(secs, 2)}


def main():
    story_path, out_dir = sys.argv[1], sys.argv[2]
    os.makedirs(out_dir, exist_ok=True)
    data_dir = os.path.join(os.path.dirname(os.path.abspath(story_path)), "data")
    for h in json.load(open(os.path.join(data_dir, "housemates.json")))["housemates"]:
        NAMES[h["id"]] = h["persona"]
    story = json.load(open(story_path))
    want = dict(lines(story))
    with concurrent.futures.ThreadPoolExecutor(6) as pool:
        manifest = dict(pool.map(lambda kv: synth(kv[0], kv[1], out_dir), want.items()))
    json.dump(manifest, open(os.path.join(out_dir, "voice.json"), "w"), ensure_ascii=False, indent=1)
    json.dump(story, open(story_path, "w"), ensure_ascii=False, indent=1)
    total = sum(v["seconds"] for v in manifest.values())
    print(f"{len(manifest)} clips, {total / 60:.1f} minutes of narration")


if __name__ == "__main__":
    main()
