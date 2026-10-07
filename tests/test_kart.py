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
    assert "BEN" in out and "+" in out
