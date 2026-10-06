"""Countdown rooms: seats, letter calls and their minimums, the clock, scoring the longest word,
Dictionary Corner, and settling the pot."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OPENAI_TOKEN", "mock-token")

from lib.activities import countdown as C  # noqa: E402

A, B, D = 111, 222, 333
NAMES = {A: "Anne", B: "Ben", D: "Dot"}
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
    for uid in (A, B, D):
        assert economy.add_bb(uid, 10_000, reason="seed", taxable=False)
    monkeypatch.setattr(C, "_FILE", str(tmp_path / "countdown.json"))
    C._rooms.clear()
    C._posts.clear()
    monkeypatch.setattr(C, "_loaded", True)
    clock = [1_000_000.0]
    monkeypatch.setattr(C.time, "time", lambda: clock[0])
    economy.clock = clock
    yield economy
    database.DatabaseManager._connection.close()
    database.DatabaseManager._connection = None


def tick(em, s):
    em.clock[0] += s


def rigged(room_id, letters):
    """Make the letters of the round in play these ones (so words can be planned)."""
    C.room(C._rooms[room_id]["host"], room_id, name_of)          # bring it up to the round in play
    rd = C._rooms[room_id]["rounds"][-1]
    rd["letters"] = list(letters)
    rd["words"] = {}
    rd["phase"] = "clock"
    rd["deadline"] = C.time.time() + C.CLOCK_SECONDS


def game(em, players=(A, B), stake=100):
    rid = C.open_room(players[0], stake)["id"]
    for p in players[1:]:
        C.join(p, rid)
    C.start(players[0], rid)
    return rid


def test_the_dictionary_and_the_board():
    assert {"colour", "realise", "realize", "islander"} <= C.words()
    assert C.fits("island", list("satirendl")) and not C.fits("sass", list("satirendl"))
    best = C.best_words(list("satirendl"))
    assert len(best) == 3 and all(len(w) == 8 for w in best) and "detrains" not in best   # everyday words first
    assert C.points("islander") == 8 and C.points("nostalgia") == 18


def test_theres_a_round_for_every_player_and_each_calls_once(em):
    rid = C.open_room(A, 0)["id"]
    assert C.view(C._rooms[rid], A, name_of)["rounds"] == 2           # two at least: it takes two to start
    C.join(B, rid)
    C.join(D, rid)
    assert C.view(C._rooms[rid], A, name_of)["rounds"] == 3
    C.start(A, rid)
    for _ in range(10):
        tick(em, 120)
        C.room(A, rid, name_of)
    r = C._rooms[rid]
    assert r["over"] and len(r["rounds"]) == 3
    assert sorted(rd["picker"] for rd in r["rounds"]) == sorted([A, B, D])


def test_letter_calls_keep_three_vowels_and_four_consonants(em):
    rid = game(em, stake=0)
    rd = C._rooms[rid]["rounds"][-1]
    assert rd["picker"] == A
    with pytest.raises(C.Refuse, match="someone else"):
        C.call(B, rid, "vowel")
    for _ in range(5):
        C.call(A, rid, "vowel")
    with pytest.raises(C.Refuse, match="other kind"):
        C.call(A, rid, "vowel")             # five vowels leaves exactly room for four consonants
    for _ in range(4):
        C.call(A, rid, "consonant")
    v = C.room(A, rid, name_of)
    assert v["phase"] == "clock" and len(v["letters"]) == 9 and v["vowels"] == 5


def test_a_slow_picker_gets_letters_called_for_them(em):
    rid = game(em, stake=0)
    tick(em, C.LETTER_SECONDS * 9 + 1)
    v = C.room(B, rid, name_of)
    assert v["phase"] == "clock" and len(v["letters"]) == 9
    assert v["vowels"] >= 3 and v["consonants"] >= 4


def test_only_the_longest_valid_word_scores_and_corner_shows_the_best(em):
    rid = game(em, (A, B, D), stake=0)
    rigged(rid, "satirendl")
    C.declare(A, rid, "islander")
    C.declare(B, rid, "retails")
    with pytest.raises(C.Refuse, match="letters on the board"):
        C.declare(D, rid, "zebras")
    C.declare(D, rid, "strandle")                   # fits the board, but isn't a word
    v = C.room(A, rid, name_of)
    assert v["phase"] == "reveal"                   # everyone had declared, so the clock stopped early
    res = {r["name"]: r for r in v["results"]}
    assert res["Anne"]["points"] == 8 and res["Ben"]["points"] == 0 and res["Dot"]["valid"] is False
    assert v["players"][0]["score"] == 8 and len(v["corner"][0]) == 8


def test_matching_the_longest_scores_too_and_a_nine_scores_eighteen(em):
    rid = game(em, stake=0)
    rigged(rid, "nostalgia")
    C.declare(A, rid, "nostalgia")
    C.declare(B, rid, "nostalgia")
    v = C.room(A, rid, name_of)
    assert [p["score"] for p in v["players"]] == [18, 18]


def test_a_staked_game_pays_the_winner_the_pot_less_the_rake(em):
    rid = game(em, (A, B, D), stake=100)
    assert all(em.get_bb(u) == 9_900 for u in (A, B, D))
    for _ in range(3):
        rigged(rid, "satirendl")
        C.declare(A, rid, "islander")
        C.declare(B, rid, "trail")
        C.declare(D, rid, "tail")
        tick(em, C.REVEAL_SECONDS)
    v = C.room(B, rid, name_of)
    assert v["phase"] == "over" and v["winners"] == [str(A)]
    from lib.economy.economy_manager import pvp_rake
    assert em.get_bb(A) == 9_900 + 300 - pvp_rake(300) and em.get_bb(B) == 9_900


def test_nobody_scoring_gives_every_stake_back(em):
    rid = game(em, stake=250)
    tick(em, 600)
    C.sweep()
    v = C.room(A, rid, name_of)
    assert v["phase"] == "over" and v["how"] == "refund"
    assert em.get_bb(A) == 10_000 and em.get_bb(B) == 10_000


def test_leaving_mid_game_forfeits_and_the_last_player_standing_wins(em):
    rid = game(em, stake=100)
    rigged(rid, "satirendl")
    C.declare(A, rid, "island")
    C.declare(B, rid, "sand")
    C.leave(B, rid)
    v = C.room(A, rid, name_of)
    assert v["phase"] == "over" and v["winners"] == [str(A)]
    assert em.get_bb(B) == 9_900


def test_rooms_seats_and_who_starts(em):
    rid = C.open_room(A, 0)["id"]
    with pytest.raises(C.Refuse, match="one more"):
        C.start(A, rid)
    C.join(B, rid)
    with pytest.raises(C.Refuse, match="host"):
        C.start(B, rid)
    with pytest.raises(C.Refuse, match="already in"):
        C.open_room(B, 0)
    assert C.lobby(D, name_of, 0)["open"][0]["players"] == 2
    C.leave(B, rid)
    assert C._rooms[rid]["players"] == [A]
    C.leave(A, rid)                                  # the host leaving closes it
    assert C._rooms[rid]["over"] and C.lobby(D, name_of, 0)["open"] == []


def test_an_unstarted_room_lapses_and_a_full_one_turns_people_away(em, monkeypatch):
    monkeypatch.setattr(C, "SEATS", 2)
    rid = C.open_room(A, 0)["id"]
    C.join(B, rid)
    with pytest.raises(C.Refuse, match="full"):
        C.join(D, rid)
    tick(em, C.LOBBY_SECONDS + 1)
    C.sweep()
    assert C._rooms[rid]["how"] == "lapsed"


def test_every_step_is_told_to_the_post(em):
    seen = []
    C.listeners.append(lambda event, room: seen.append(event))
    try:
        rid = game(em, stake=0)
        rigged(rid, "satirendl")
        C.declare(A, rid, "island")
        C.declare(B, rid, "sand")
        C.leave(B, rid)
    finally:
        C.listeners.clear()
    assert seen == ["open", "seats", "start", "round", "over"]


def test_the_post_goes_where_the_game_was_opened_or_casino_if_it_cant(em, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    import discord

    from lib.activities import countdown_card, countdown_posts as P, launcher
    sent = []

    async def post_view(ch, view, files=None, ping=None):
        if ch == 555:
            raise discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Missing Access")
        sent.append(("post", ch))
        return 77

    async def edit_view(ch, mid, view, files=None):
        sent.append(("edit", ch))

    async def no_picture(*a):
        return None

    monkeypatch.setattr(launcher, "post_view", post_view)
    monkeypatch.setattr(launcher, "edit_view", edit_view)
    monkeypatch.setattr(countdown_card, "png", no_picture)
    monkeypatch.setattr(P, "_casino", lambda: 999)
    monkeypatch.setattr(P, "_name", lambda uid: NAMES.get(uid, "Someone"))
    monkeypatch.setattr(P, "EDIT_GAP", 0)
    P._posts.clear()

    async def run():
        C.listeners.append(P.on_event)
        try:
            here = C.open_room(A, 0, channel=444)["id"]
            await asyncio.sleep(0.01)
            C.join(B, here)
            await asyncio.sleep(0.01)
            blocked = C.open_room(D, 0, channel=555)["id"]
            await asyncio.sleep(0.01)
        finally:
            C.listeners.clear()
        return here, blocked

    here, blocked = asyncio.run(run())
    assert sent == [("post", 444), ("edit", 444), ("post", 999)]
    assert C.post_for(here) == (444, 77) and C.post_for(blocked) == (999, 77)


def test_posts_saved_before_channels_are_read_as_casino(em):
    C._posts["old"] = 123
    assert C.post_for("old") == (None, 123)


def test_the_result_is_posted_afresh_and_the_first_post_points_to_it(em, monkeypatch):
    import asyncio

    from lib.activities import countdown_card, countdown_posts as P, launcher
    sent = []

    def text(view):
        out, todo = [], list(view.children)
        while todo:
            item = todo.pop(0)
            if getattr(item, "content", None):
                out.append(item.content)
            todo[:0] = list(getattr(item, "children", []) or [])
        return " ".join(out)

    async def post_view(ch, view, files=None, ping=None):
        sent.append(("post", text(view)))
        return 100 + len(sent)

    async def edit_view(ch, mid, view, files=None):
        sent.append(("edit", mid, text(view)))

    async def no_picture(*a):
        return None

    monkeypatch.setattr(launcher, "post_view", post_view)
    monkeypatch.setattr(launcher, "edit_view", edit_view)
    monkeypatch.setattr(countdown_card, "png", no_picture)
    monkeypatch.setattr(P, "_casino", lambda: 999)
    monkeypatch.setattr(P, "_name", lambda uid: NAMES.get(uid, "Someone"))
    monkeypatch.setattr(P, "EDIT_GAP", 0)
    P._posts.clear()

    async def run():
        C.listeners.append(P.on_event)
        try:
            rid = C.open_room(A, 0, channel=444)["id"]
            await asyncio.sleep(0.01)
            C.join(B, rid)
            C.start(A, rid)
            await asyncio.sleep(0.01)
            rigged(rid, "satirendl")
            C.declare(A, rid, "island")
            C.declare(B, rid, "sand")
            await asyncio.sleep(0.01)
            C.leave(B, rid)                              # Anne wins
            await asyncio.sleep(0.01)
        finally:
            C.listeners.clear()

    asyncio.run(run())
    first = sent[0]
    assert first[0] == "post" and "opened a **Countdown** room" in first[1]
    shrunk = [s for s in sent if s[0] == "edit" and "the result's below" in s[2]]
    assert shrunk and shrunk[0][1] == 101                # the first message became the pointer
    assert sent[-1][0] == "post" and f"<@{A}> won **Countdown**" in sent[-1][1]   # and the result is new
