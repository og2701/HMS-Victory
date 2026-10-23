"""UKGuessr (lib/activities/guessr.py): three UK spots a day, a pin for each, scored by distance, everyone's pins
shown once you've guessed, today's and the all-time leaderboards, and a flat reward for finishing."""

import datetime
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OPENAI_TOKEN", "mock-token")

DAY = datetime.date(2026, 10, 23)
A, B = 1001, 1002
SPOTS = [{"n": 1, "id": "m1", "lat": 51.5007, "lon": -0.1246, "by": "someone", "at": 0},
         {"n": 2, "id": "m2", "lat": 55.9486, "lon": -3.1999, "by": "someone", "at": 0},
         {"n": 3, "id": "m3", "lat": 54.5973, "lon": -5.9301, "by": "someone", "at": 0}]


@pytest.fixture
def day(tmp_path, monkeypatch):
    import config
    import database
    if database.DatabaseManager._connection is not None:
        database.DatabaseManager._connection.close()
        database.DatabaseManager._connection = None
    monkeypatch.setattr(database, "DB_FILE", str(tmp_path / "test.db"))
    database.init_db()
    monkeypatch.setattr(config, "JSON_DATA_DIR", str(tmp_path))
    from lib.activities import guessr
    database.DatabaseManager.execute("INSERT INTO guessr_days (date, spots) VALUES (?, ?)", (DAY.isoformat(), json.dumps(SPOTS)))
    for n in (1, 2, 3):
        p = tmp_path / "guessr" / DAY.isoformat() / f"{n}.jpg"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"jpeg")
    paid = []
    import lib.economy.economy_manager as economy
    monkeypatch.setattr(economy, "add_bb", lambda uid, n, **kw: paid.append((uid, n)) or True)
    return guessr, paid


def test_the_map_knows_uk_land_from_the_sea_and_from_ireland():
    from lib.activities import guessr
    assert guessr.on_land(51.5, -0.12) and guessr.on_land(54.6, -5.93) and guessr.on_land(57.48, -4.22)   # London, Belfast, Inverness
    assert not guessr.on_land(53.35, -6.26)                                    # Dublin
    assert not guessr.on_land(50.0, -3.0) and not guessr.on_land(54.0, -4.0)   # the Channel, the Irish Sea
    assert round(guessr.km_between(51.5, -0.12, 55.95, -3.19)) == 534          # London to Edinburgh


def test_closer_pins_score_more():
    from lib.activities import guessr
    assert guessr.score_for(0.01) == 5000
    scores = [guessr.score_for(km) for km in (1, 10, 50, 100, 300, 800)]
    assert scores == sorted(scores, reverse=True) and scores[1] > 4500 and scores[-1] < 50


def test_a_day_is_three_pins_in_order_and_pays_once(day):
    guessr, paid = day
    s = guessr.state(A, DAY)
    assert s["ready"] and [r["photo"] is not None for r in s["rounds"]] == [True, False, False]     # no peeking ahead
    assert all(r["answer"] is None for r in s["rounds"])
    with pytest.raises(guessr.Refuse):
        guessr.guess(A, DAY, 2, 55.9, -3.2)                                   # in order
    s = guessr.guess(A, DAY, 1, 51.51, -0.13)
    assert s["rounds"][0]["guess"]["score"] > 4900 and s["rounds"][0]["answer"]["lat"] == 51.5007
    assert s["rounds"][1]["photo"] and not s["rounds"][2]["photo"]
    with pytest.raises(guessr.Refuse):
        guessr.guess(A, DAY, 1, 51.5, -0.12)                                  # once each
    guessr.guess(A, DAY, 2, 56.0, -3.2)
    assert not paid
    s = guessr.guess(A, DAY, 3, 53.0, -2.0)                                   # Belfast, pinned in Cheshire
    assert s["done"] and s["total"] == sum(r["guess"]["score"] for r in s["rounds"]) and paid == [(A, 10)]
    assert guessr.state(A, DAY)["paid"]
    guessr._pay(A, DAY.isoformat())
    assert paid == [(A, 10)]                                                   # just the once


def test_pins_and_photos_only_once_you_have_guessed(day):
    guessr, _ = day
    name = {A: "Ann", B: "Bob"}.get
    with pytest.raises(guessr.Refuse):
        guessr.pins(A, DAY, 1, name)
    assert guessr.photo(A, DAY, DAY.isoformat(), 1) and guessr.photo(A, DAY, DAY.isoformat(), 2) is None
    assert guessr.photo(A, DAY, "2026-10-22", 1) is None                      # only today's
    guessr.guess(B, DAY, 1, 52.0, -1.0)
    guessr.guess(A, DAY, 1, 51.5, -0.12)
    pins = guessr.pins(A, DAY, 1, name)
    assert [p["name"] for p in pins] == ["You", "Bob"] and pins[0]["me"] and pins[0]["score"] > pins[1]["score"]


def test_today_and_all_time_leaderboards_add_up(day):
    import database
    guessr, _ = day
    name = {A: "Ann", B: "Bob"}.get
    # yesterday Bob did well
    database.DatabaseManager.execute(
        "INSERT INTO guessr_guesses (user_id, date, round, lat, lon, km, score, at) VALUES (?, '2026-10-22', 1, 0, 0, 0, 5000, 0)", (str(B),))
    guessr.guess(A, DAY, 1, 51.5, -0.12)
    guessr.guess(B, DAY, 1, 53.0, -1.0)
    b = guessr.board(A, DAY, name)
    assert [r["name"] for r in b["today"]["rows"]] == ["You", "Bob"] and b["today"]["you"]["rank"] == 1
    assert b["allTime"]["rows"][0]["name"] == "Bob" and b["allTime"]["you"]["rank"] == 2   # yesterday's counts too
    assert b["today"]["players"] == 2


def test_without_a_token_a_test_day_is_made_for_developing(tmp_path, monkeypatch):
    import asyncio

    import config
    import database
    if database.DatabaseManager._connection is not None:
        database.DatabaseManager._connection.close()
        database.DatabaseManager._connection = None
    monkeypatch.setattr(database, "DB_FILE", str(tmp_path / "test.db"))
    database.init_db()
    monkeypatch.setattr(config, "JSON_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "GUESSR_TEST_SPOTS", True, raising=False)
    monkeypatch.delenv("MAPILLARY_TOKEN", raising=False)
    from lib.activities import guessr
    got = asyncio.run(guessr.pick_day("2026-10-24"))
    assert len(got) == 3 and (tmp_path / "guessr" / "2026-10-24" / "3.jpg").exists()
    (tmp_path / "guessr" / "2026-10-20").mkdir()
    guessr.tidy("2026-10-24")
    assert not (tmp_path / "guessr" / "2026-10-20").exists() and (tmp_path / "guessr" / "2026-10-24").exists()


def test_the_map_passes_on_only_the_maps_own_files_and_keeps_a_copy(tmp_path, monkeypatch):
    import asyncio
    import config
    monkeypatch.setattr(config, "JSON_DATA_DIR", str(tmp_path))
    from lib.activities import guessr
    fetched = []

    async def fetch(path, dest):
        fetched.append(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"" if path.endswith("/0/0.pbf") else b"tile")
        return dest
    monkeypatch.setattr(guessr, "_fetch_map", fetch)

    async def run():
        for bad in ("../database.db", "planet/../../x", "styles/other", "planet/20261018_080001_pt/8/1/2.png"):
            with pytest.raises(ValueError):
                await guessr.map_file(bad)
        tile = "planet/20261018_080001_pt/8/127/84.pbf"
        a = await guessr.map_file(tile)
        b = await guessr.map_file(tile)
        assert a == b and a.read_bytes() == b"tile" and fetched == [tile]
        sea = await guessr.map_file("planet/20261018_080001_pt/8/0/0.pbf")
        assert sea.stat().st_size == 0
        style = await guessr.map_file("styles/liberty")
        assert style.name == "liberty.json"
        assert (await guessr.map_file("fonts/Noto Sans Regular/0-255.pbf")).exists()
        # the tilejson changes, so a day-old copy is fetched again
        old = guessr.map_dir() / "planet.json"
        await guessr.map_file("planet")
        os.utime(old, (0, 0))
        await guessr.map_file("planet")
        assert fetched.count("planet") == 2
    asyncio.run(run())

    # trimmed back to the limit, least used first
    for i, age in enumerate((300, 200, 100)):
        p = guessr.map_dir() / "planet" / f"big{i}.pbf"
        p.write_bytes(b"x" * 600_000)
        os.utime(p, (p.stat().st_mtime - age,) * 2)
    guessr.trim_map(limit_mb=1)
    left = sorted(p.name for p in (guessr.map_dir() / "planet").glob("big*"))
    assert left == ["big2.pbf"]


def test_only_testers_get_in_until_it_goes_live(day, monkeypatch):
    import config
    from lib.activities import home
    guessr, _ = day
    monkeypatch.setattr(config, "GUESSR_LIVE", False)
    monkeypatch.setattr(config, "GUESSR_TESTERS", [A])
    assert guessr.allowed(A) and not guessr.allowed(B)
    assert home._guessr(A, DAY) is not None and home._guessr(B, DAY) is None
    monkeypatch.setattr(config, "GUESSR_LIVE", True)
    assert guessr.allowed(B)
    monkeypatch.setattr(config, "GUESSR_ENABLED", False)
    assert not guessr.allowed(A)


def test_finishing_the_day_posts_its_card_without_giving_the_spots_away(day, monkeypatch):
    import asyncio
    from lib.activities import guessr_card, launcher
    guessr, _ = day
    posted, drawn = [], []

    async def announce(channel_id, text, game, message_id=None, png=None):
        posted.append((channel_id, text, game, png))
        return 1

    async def card(**kw):
        drawn.append(kw)
        return b"png"
    monkeypatch.setattr(launcher, "announce_or_edit", announce)
    monkeypatch.setattr(guessr_card, "card_png", card)
    for n, s in enumerate(SPOTS, 1):
        guessr.guess(A, DAY, n, s["lat"] + 0.1, s["lon"])

    async def run():
        guessr.post_day(A, 555, DAY)
        await asyncio.gather(*guessr._posting)
    asyncio.run(run())
    (ch, text, game, png), = posted
    assert ch == 555 and game == "guessr" and png == b"png" and f"<@{A}>" in text and "+10 UKP" in text
    kw = drawn[0]
    assert kw["rank"] == 1 and kw["players"] == 1 and kw["paid"] == 10 and len(kw["rounds"]) == 3
    # nothing on the card says where the spots were
    html = guessr_card.card_html(**kw)
    assert "51.50" not in html and "Westminster" not in html and "someone" not in html
