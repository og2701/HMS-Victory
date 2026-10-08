"""UKP Kart: rooms and seats, the grid with its CPU karts, the bot's own count of how far each person has got
(capped at what their kart can do), finishes, and paying the podium."""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OPENAI_TOKEN", "mock-token")

from lib.activities import kart as K  # noqa: E402

PICK_TRACK = K._pick_track

A, B, D, E = 111, 222, 333, 444
NAMES = {A: "Anne", B: "Ben", D: "Dot", E: "Eve"}
name_of = NAMES.get


@pytest.fixture
def em(tmp_path, monkeypatch):
    import database
    if database.DatabaseManager._connection is not None:
        database.DatabaseManager._connection.close()
        database.DatabaseManager._connection = None
    monkeypatch.setattr(database, "DB_FILE", str(tmp_path / "test.db"))
    database.init_db()
    import lib.economy.economy_manager as economy
    economy._HIST_LAST.clear()
    for uid in (A, B, D, E):
        assert economy.add_bb(uid, 10_000, reason="seed", taxable=False)
    monkeypatch.setattr(K, "_FILE", str(tmp_path / "kart.json"))
    K._rooms.clear()
    K._posts.clear()
    K._live.clear()
    monkeypatch.setattr(K, "_loaded", True)
    # the timings below are the village's; the track picking has its own test
    monkeypatch.setattr(K, "_pick_track", lambda: "village")
    clock = [1_000_000.0]
    monkeypatch.setattr(K.time, "time", lambda: clock[0])
    economy.clock = clock
    yield economy
    database.DatabaseManager._connection.close()
    database.DatabaseManager._connection = None


def tick(em, s):
    em.clock[0] += s


def race(em, players=(A, B), stake=100, cars=None):
    rid = K.open_room(players[0], stake, (cars or {}).get(players[0], "cab"))["id"]
    for p in players[1:]:
        K.join(p, rid, (cars or {}).get(p, "cab"))
    K.start(players[0], rid)
    return rid


def drive(room, uid, metres, seconds, steps=20):
    """A page reporting a steady drive of `metres` over `seconds`, from the kart's grid slot."""
    live = K.Live(room) if room["id"] not in K._live else K._live[room["id"]]
    K._live[room["id"]] = live
    pace = live.paces[uid]
    start, t0 = pace.prog, pace.at
    for i in range(1, steps + 1):
        K.report(room, str(uid), start + metres * i / steps, t0 + seconds * i / steps, pace)
    return pace


def test_the_page_and_the_bot_agree_on_the_tracks_and_karts():
    page = json.loads((Path(__file__).parent / "data" / "kart_tracks.json").read_text())
    for key, t in page["tracks"].items():
        assert K.TRACKS[key] == t
    assert K.CARS == {k: float(v) for k, v in page["cars"].items()}


def test_cpu_karts_fill_the_grid_in_front_and_the_people_go_behind(em):
    rid = race(em, (A, B, D), stake=0)
    r = K._rooms[rid]
    assert len(r["grid"]) == K.SEATS
    cpus = [g for g in r["grid"] if g["cpu"]]
    people = [g for g in r["grid"] if not g["cpu"]]
    assert len(cpus) == 5 and all(g["slot"] < 5 for g in cpus)
    assert sorted(int(g["id"]) for g in people) == [A, B, D] and all(g["slot"] >= 5 for g in people)
    assert r["green"] == pytest.approx(em.clock[0] + K.LOAD_SECONDS + K.COUNTDOWN)
    assert r["pilot"] == A


def test_a_free_race_can_go_alone_but_a_staked_one_needs_a_rival(em):
    solo = K.open_room(A, 0)["id"]
    K.start(A, solo)
    assert K._rooms[solo]["state"] == "race"
    staked = K.open_room(B, 100)["id"]
    with pytest.raises(K.Refuse):
        K.start(B, staked)


def test_stakes_go_in_at_the_start(em):
    rid = race(em, (A, B), stake=500)
    assert em.get_bb(A) == 9_500 and em.get_bb(B) == 9_500
    assert K.view(K._rooms[rid], A, name_of)["pot"] == 1_000


def test_the_pace_cap_trims_a_page_claiming_too_much(em):
    rid = race(em, (A, B), stake=0)
    r = K._rooms[rid]
    tick(em, K.LOAD_SECONDS + K.COUNTDOWN)
    # a cab tops out at 26 m/s: 60 s flat out is about 1,750 m with the slack; a page claiming 3,000 gets trimmed
    pace = drive(r, A, 3000, 60)
    assert pace.prog < 26 * K.PACE * 60 + K.BURST + 10
    assert pace.trimmed > 1000
    honest = drive(r, B, 1300, 60)
    assert honest.trimmed == 0


def test_a_finish_needs_the_bots_own_count_to_say_the_laps_are_done(em):
    rid = race(em, (A, B), stake=0)
    r = K._rooms[rid]
    green = r["green"]
    tick(em, K.LOAD_SECONDS + K.COUNTDOWN)
    live = K._live[rid] = K.Live(r)
    # claiming the line early doesn't stand
    assert K.claim_finish(r, str(A), 50, em.clock[0] + 50, live.paces[A]) is None
    lap = K.race_length(r)
    drive(r, A, lap + 40, 160)
    now = green + 160.4
    place = K.claim_finish(r, str(A), 159.9, now, live.paces[A])
    assert place == 1 and r["finish"][str(A)] == pytest.approx(159.9)
    # a time far earlier than the bot heard it is pulled up to within a second
    drive(r, B, lap + 40, 170)
    K.claim_finish(r, str(B), 100, green + 171, live.paces[B])
    assert r["finish"][str(B)] == pytest.approx(170)


def test_the_winner_of_two_takes_the_pot_less_the_rake(em):
    rid = race(em, (A, B), stake=1000)
    r = K._rooms[rid]
    tick(em, K.LOAD_SECONDS + K.COUNTDOWN)
    live = K._live[rid] = K.Live(r)
    lap = K.race_length(r)
    drive(r, B, lap + 40, 150)
    K.claim_finish(r, str(B), 150, r["green"] + 150, live.paces[B])
    drive(r, A, lap + 40, 160)
    K.claim_finish(r, str(A), 160, r["green"] + 160, live.paces[A])
    tick(em, 200)
    K.sweep()
    assert r["over"] and r["how"] == "race" and r["winners"] == [str(B)]
    won = K.prize(2000)
    assert r["shares"] == {str(B): won}
    assert em.get_bb(B) == 9_000 + won and em.get_bb(A) == 9_000


def test_a_bigger_field_pays_the_podium_and_cpus_never_take_a_share(em):
    rid = race(em, (A, B, D, E), stake=100)
    r = K._rooms[rid]
    tick(em, K.LOAD_SECONDS + K.COUNTDOWN)
    live = K._live[rid] = K.Live(r)
    lap = K.race_length(r)
    # a CPU kart wins on the screen, then Dot, Eve, Anne; Ben never finishes
    r["pilot"] = A
    K.claim_finish(r, "cpu0", 140, r["green"] + 140, None)
    for i, u in enumerate((D, E, A)):
        drive(r, u, lap + 40, 150 + i * 5)
        K.claim_finish(r, str(u), 150 + i * 5, r["green"] + 150 + i * 5, live.paces[u])
    tick(em, 150 + K.AFTER_FIRST + 5)
    K.sweep()
    won = K.prize(400)
    assert r["winners"] == [str(D), str(E)]
    assert r["shares"][str(D)] + r["shares"][str(E)] == won and r["shares"][str(D)] == pytest.approx(won * 0.7, abs=1)
    assert K.order(r)[0] == "cpu0"


def test_nobody_home_gives_the_stakes_back(em):
    rid = race(em, (A, B), stake=250)
    r = K._rooms[rid]
    tick(em, K.LOAD_SECONDS + K.COUNTDOWN + K.MAX_RACE + 1)
    K.sweep()
    assert r["over"] and r["how"] == "refund"
    assert em.get_bb(A) == 10_000 and em.get_bb(B) == 10_000


def test_leaving_mid_race_keeps_your_stake_in_and_the_race_ends_when_the_rest_are_home(em):
    rid = race(em, (A, B), stake=100)
    r = K._rooms[rid]
    tick(em, K.LOAD_SECONDS + K.COUNTDOWN)
    live = K._live[rid] = K.Live(r)
    K.leave(A, rid)
    drive(r, B, K.race_length(r) + 40, 150)
    K.claim_finish(r, str(B), 150, r["green"] + 150, live.paces[B])
    tick(em, 151)
    K.sweep()
    assert r["over"] and r["winners"] == [str(B)] and em.get_bb(B) == 9_900 + K.prize(200)


def test_an_unstarted_room_lapses(em):
    rid = K.open_room(A, 0)["id"]
    tick(em, K.LOBBY_SECONDS + 1)
    K.sweep()
    assert K._rooms[rid]["over"] and K._rooms[rid]["how"] == "lapsed"


def test_the_lobby_shows_your_room_and_the_open_ones(em):
    mine = K.open_room(A, 0, "bus")["id"]
    other = K.open_room(B, 50)["id"]
    lob = K.lobby(A, name_of, 10_000)
    assert lob["room"]["id"] == mine and lob["room"]["players"][0]["car"] == "bus"
    assert [o["id"] for o in lob["open"]] == [other]
    K.pick_car(A, mine, "scooter")
    assert K._rooms[mine]["cars"][str(A)] == "scooter"
    K.pick_car(A, mine, "spaceship")
    assert K._rooms[mine]["cars"][str(A)] == "cab"


def test_the_cards_draw_every_stage(em):
    from lib.activities import kart_card
    rid = K.open_room(A, 100, "bus")["id"]
    K.join(B, rid, "mini")
    names = {A: "Anne", B: "Ben"}
    assert "ANNE" in kart_card.page(K._rooms[rid], names, "seats")
    K.start(A, rid)
    r = K._rooms[rid]
    assert "UKP KART" in kart_card.page(r, names, "start")
    tick(em, K.LOAD_SECONDS + K.COUNTDOWN)
    live = K._live[rid] = K.Live(r)
    drive(r, B, K.race_length(r) + 40, 150)
    K.claim_finish(r, str(B), 150, r["green"] + 150, live.paces[B])
    tick(em, 200)
    K.sweep()
    out = kart_card.page(r, names, "over")
    assert "BEN" in out and "+" in out and "FASTEST LAP" not in out
    # the race's fastest lap, a CPU's here, once the pages have reported their laps
    r["laps"] = {str(B): K._lap(r, 52.4), "cpu0": K._lap(r, 50.1)}
    out = kart_card.page(r, names, "over")
    assert "FASTEST LAP" in out and "0:50.10" in out


def test_only_its_testers_get_in_until_it_goes_live(monkeypatch):
    import config
    from lib.activities import server
    # the command's there exactly when it's live (the list is made once, as the bot starts)
    assert any(game == "kart" for game, _ in config.ACTIVITIES_LAUNCH_COMMANDS.values()) == config.KART_LIVE
    monkeypatch.setattr(config, "KART_LIVE", False)
    monkeypatch.setattr(config, "KART_TESTERS", [1])
    assert server._kart_allowed(1) and not server._kart_allowed(2)
    monkeypatch.setattr(config, "KART_LIVE", True)
    assert server._kart_allowed(2)


def test_each_room_gets_a_track_and_never_the_last_ones(em, monkeypatch):
    monkeypatch.setattr(K, "_pick_track", PICK_TRACK)
    seen = []
    for uid in (A, B, D, E):
        rid = K.open_room(uid, 0)["id"]
        seen.append(K._rooms[rid]["track"])
        K.leave(uid, rid)
    assert all(t in K.TRACKS for t in seen)
    assert all(a != b for a, b in zip(seen, seen[1:]))


def test_every_race_is_kept_with_every_kart_in_it(em):
    from database import DatabaseManager
    rid = race(em, (A, B, D, E), stake=100)
    r = K._rooms[rid]
    tick(em, K.LOAD_SECONDS + K.COUNTDOWN)
    live = K._live[rid] = K.Live(r)
    lap = K.race_length(r)
    r["pilot"] = A
    K.claim_finish(r, "cpu0", 140, r["green"] + 140, None)
    for i, u in enumerate((D, E, A)):
        drive(r, u, lap + 40, 150 + i * 5)
        K.claim_finish(r, str(u), 150 + i * 5, r["green"] + 150 + i * 5, live.paces[u])
    r.setdefault("laps", {})[str(D)] = K._lap(r, 49.25)
    tick(em, 150 + K.AFTER_FIRST + 5)
    K.sweep()
    race_row = DatabaseManager.fetch_one("SELECT mode, track, stake, pot, outcome, host_id FROM kart_races WHERE id = ?", (rid,))
    assert tuple(race_row) == ("race", "village", 100, 400, "race", str(A))
    rows = DatabaseManager.fetch_all("SELECT user_id, name, place, finish_time, best_lap, payout FROM kart_results WHERE race_id = ? ORDER BY place", (rid,))
    assert len(rows) == K.SEATS
    assert rows[0][0] is None and rows[0][1] and rows[0][2] == 1          # the CPU that won, by name
    assert [r_[0] for r_ in rows[1:4]] == [str(D), str(E), str(A)]
    assert rows[1][4] == 49.25 and rows[1][5] == r["shares"][str(D)]
    ben = next(r_ for r_ in rows if r_[0] == str(B))
    assert ben[3] is None and ben[5] == 0                                  # never got home
    # a lap no kart could do isn't kept
    assert K._lap(r, 3) is None


def test_a_practice_race_is_kept_and_a_made_up_one_isnt(em):
    from database import DatabaseManager
    field = [{"cpu": True, "name": "Mr Bean", "car": "mini", "place": 2, "time": 170.5, "lap": 55.3},
             {"cpu": False, "car": "cab", "place": 1, "time": 168.2, "lap": 54.1}]
    rid = K.record_practice(A, {"track": "silverstone", "field": field})
    assert tuple(DatabaseManager.fetch_one("SELECT mode, track, host_id FROM kart_races WHERE id = ?", (rid,))) == ("practice", "silverstone", str(A))
    mine = DatabaseManager.fetch_one("SELECT place, finish_time, best_lap FROM kart_results WHERE race_id = ? AND user_id = ?", (rid, str(A)))
    assert tuple(mine) == (1, 168.2, 54.1)
    assert DatabaseManager.fetch_one("SELECT best_lap FROM kart_results WHERE race_id = ? AND name = 'Mr Bean'", (rid,))[0] == 55.3
    with pytest.raises(K.Refuse):
        K.record_practice(A, {"track": "silverstone", "field": [{"cpu": False, "car": "cab", "place": 1, "time": 9}]})
    with pytest.raises(K.Refuse):
        K.record_practice(A, {"track": "the moon", "field": field})


def test_anyone_can_watch_a_race_under_way(em):
    rid = race(em, (A, B), stake=0)
    with pytest.raises(K.Refuse):
        K.watch(E, "nope", name_of)
    seen = K.watch(E, rid, name_of)
    assert seen["state"] == "race" and len(seen["grid"]) == K.SEATS
    # it's in the lobby's list of races to watch, for everyone but the people in it
    assert [r["id"] for r in K.lobby(E, name_of, 0)["live"]] == [rid]
    assert K.lobby(A, name_of, 0)["live"] == []
    # a race still filling up isn't there to watch
    waiting = K.open_room(D, 0, "cab")["id"]
    with pytest.raises(K.Refuse):
        K.watch(E, waiting, name_of)


class FakeSock:
    def __init__(self):
        self.got = []

    async def send_str(self, text):
        self.got.append(json.loads(text))


def test_watchers_hear_everything_and_see_whats_on_the_road(em):
    import asyncio
    rid = race(em, (A, B), stake=0)
    tick(em, K.LOAD_SECONDS + K.COUNTDOWN)
    live = K.Live(K._rooms[rid])
    racer, watcher = FakeSock(), FakeSock()
    live.socks[A] = racer
    live.watchers[E] = watcher
    asyncio.run(live.handle(B, {"t": "e", "e": {"k": "add", "th": {"id": 7, "kind": "cone", "owner": str(B)}}}))
    assert [m["t"] for m in watcher.got] == ["e"] and [m["t"] for m in racer.got] == ["e"]
    assert list(live.things) == [7]
    asyncio.run(live.handle(B, {"t": "e", "e": {"k": "del", "id": 7}}))
    assert live.things == {}
    # a watcher is never handed the CPU karts to drive
    live.socks.pop(A)
    live.repilot()
    assert K._rooms[rid].get("pilot") != E


def test_a_race_under_way_has_a_watch_button(em):
    from lib.activities import kart_posts
    rid = race(em, (A, B), stake=0)
    ids = [c.custom_id for row in kart_posts.view(K._rooms[rid], "start", None).children
           if hasattr(row, "children") for c in row.children if hasattr(c, "custom_id")]
    assert f"ukplace:kartwatch:{rid}" in ids and "ukplace:play:kart" in ids


def test_the_host_can_pick_the_track(em, monkeypatch):
    monkeypatch.setattr(K, "_pick_track", PICK_TRACK)
    assert K.open_room(A, 0, "cab", track="silverstone")["track"] == "silverstone"
    # anything else (none, or not a track) gets a random one
    assert K.open_room(B, 0, "cab", track="the moon")["track"] in K.TRACKS
