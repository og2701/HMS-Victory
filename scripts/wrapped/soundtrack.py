"""The Wrapped soundtrack: find where things happen in each song, then mix the songs under the video.

    ~/.venv/bin/python scripts/wrapped/soundtrack.py analyze <music dir>
    ~/.venv/bin/python scripts/wrapped/soundtrack.py mix <music dir> <timeline.json> <video.mp4> <out.mp4>

analyze: for each song in the music dir, works out the tempo and beat grid, and lines up time-synced lyrics
(lyrics/<name>.json, from lrclib) against the actual file - which may be a music-video cut with an intro
the album doesn't have - by sliding the lyric line starts along the vocal-band onset curve until they fit
best. Writes <name>.analysis.json with the beats and every lyric line at its real time in this file, so
cues can be written against lines ("start at 'And so Sally can wait'") and land on the beat.

mix: reads cues.json from the music dir and the scene timeline render.mjs writes, and builds the audio:
each cue plays a song from a point in it, over a span of scenes, with fades, dipping under any scene
listed in its "duck"; stings and sound effects drop in at a scene's start (or every matching scene's,
with "each"); a "muffle" cue low-passes the songs inside matching scenes, as if heard from the next
room. Cues name scenes, not seconds, so the soundtrack follows the video when scene lengths change.

Needs numpy and scipy (the analysis), and ffmpeg.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

SR = 22050
HOP = 512


def decode(path):
    import numpy as np
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32)


def onset_curves(y):
    """Spectral flux in the whole band and in the vocal band, one value per hop."""
    import numpy as np
    from scipy.signal import stft
    f, t, Z = stft(y, fs=SR, nperseg=2048, noverlap=2048 - HOP, boundary=None)
    mag = np.log1p(np.abs(Z) * 100)
    flux = np.maximum(np.diff(mag, axis=1), 0)
    vocal = (f >= 300) & (f <= 3400)
    full = flux.sum(axis=0)
    voc = flux[vocal].sum(axis=0)
    rms = np.sqrt((np.abs(Z) ** 2).mean(axis=0))[1:]
    return full, voc, rms


def tempo_and_beats(onset):
    """Tempo by autocorrelation of the onset curve, then the beat phase that lands on the most onsets."""
    import numpy as np
    o = (onset - onset.mean()) / (onset.std() + 1e-9)
    fps = SR / HOP
    ac = np.correlate(o, o, mode="full")[len(o) - 1:]
    lo, hi = int(fps * 60 / 180), int(fps * 60 / 70)
    lag = lo + int(np.argmax(ac[lo:hi]))
    bpm = 60 * fps / lag
    phases = [o[p::lag].sum() for p in range(lag)]
    p = int(np.argmax(phases))
    beats = np.arange(p, len(o), lag) / fps
    return round(float(bpm), 1), [round(float(b), 3) for b in beats]


def parse_lrc(text):
    lines = []
    for line in text.splitlines():
        m = re.match(r"\[(\d+):(\d+(?:\.\d+)?)\]\s*(.*)", line)
        if m and m.group(3).strip():
            lines.append((int(m.group(1)) * 60 + float(m.group(2)), m.group(3).strip()))
    return lines


def align(lines, vocal_onset, lo=-15.0, hi=45.0):
    """Slide the lyric line starts along the vocal onset curve; keep the shift where they hit hardest.
    Songs repeat themselves, so the shift is kept to what the two versions' lengths allow."""
    import numpy as np
    fps = SR / HOP
    v = np.convolve((vocal_onset - vocal_onset.mean()) / (vocal_onset.std() + 1e-9), np.ones(5) / 5, mode="same")
    starts = np.array([t for t, _ in lines])
    best = (-1e9, 0.0)
    for shift in np.arange(lo, hi, 0.04):
        idx = ((starts + shift) * fps).astype(int)
        idx = idx[(idx >= 0) & (idx < len(v))]
        if len(idx) < len(starts) * 0.8:
            continue
        # a line starting is a rise in vocal energy just after its timestamp
        score = sum(v[i:i + 8].max() for i in idx) / len(idx)
        if score > best[0]:
            best = (score, float(shift))
    return best


def analyze(music_dir):
    import numpy as np
    music_dir = Path(music_dir)
    for path in sorted(music_dir.glob("*.mp3")):
        y = decode(path)
        duration = len(y) / SR
        full, voc, rms = onset_curves(y)
        bpm, beats = tempo_and_beats(full)
        loud = [round(float(20 * np.log10(rms[int(i * SR / HOP):int((i + 1) * SR / HOP)].mean() + 1e-9)), 1)
                for i in range(int(duration))]
        out = {"file": path.name, "duration": round(duration, 2), "bpm": bpm, "beats": beats, "loudness": loud}
        lyr = music_dir / "lyrics" / f"{path.stem}.json"
        if lyr.exists():
            best = None
            for cand in json.loads(lyr.read_text()):
                lines = parse_lrc(cand["syncedLyrics"])
                if len(lines) < 4:
                    continue
                # Bounded by the lyrics themselves (lrclib's duration field isn't reliable): the first line
                # can't start before the file does, and the last has to start before it ends.
                lo, hi = -lines[0][0] + 0.5, duration - lines[-1][0] + 5.0
                if hi < lo:
                    continue
                score, shift = align(lines, voc, lo, hi + 0.04)
                if not best or score > best[0]:
                    best = (score, shift, lines, cand.get("duration"))
            if best:
                score, shift, lines, cand_dur = best
                out["lyrics_shift"] = round(shift, 2)
                out["lyrics_fit"] = round(score, 2)
                out["lines"] = [[round(t + shift, 2), text] for t, text in lines]
        (music_dir / f"{path.stem}.analysis.json").write_text(json.dumps(out, ensure_ascii=False))
        print(f"{path.stem:26} {duration:6.1f}s  {bpm:5.1f} bpm  "
              f"lyrics {'shift ' + str(out.get('lyrics_shift')) + 's fit ' + str(out.get('lyrics_fit')) if 'lines' in out else '-'}")


# ---------------------------------------------------------------- mixing
# Sound effects made from scratch, so nothing has to be fetched: a Big Ben strike (bell partials on E,
# each dying away at its own rate) and the UK Emergency Alert: both tones at once (853 and 960 Hz), on
# for a long blast and two short ones, with the phone's vibration buzzing under it.
SFX = {
    "bigben": ("aevalsrc='0.9*exp(-0.55*t)*(0.55*sin(2*PI*82.4*t)+0.9*sin(2*PI*164.8*t)+0.55*sin(2*PI*197.8*t)"
               "+0.4*sin(2*PI*247.2*t)+0.6*exp(-0.9*t)*sin(2*PI*329.6*t)+0.25*exp(-2.2*t)*sin(2*PI*415.3*t)"
               "+0.15*exp(-4*t)*sin(2*PI*659.3*t))':s=44100:d=8"),
    "alert": ("aevalsrc='(0.33*sin(2*PI*853*t)+0.33*sin(2*PI*960*t))"
              "*(clip(min(t,1.6-t)/0.006,0,1)+clip(min(t-1.85,2.35-t)/0.006,0,1)+clip(min(t-2.6,3.1-t)/0.006,0,1))"
              "+0.2*sin(2*PI*150*t)*gt(sin(2*PI*7*t),0)*lt(t,3.1)':s=44100:d=3.2"),
    # a soft two-note message chime, C6 then G6, each note with a quieter octave on top
    "ping": ("aevalsrc='lt(t,0.09)*exp(-14*t)*(0.5*sin(2*PI*1046.5*t)+0.12*sin(2*PI*2093*t))"
             "+gte(t,0.09)*exp(-9*(t-0.09))*(0.5*sin(2*PI*1568*t)+0.1*sin(2*PI*3136*t))"
             "*min((t-0.09)/0.004,1)':s=44100:d=0.8"),
}


TARGET_DB = -18.0  # every song is levelled to this, measured over the stretch actually used


def level(path, offset, length):
    """RMS level of a stretch of a file in dB, ignoring silences, so a quiet intro doesn't skew it."""
    import numpy as np
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{offset:.3f}", "-t", f"{length:.3f}", "-i", str(path),
                          "-ac", "1", "-ar", "8000", "-f", "f32le", "-"], capture_output=True, check=True).stdout
    y = np.frombuffer(raw, dtype=np.float32)
    win = [y[i:i + 8000] for i in range(0, len(y) - 8000, 8000)] or [y]
    ms = np.array([float((w ** 2).mean()) for w in win])
    ms = ms[ms > 1e-5] if (ms > 1e-5).any() else ms
    return 10 * np.log10(ms.mean() + 1e-12)


def scene_times(timeline):
    return {s["id"]: (s["start"], s["start"] + s["dur"]) for s in timeline["scenes"]}


def matching(times, pattern):
    rx = re.compile("^" + re.escape(pattern).replace(r"\*", ".*") + "$")
    return sorted((v for k, v in times.items() if rx.match(k)), key=lambda v: v[0])


OPEN_HZ = 20000.0   # the low-pass when a song plays clear
SWEEP_TO = 150.0    # where a song's low-pass ends up as it hands over to the next one
RAMP = 0.35         # seconds to sweep into and out of a muffle
ONSET_LAG = 2048 / SR  # the onset curve peaks this long before the hit itself (a full STFT window)


def low_hits(path, cache={}):
    """Onsets in the bass (kick drums, bass notes), one value per hop: the big ones are downbeats."""
    import numpy as np
    from scipy.signal import stft
    if path not in cache:
        f, t, Z = stft(decode(path), fs=SR, nperseg=2048, noverlap=2048 - HOP, boundary=None)
        flux = np.maximum(np.diff(np.log1p(np.abs(Z) * 100), axis=1), 0)[f < 200].sum(axis=0)
        cache[path] = np.convolve(flux, np.ones(3) / 3, mode="same")
    return cache[path]


def strongest_hit(path, lo, hi):
    """Song time of the biggest bass hit between lo and hi (song seconds)."""
    import numpy as np
    curve = low_hits(path)
    a, b = max(int((lo - ONSET_LAG) * SR / HOP), 0), max(int((hi - ONSET_LAG) * SR / HOP), 1)
    return (a + int(np.argmax(curve[a:b]))) * HOP / SR + ONSET_LAG


def mix(music_dir, timeline_path, video, out):
    import math
    import tempfile
    music_dir = Path(music_dir)
    cues = json.loads((music_dir / "cues.json").read_text())
    timeline = json.loads(Path(timeline_path).read_text())
    times = scene_times(timeline)
    order = [sc["id"] for sc in timeline["scenes"]]
    total = timeline["total"]
    inputs, chains = [], []
    tmp = tempfile.TemporaryDirectory()
    AUDIO = (".mp3", ".m4a", ".wav", ".flac", ".aac", ".ogg", ".opus")

    def add(src_args, start, length, fade_in, fade_out, gain_db, duck=(), duck_db=-15.0, cutoff=None, fade_at=None):
        k = len(chains)  # one input per chain, so the chain number is the input number
        inputs.extend(src_args)
        f = [f"atrim=0:{length:.3f}", "asetpts=PTS-STARTPTS", "aresample=44100", "aformat=channel_layouts=stereo"]
        if cutoff:
            # a two-stage low-pass whose frequency follows the schedule; small frames so it moves smoothly
            cmds = Path(tmp.name) / f"cutoff{k}.txt"
            cmds.write_text("".join(f"{t:.3f} lowpass@a{k} f {hz:.0f}, lowpass@b{k} f {hz:.0f};\n" for t, hz in cutoff))
            f += ["asetnsamples=n=256", f"asendcmd=f={cmds}",
                  f"lowpass@a{k}=f={OPEN_HZ:.0f}:transform=zdf", f"lowpass@b{k}=f={OPEN_HZ:.0f}:transform=zdf"]
        if fade_in:
            f.append(f"afade=t=in:st=0:d={fade_in}")
        if fade_out:
            st = fade_at if fade_at is not None else max(length - fade_out, 0)
            f.append(f"afade=t=out:st={st:.3f}:d={fade_out:.3f}")
        f.append(f"volume={10 ** (gain_db / 20):.4f}")
        ms = int(round(start * 1000))
        f.append(f"adelay={ms}|{ms}")
        if duck:
            # dip inside each ducked span (by its own dB, or duck_db), ramping over a third of a second each side
            terms = "*".join(f"(1-{1 - 10 ** ((d[2] if len(d) > 2 else duck_db) / 20):.3f}"
                             f"*clip((t-{d[0]:.3f})/0.3,0,1)*clip(({d[1]:.3f}-t)/0.3,0,1))" for d in duck)
            f.append(f"volume='{terms}':eval=frame")
        chains.append(f"[{k}:a]" + ",".join(f) + f"[c{k}]")

    muffle, songs, sfx_ducks = None, [], []
    for cue in cues:
        if "muffle" in cue:
            muffle = cue
            continue
        if "sfx" in cue:
            # at one scene, or at every scene matching "each"
            spans = matching(times, cue["each"]) if "each" in cue else [times[cue["scene"]]]
            for a, _ in spans:
                add(["-f", "lavfi", "-i", SFX[cue["sfx"]]], a + cue.get("at", 0), 8, 0, 0.4, cue.get("gain", 0))
                if "duck" in cue:  # the songs dip under this one while it plays
                    at = a + cue.get("at", 0)
                    sfx_ducks.append((at, at + float(re.search(r":d=([\d.]+)", SFX[cue["sfx"]]).group(1)), cue["duck"]))
            continue
        path = next(p for p in music_dir.glob(cue["song"] + ".*") if p.suffix.lower() in AUDIO)
        if "each" in cue:
            # a short sting at the start of every matching scene, each from the next offset in the list
            for n, (a, b) in enumerate(matching(times, cue["each"])):
                off = cue["offsets"][n % len(cue["offsets"])]
                length = min(b - a, cue.get("max", 9.0))
                gain = TARGET_DB - level(path, off, length) + cue.get("gain", 0)
                add(["-ss", f"{off}", "-i", str(path)], a, length, cue.get("fade_in", 0.1), cue.get("fade_out", 0.6), gain)
            continue
        start = times[cue["from"]][0] - cue.get("lead", 0)
        end = times[cue["to"]][1] + cue.get("tail", 0)
        # a Meanwhile straight after the cue's last scene is part of it: the music carries on under it
        i = order.index(cue["to"]) + 1
        while i < len(order) and order[i].startswith("meanwhile-"):
            end = times[order[i]][1]
            i += 1
        song_t, scene, into = cue["sync"]
        offset = song_t - (times[scene][0] + into - start)
        if offset < 0:  # the song can't start before its own beginning: start the cue later instead
            start -= offset
            offset = 0
        songs.append({"cue": cue, "path": path, "start": start, "end": end, "offset": offset,
                      "fade_in": cue.get("fade_in", 0.4), "fade_out": cue.get("fade_out", 1.2), "sweep": None,
                      "gain": TARGET_DB - level(path, offset, end - start) + cue.get("gain", 0)})

    # Handovers: where one song ends as the next begins, unless the next is meant to cut in. The old song
    # plays to its last big bass hit before the cut, then sweeps down a low-pass and fades, out from under
    # the new one, which comes in on a hit of its own as the scene lands (a chapter card's number slams
    # in a quarter of a second in). Neither song moves in time, so every synced moment stays put.
    songs.sort(key=lambda s: s["start"])
    for p, q in zip(songs, songs[1:]):
        if abs(q["start"] - p["end"]) > 0.05 or q["cue"].get("cut"):
            continue
        cut = p["end"]
        landing = next((sid for sid in order if abs(times[sid][0] - cut) < 0.05), "")
        land = cut + (0.25 if landing.startswith("ch-") else 0)
        if q["offset"] > 0.5:  # mid-song: come in on the biggest hit near the landing
            at = q["offset"] + (land - q["start"])
            hit = strongest_hit(q["path"], at - 0.25, at + 0.45) - 0.03
            q["offset"], q["start"] = hit, q["start"] + (hit - q["offset"])
            q["fade_in"] = 0.02
        sp = lambda t: p["offset"] + (t - p["start"])
        last = strongest_hit(p["path"], sp(cut - 2.2), sp(cut - 0.5)) + p["start"] - p["offset"]
        sweep_from = last + 0.12
        p["end"] = max(q["start"] + 0.7, sweep_from + 1.5)
        p["sweep"] = (sweep_from, p["end"])
        print(f"  handover {p['cue']['song']} > {q['cue']['song']}: last hit {last:.2f}s, "
              f"sweeps out by {p['end']:.2f}s, next in at {q['start']:.2f}s ({q['start'] - cut:+.2f}s)")

    spans = matching(times, muffle["muffle"]) if muffle else []
    for s in songs:
        cue, start, end = s["cue"], s["start"], s["end"]
        # The low-pass schedule: clear, swept down to the muffle inside a muffled scene and back up after
        # it (held, if the song ends or hands over before it would open again), then the handover sweep.
        lo_open, lo_muf, lo_end = math.log(OPEN_HZ), math.log(muffle.get("cutoff", 500) if muffle else OPEN_HZ), math.log(SWEEP_TO)
        mine = [(a, b) for a, b in spans if a < end and b > start]
        sweep = s["sweep"]

        def logf(t):
            w = 0.0
            for a, b in mine:
                held = b >= end - 0.05 or (sweep and b >= sweep[0] - 0.01)  # the song ends muffled
                w = max(w, min(max((t - a) / RAMP, 0), 1) * (1 if held else min(max((b - t) / RAMP, 0), 1)))
            return lo_open + w * (lo_muf - lo_open)

        cutoff, prev = [], None
        steps = int((end - start) / 0.01) + 1
        for n in range(steps):
            t = start + n * 0.01
            v = logf(t)
            if sweep and t >= sweep[0]:
                x = min((t - sweep[0]) / (sweep[1] - sweep[0]), 1)
                v = logf(sweep[0]) + (1 - (1 - x) ** 2) * (lo_end - logf(sweep[0]))
            if prev is None or abs(v - prev) > 0.004:
                cutoff.append((n * 0.01, math.exp(v)))
                prev = v
        print(f"  {cue['song']:26} {start:7.2f}s +{end - start:6.2f}s from {s['offset']:7.2f}s  gain {s['gain']:+.1f}dB")
        fade_out, fade_at = s["fade_out"], None
        if sweep:
            fade_at, fade_out = sweep[0] - start, sweep[1] - sweep[0]
        duck = [v for pat in cue.get("duck", []) for v in matching(times, pat) if v[0] < end and v[1] > start]
        duck += [d for d in sfx_ducks if d[0] < end and d[1] > start]
        add(["-ss", f"{s['offset']:.3f}", "-i", str(s["path"])], start, end - start, s["fade_in"], fade_out, s["gain"],
            duck, cue.get("duck_db", -15.0), cutoff if (mine or sweep) else None, fade_at)

    n = len(chains)
    graph = ";".join(chains) + ";" + "".join(f"[c{k}]" for k in range(n)) + \
        f"amix=inputs={n}:normalize=0:dropout_transition=0,alimiter=limit=0.8:level=false:attack=5:release=80,atrim=0:{total:.3f}[a]"
    audio = Path(out).with_suffix(".m4a")
    subprocess.run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", graph, "-map", "[a]", "-c:a", "aac",
                    "-b:a", "192k", str(audio)], check=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-i", str(audio), "-map", "0:v", "-map", "1:a",
                    "-c:v", "copy", "-c:a", "copy", "-shortest", "-movflags", "+faststart", str(out)], check=True)
    audio.unlink()
    tmp.cleanup()
    print(f"wrote {out}: {n} audio cues over {total:.1f}s")


if __name__ == "__main__":
    if sys.argv[1] == "analyze":
        analyze(sys.argv[2])
    elif sys.argv[1] == "mix":
        mix(*sys.argv[2:6])
