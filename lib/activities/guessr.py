"""UKGuessr: three spots somewhere in the UK each day, the same for everyone. Each is a 360° street photo from
Mapillary (openly licensed, CC BY-SA, so the bot keeps the day's three and serves them itself rather than every
player fetching them), and you drop a pin on a map of the UK for where you think it is. A round scores up to
5,000, less the further off the pin is; once you've guessed, the map shows where it was and where everyone else
has guessed so far. The day's total goes on today's leaderboard and on the all-time one, which adds up every
day's; finishing all three pays a flat GUESSR_REWARD UKP and posts the day's card (guessr_card) in the channel the
game was opened in.

The bot picks the spots (pick_day): on land in the UK (data/guessr/uk-land.json, made by ukplace-activities
scripts/guessr-map.py from Natural Earth's public-domain map), half near a city and half anywhere, each a recent
Mapillary 360° photo, and spread out round the country. Today's and tomorrow's photos are kept on disk
(photo_dir); older days' are deleted, while the spots and everyone's guesses stay in the database.

The map players pin on is OpenStreetMap's, from OpenFreeMap (free, no key). Inside Discord the page can't reach
other sites, so it asks the bot for each of the map's files (map_file): fetched once, then served from a copy on
disk that's trimmed back to MAP_CACHE_MB, least used first.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import math
import os
import random
import re
import shutil
import time
from datetime import date as _date, timedelta
from pathlib import Path
from urllib.parse import quote

import config
from database import DatabaseManager

log = logging.getLogger(__name__)

ROUNDS = 3
MAX_SCORE = 5000
SCALE_KM = 130                 # a pin this far off scores MAX_SCORE / e; 10 km off still scores ~4,600
MIN_APART_KM = 120             # the day's spots are spread out this far at least
LAND_FILE = Path(__file__).resolve().parents[2] / "data" / "guessr" / "uk-land.json"
API = "https://graph.mapillary.com/images"
FIELDS = "id,computed_geometry,geometry,thumb_2048_url,captured_at,creator,is_pano"
OLDEST_MS = 1451606400000      # photos from 2016 on, so the streets look like today's
MAP_HOST = "https://tiles.openfreemap.org/"
MAP_CACHE_MB = 1000
_MAP_FILE = re.compile(
    r"styles/liberty|planet"                                    # these two change, so they're fetched again daily
    r"|planet/\d{8}_\d{6}_pt/\d{1,2}/\d{1,6}/\d{1,6}\.pbf"
    r"|fonts/[A-Za-z0-9 ,_-]{1,80}/\d{1,5}-\d{1,5}\.pbf"
    r"|sprites/[a-z0-9_]{1,40}/ofm(@2x)?\.(json|png)"
    r"|natural_earth/ne2sr/\d{1,2}/\d{1,6}/\d{1,6}\.png")
_map_fetching: dict[str, asyncio.Task] = {}
_posting: set[asyncio.Task] = set()
_map_session = None
_picking: dict[str, asyncio.Task] = {}


class Refuse(Exception):
    """A guess the rules won't take, with why (for the player)."""


def reward() -> int:
    return int(getattr(config, "GUESSR_REWARD", 10))


def allowed(uid) -> bool:
    """Everyone once UKGuessr's live (config.GUESSR_LIVE), until then only its testers."""
    if not getattr(config, "GUESSR_ENABLED", True):
        return False
    return bool(getattr(config, "GUESSR_LIVE", False)) or int(uid or 0) in getattr(config, "GUESSR_TESTERS", [])


def token() -> str:
    return os.getenv("MAPILLARY_TOKEN") or str(getattr(config, "MAPILLARY_TOKEN", "") or "")


def photo_dir() -> Path:
    return Path(config.JSON_DATA_DIR) / "guessr"


# ---- the map ---------------------------------------------------------------------------------------

_land: dict | None = None


def _load() -> dict:
    global _land
    if _land is None:
        d = json.loads(LAND_FILE.read_text())
        rings = []
        for ring in d["rings"]:
            lons, lats = [p[0] for p in ring], [p[1] for p in ring]
            rings.append((min(lons), min(lats), max(lons), max(lats), ring))
        _land = {"rings": rings, "cities": d["cities"]}
    return _land


def on_land(lat: float, lon: float) -> bool:
    """Whether a point is on UK land (even-odd over every ring, so lakes and the like count as holes)."""
    inside = False
    for x0, y0, x1, y1, ring in _load()["rings"]:
        if not (x0 <= lon <= x1 and y0 <= lat <= y1):
            continue
        j = len(ring) - 1
        for i in range(len(ring)):
            xi, yi = ring[i]
            xj, yj = ring[j]
            if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
                inside = not inside
            j = i
    return inside


def km_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * math.asin(min(1.0, math.sqrt(a)))


def score_for(km: float) -> int:
    return MAX_SCORE if km < 0.05 else int(round(MAX_SCORE * math.exp(-km / SCALE_KM)))


def _sample(r: random.Random) -> tuple[float, float]:
    """A point to look for a photo near: half the time near a city (the bigger, the likelier), else anywhere."""
    land = _load()
    if r.random() < 0.5:
        cities = land["cities"]
        c = r.choices(cities, weights=[math.sqrt(max(1, x["pop"])) for x in cities])[0]
        d, a = 15 * math.sqrt(r.random()), r.random() * 2 * math.pi
        return c["lat"] + d * math.sin(a) / 111.0, c["lon"] + d * math.cos(a) / (111.0 * math.cos(math.radians(c["lat"])))
    for _ in range(200):
        lat, lon = r.uniform(49.9, 60.9), r.uniform(-8.2, 1.8)
        if on_land(lat, lon):
            return lat, lon
    return 52.48, -1.9


# ---- picking the day's spots -------------------------------------------------------------------------

async def _find(session, lat: float, lon: float, r: random.Random) -> dict | None:
    """A recent 360° photo near a point, on UK land, if Mapillary has one."""
    for half in (0.02, 0.05):
        bbox = f"{lon - half * 1.7:.5f},{lat - half:.5f},{lon + half * 1.7:.5f},{lat + half:.5f}"
        params = {"fields": FIELDS, "bbox": bbox, "is_pano": "true", "limit": "40"}
        try:
            # the token in a header, not the URL, so it never turns up in a logged error
            async with session.get(API, params=params, headers={"Authorization": f"OAuth {token()}"}, timeout=20) as resp:
                if resp.status != 200:
                    log.warning("Mapillary said %s near %.3f,%.3f", resp.status, lat, lon)
                    return None
                data = (await resp.json()).get("data") or []
        except Exception:
            log.warning("couldn't ask Mapillary near %.3f,%.3f", lat, lon, exc_info=True)
            return None
        found = []
        for im in data:
            geo = (im.get("computed_geometry") or im.get("geometry") or {}).get("coordinates")
            if not geo or not im.get("thumb_2048_url") or not im.get("is_pano") or int(im.get("captured_at") or 0) < OLDEST_MS:
                continue
            plon, plat = float(geo[0]), float(geo[1])
            if on_land(plat, plon):
                found.append({"id": str(im["id"]), "lat": round(plat, 6), "lon": round(plon, 6), "url": im["thumb_2048_url"],
                              "by": (im.get("creator") or {}).get("username") or "a Mapillary contributor",
                              "at": int(im.get("captured_at") or 0)})
        if found:
            return r.choice(found)
    return None


async def _save(session, url: str, dest: Path) -> bool:
    """Download a photo and save it re-encoded (which also drops anything stored inside it, like where it was)."""
    try:
        async with session.get(url, timeout=40) as resp:
            if resp.status != 200:
                return False
            raw = await resp.read()
        from PIL import Image
        im = Image.open(io.BytesIO(raw)).convert("RGB")
        if im.width > 2048:
            im = im.resize((2048, round(im.height * 2048 / im.width)))
        dest.parent.mkdir(parents=True, exist_ok=True)
        im.save(dest, "JPEG", quality=84)
        return True
    except Exception:
        log.warning("couldn't save a Mapillary photo", exc_info=True)
        return False


def spots(iso: str) -> list[dict] | None:
    row = DatabaseManager.fetch_one("SELECT spots FROM guessr_days WHERE date = ?", (iso,))
    return json.loads(row[0]) if row else None


async def pick_day(iso: str) -> list[dict] | None:
    """Pick (once) the day's three spots and save their photos."""
    have = spots(iso)
    if have is not None:
        return have
    if not token():
        return _test_day(iso) if getattr(config, "GUESSR_TEST_SPOTS", False) else None
    import aiohttp
    r = random.Random(f"guessr:{iso}:{time.time()}")
    chosen: list[dict] = []
    async with aiohttp.ClientSession() as session:
        for _ in range(80):
            if len(chosen) == ROUNDS:
                break
            lat, lon = _sample(r)
            if any(km_between(lat, lon, s["lat"], s["lon"]) < MIN_APART_KM for s in chosen):
                continue
            found = await _find(session, lat, lon, r)
            if not found or any(km_between(found["lat"], found["lon"], s["lat"], s["lon"]) < MIN_APART_KM for s in chosen):
                continue
            n = len(chosen) + 1
            if await _save(session, found.pop("url"), photo_dir() / iso / f"{n}.jpg"):
                chosen.append({"n": n, **found})
    if len(chosen) < ROUNDS:
        log.error("UKGuessr couldn't find %s spots for %s (found %s)", ROUNDS, iso, len(chosen))
        return None
    DatabaseManager.execute("INSERT OR IGNORE INTO guessr_days (date, spots) VALUES (?, ?)", (iso, json.dumps(chosen)))
    log.info("UKGuessr picked %s: %s", iso, ", ".join(f"{s['lat']:.3f},{s['lon']:.3f}" for s in chosen))
    return spots(iso)


def _test_day(iso: str) -> list[dict]:
    """Without a Mapillary token (developing): three known spots with plain test panoramas."""
    from PIL import Image, ImageDraw
    known = [(51.5007, -0.1246, "Westminster"), (55.9486, -3.1999, "Edinburgh Castle"), (53.0685, -4.0763, "Snowdon")]
    out = []
    for n, (lat, lon, name) in enumerate(known, 1):
        dest = photo_dir() / iso / f"{n}.jpg"
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            im = Image.new("RGB", (2048, 1024))
            d = ImageDraw.Draw(im)
            for y in range(1024):
                c = (90 + y // 8, 150 + y // 12, 220) if y < 512 else (70 + (y - 512) // 10, 110, 70)
                d.line([(0, y), (2048, y)], fill=c)
            for x in range(0, 2048, 128):
                d.line([(x, 0), (x, 1024)], fill=(255, 255, 255), width=2)
                d.text((x + 8, 500), f"TEST {n} · {x * 360 // 2048}°", fill=(0, 0, 0))
            im.save(dest, "JPEG", quality=80)
        out.append({"n": n, "id": f"test{n}", "lat": lat, "lon": lon, "by": f"test ({name})", "at": 0})
    DatabaseManager.execute("INSERT OR IGNORE INTO guessr_days (date, spots) VALUES (?, ?)", (iso, json.dumps(out)))
    return out


def tidy(today: str) -> None:
    """Delete the photos of days before today (tomorrow's are kept, picked ahead)."""
    base = photo_dir()
    if not base.exists():
        return
    for d in base.iterdir():
        if d.is_dir() and d.name < today:
            shutil.rmtree(d, ignore_errors=True)


def ensure(iso: str) -> None:
    """Start picking a day's spots if nobody has yet (it takes a little while: the screen says it's on its way)."""
    if spots(iso) is not None or (iso in _picking and not _picking[iso].done()):
        return
    try:
        _picking[iso] = asyncio.get_running_loop().create_task(pick_day(iso))
    except RuntimeError:
        pass


async def run_sweeper(today) -> None:
    """Keeps today's and tomorrow's spots picked ahead, and clears out old photos."""
    while True:
        try:
            d = today()
            for iso in (d.isoformat(), (d + timedelta(days=1)).isoformat()):
                if spots(iso) is None:
                    await pick_day(iso)
            tidy(d.isoformat())
            await asyncio.to_thread(trim_map)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.error("UKGuessr's sweeper slipped", exc_info=True)
        await asyncio.sleep(20 * 60)


# ---- playing -------------------------------------------------------------------------------------------

def _guesses(uid: int, iso: str) -> dict[int, dict]:
    rows = DatabaseManager.fetch_all(
        "SELECT round, lat, lon, km, score FROM guessr_guesses WHERE user_id = ? AND date = ? ORDER BY round", (str(uid), iso))
    return {int(r[0]): {"lat": r[1], "lon": r[2], "km": round(float(r[3]), 1), "score": int(r[4])} for r in rows or []}


def opened(client, uid: int, date: _date) -> None:
    """(Opening the game needs nothing doing: the spots are picked ahead.)"""


def state(uid: int, date: _date) -> dict:
    iso = date.isoformat()
    day = spots(iso)
    if day is None:
        ensure(iso)
    mine = _guesses(uid, iso)
    rounds = []
    for n in range(1, ROUNDS + 1):
        g = mine.get(n)
        s = day[n - 1] if day else None
        seen = day is not None and (g is not None or n == len(mine) + 1)
        rounds.append({"n": n, "photo": f"guessr/photo/{iso}/{n}" if seen else None, "guess": g,
                       "answer": {"lat": s["lat"], "lon": s["lon"], "by": s["by"], "id": s["id"]} if g and s else None})
    total = sum(g["score"] for g in mine.values())
    paid = DatabaseManager.fetch_one("SELECT 1 FROM guessr_paid WHERE user_id = ? AND date = ?", (str(uid), iso)) is not None
    played = DatabaseManager.fetch_one("SELECT COUNT(DISTINCT user_id) FROM guessr_guesses WHERE date = ?", (iso,))
    return {"game": "guessr", "date": iso, "dateLabel": f"{date:%A} {date.day} {date:%B}", "ready": day is not None,
            "rounds": rounds, "total": total, "max": ROUNDS * MAX_SCORE, "done": len(mine) >= ROUNDS,
            "reward": reward(), "paid": paid, "players": int(played[0]) if played else 0}


def guess(uid: int, date: _date, n: int, lat: float, lon: float) -> dict:
    """Pin a round: the score, and the day paid for once all three are in."""
    iso = date.isoformat()
    day = spots(iso)
    if day is None:
        raise Refuse("Today's spots aren't ready yet. Give it a minute.")
    mine = _guesses(uid, iso)
    if n != len(mine) + 1 or n > ROUNDS:
        raise Refuse("That round's already been guessed." if n in mine else "Guess the rounds in order.")
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        raise Refuse("Drop a pin on the map first.")
    if not (48 <= lat <= 62 and -12 <= lon <= 3):
        raise Refuse("Drop your pin on the UK.")
    s = day[n - 1]
    km = km_between(lat, lon, s["lat"], s["lon"])
    DatabaseManager.execute(
        "INSERT OR IGNORE INTO guessr_guesses (user_id, date, round, lat, lon, km, score, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (str(uid), iso, n, round(lat, 5), round(lon, 5), km, score_for(km), int(time.time())))
    if n == ROUNDS:
        _pay(uid, iso)
    return state(uid, date)


def _pay(uid: int, iso: str) -> None:
    if DatabaseManager.fetch_one("SELECT 1 FROM guessr_paid WHERE user_id = ? AND date = ?", (str(uid), iso)):
        return
    from lib.economy.economy_manager import add_bb
    if add_bb(uid, reward(), reason="UKGuessr", taxable=False, discretionary=True):
        DatabaseManager.execute("INSERT OR IGNORE INTO guessr_paid (user_id, date, paid) VALUES (?, ?, ?)", (str(uid), iso, reward()))


def pins(viewer: int, date: _date, n: int, name) -> list[dict]:
    """Everyone's guesses on a round so far, once the viewer has guessed it themselves."""
    iso = date.isoformat()
    if n not in _guesses(viewer, iso):
        raise Refuse("Guess this one yourself first.")
    rows = DatabaseManager.fetch_all(
        "SELECT user_id, lat, lon, km, score FROM guessr_guesses WHERE date = ? AND round = ? ORDER BY score DESC", (iso, n))
    return [{"uid": r[0], "name": "You" if r[0] == str(viewer) else (name(int(r[0])) or "Someone"), "lat": r[1], "lon": r[2],
             "km": round(float(r[3]), 1), "score": int(r[4]), "me": r[0] == str(viewer)} for r in rows or []]


def board(viewer: int, date: _date, name, top: int = 10) -> dict:
    """Today's leaderboard (the day's totals, finished or not) and the all-time one (every day's added up)."""
    iso = date.isoformat()
    def rows(sql: str, args: tuple) -> dict:
        got = DatabaseManager.fetch_all(sql, args) or []
        ranked = [{"uid": r[0], "name": "You" if r[0] == str(viewer) else (name(int(r[0])) or "Someone"), "score": int(r[1]),
                   "rank": i + 1, "me": r[0] == str(viewer)} for i, r in enumerate(got)]
        you = next((x for x in ranked if x["me"]), None)
        return {"rows": ranked[:top], "you": you, "players": len(ranked)}
    return {
        "today": rows("SELECT user_id, SUM(score) AS s FROM guessr_guesses WHERE date = ? GROUP BY user_id ORDER BY s DESC", (iso,)),
        "allTime": rows("SELECT user_id, SUM(score) AS s FROM guessr_guesses GROUP BY user_id ORDER BY s DESC", ()),
    }


def photo(viewer: int, date: _date, iso: str, n: int) -> Path | None:
    """A round's photo, for someone who's got to that round today (not before, so nobody can peek ahead)."""
    if iso != date.isoformat() or not 1 <= n <= ROUNDS or spots(iso) is None:
        return None
    if n > len(_guesses(viewer, iso)) + 1:
        return None
    path = photo_dir() / iso / f"{n}.jpg"
    return path if path.exists() else None


# ---- telling the channel ------------------------------------------------------------------------------

def post_day(uid: int, channel_id: int, date: _date) -> None:
    """Once someone's pinned all three, say so in the channel the game was opened in: a line with a Play button
    and the day's card (which never shows where the spots were). In the background, and best-effort."""
    async def send():
        from lib.activities import guessr_card, launcher
        from lib.activities.daily_score import _member, ordinal
        s = state(uid, date)
        if not s["done"]:
            return
        today = DatabaseManager.fetch_all(
            "SELECT user_id, SUM(score) AS s FROM guessr_guesses WHERE date = ? GROUP BY user_id ORDER BY s DESC, user_id",
            (date.isoformat(),)) or []
        rank = next((i + 1 for i, r in enumerate(today) if r[0] == str(uid)), len(today))
        member = _member(uid)
        avatar = None
        if member is not None:
            try:
                avatar = await member.display_avatar.replace(size=256, format="png").read()
            except Exception:
                avatar = None
        called = lambda u: getattr(_member(int(u)), "display_name", None) or "A guesser"
        paid = s["reward"] if s["paid"] else 0
        png = await guessr_card.card_png(
            name=called(uid), avatar=avatar, date_label=s["dateLabel"], total=s["total"], most=s["max"],
            rounds=[(r["guess"]["score"], r["guess"]["km"]) for r in s["rounds"]], rank=rank, players=len(today),
            paid=paid, top=[(called(u), int(t), u == str(uid)) for u, t in today[:3]])
        text = (f"🗺️ <@{uid}> scored **{s['total']:,}** on today's **UKGuessr** ({ordinal(rank)} today)"
                + (f" · +{paid} UKP" if paid else ""))
        if await launcher.announce_or_edit(channel_id, text, "guessr", png=png):
            log.info("UKGuessr day posted for %s in %s (%s)", uid, channel_id, s["total"])

    task = asyncio.get_running_loop().create_task(send())
    _posting.add(task)
    task.add_done_callback(_posting.discard)


# ---- the map's files ------------------------------------------------------------------------------------

def map_dir() -> Path:
    return Path(config.JSON_DATA_DIR) / "guessr-map"


def map_type(path: str) -> str:
    return {".pbf": "application/x-protobuf", ".png": "image/png"}.get(Path(path).suffix, "application/json")


async def map_file(path: str) -> Path | None:
    """One of OpenFreeMap's files (`path` under MAP_HOST) from the bot's copy, fetched the first time it's asked
    for. An empty file is a tile with nothing on it; None if OpenFreeMap couldn't be reached. Raises ValueError
    for anything that isn't one of the map's files."""
    if not _MAP_FILE.fullmatch(path):
        raise ValueError(path)
    changes = path in ("planet", "styles/liberty")
    dest = map_dir() / (path + ".json" if changes else path)
    if dest.exists():
        age = time.time() - dest.stat().st_mtime
        if not changes:
            if age > 86400:
                os.utime(dest)                 # still in use: it's trimmed last
            return dest
        if age < 86400:
            return dest
    task = _map_fetching.get(path)
    if task is None:
        task = _map_fetching[path] = asyncio.get_running_loop().create_task(_fetch_map(path, dest))
        task.add_done_callback(lambda _: _map_fetching.pop(path, None))
    return await asyncio.shield(task)


async def _fetch_map(path: str, dest: Path) -> Path | None:
    import aiohttp
    global _map_session
    if _map_session is None or _map_session.closed:
        _map_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20),
                                             headers={"User-Agent": "HMS-Victory UKGuessr (Discord activity)"})
    try:
        async with _map_session.get(MAP_HOST + quote(path, safe="/,@")) as r:
            if r.status in (204, 404):
                body = b""
            elif r.status != 200:
                log.warning("UKGuessr's map: %s for %s", r.status, path)
                return dest if dest.exists() else None
            else:
                body = await r.read()
    except (aiohttp.ClientError, asyncio.TimeoutError):
        log.warning("UKGuessr's map: couldn't fetch %s", path, exc_info=True)
        return dest if dest.exists() else None
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    part.write_bytes(body)
    os.replace(part, dest)
    return dest


def trim_map(limit_mb: int = MAP_CACHE_MB) -> None:
    """Keep the map's copy under limit_mb, deleting what was used longest ago."""
    base = map_dir()
    if not base.exists():
        return
    files = []
    for p in base.rglob("*"):
        if p.is_file():
            st = p.stat()
            files.append((st.st_mtime, st.st_size, p))
    total = sum(size for _, size, _ in files)
    for _, size, p in sorted(files):
        if total <= limit_mb * 1024 * 1024:
            break
        p.unlink(missing_ok=True)
        total -= size


async def close_map() -> None:
    global _map_session
    if _map_session is not None and not _map_session.closed:
        await _map_session.close()
    _map_session = None


def home_card(uid: int, date: _date) -> dict:
    """What the home tile shows: rounds done today, the score so far, and the reward."""
    s = state(uid, date)
    return {"done": s["done"], "played": sum(1 for r in s["rounds"] if r["guess"]), "rounds": ROUNDS, "total": s["total"],
            "reward": s["reward"], "paid": s["paid"]}
