"""Broadside duels: the wheel, a match from challenge to payout, clocks that run out, sudden
death, draws, and who may take which challenge."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OPENAI_TOKEN", "mock-token")

from lib.activities import duel as D  # noqa: E402

A, B, C = 111, 222, 333
NAMES = {A: "Anne", B: "Ben", C: "Cat"}
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
    for uid in (A, B, C):
        assert economy.add_bb(uid, 10_000, reason="seed", taxable=False)
    monkeypatch.setattr(D, "_FILE", str(tmp_path / "duels.json"))
    D._challenges.clear()
    D._matches.clear()
    monkeypatch.setattr(D, "_loaded", True)
    clock = [1_000_000.0]
    monkeypatch.setattr(D.time, "time", lambda: clock[0])
    economy.clock = clock
    yield economy
    database.DatabaseManager._connection.close()
    database.DatabaseManager._connection = None


def tick(em, seconds):
    em.clock[0] += seconds


def start(em, stake=100):
    c = D.challenge(A, stake)
    return D.accept(B, c["id"])["id"]


def play_round(em, mid, a_move, b_move):
    D.pick(A, mid, a_move)
    D.pick(B, mid, b_move)
    tick(em, D.REVEAL_SECONDS)


def test_the_wheel_each_move_beats_three_and_every_win_has_a_reason():
    for a in D.MOVES:
        assert sum(D.beats(a, b) for b in D.MOVES) == 3
        for b in D.MOVES:
            assert not (D.beats(a, b) and D.beats(b, a))
    wins = {(a, b) for a in D.MOVES for b in D.MOVES if D.beats(a, b)}
    assert wins == set(D.REASONS)
    # the one-line rule: the three after it, clockwise
    assert [b for b in D.MOVES if D.beats("broadside", b)] == ["fireship", "ram", "board"]


def test_a_staked_match_from_challenge_to_payout(em):
    mid = start(em, 100)
    assert em.get_bb(A) == 9_900 and em.get_bb(B) == 9_900
    D.pick(A, mid, "broadside")
    seen = D.match(B, mid, name_of)
    assert seen["theyPicked"] is True and "broadside" not in str(seen["rounds"])   # hidden until both are down
    D.pick(B, mid, "board")
    v = D.match(A, mid, name_of)
    assert v["phase"] == "reveal" and v["rounds"][0]["result"] == "win"
    assert v["rounds"][0]["reason"] == "Broadside cuts down the boarders as they cross."
    tick(em, D.REVEAL_SECONDS)
    assert D.match(A, mid, name_of)["phase"] == "pick"
    play_round(em, mid, "fireship", "grapeshot")
    D.pick(A, mid, "ram")
    D.pick(B, mid, "chainshot")
    v = D.match(A, mid, name_of)
    assert v["over"] == {"result": "win", "how": "score", "payout": 190}
    assert D.match(B, mid, name_of)["over"]["result"] == "loss"
    assert em.get_bb(A) == 10_090 and em.get_bb(B) == 9_900


def test_cards_fire_once_and_picks_wait_their_turn(em):
    mid = start(em)
    D.pick(A, mid, "evade")
    with pytest.raises(D.Refuse):
        D.pick(A, mid, "ram")                   # already given
    D.pick(B, mid, "evade")
    with pytest.raises(D.Refuse):
        D.pick(A, mid, "ram")                   # still showing the reveal
    tick(em, D.REVEAL_SECONDS)
    with pytest.raises(D.Refuse, match="already been fired"):
        D.pick(A, mid, "evade")
    assert "evade" not in D.match(A, mid, name_of)["hand"]
    assert "evade" not in D.match(A, mid, name_of)["theirHand"]


def test_a_lapsed_clock_fires_a_card_and_two_in_a_row_leaves_the_match(em, monkeypatch):
    monkeypatch.setattr(D.random, "choice", lambda hand: hand[0])
    mid = start(em, 100)
    D.pick(A, mid, "broadside")
    tick(em, D.PICK_SECONDS + 1)
    v = D.match(A, mid, name_of)
    assert v["rounds"][0]["them"] == "evade" and v["rounds"][0]["auto"]["them"] is True
    tick(em, D.REVEAL_SECONDS)
    D.pick(A, mid, "fireship")
    tick(em, D.PICK_SECONDS + 1)
    v = D.match(A, mid, name_of)
    assert v["over"]["result"] == "win" and v["over"]["how"] == "left"
    assert em.get_bb(A) == 10_090


def test_nobody_turning_up_voids_the_match_and_refunds(em):
    mid = start(em, 250)
    tick(em, (D.PICK_SECONDS + D.REVEAL_SECONDS) * 3)
    D.sweep()
    v = D.match(A, mid, name_of)
    assert v["over"]["how"] == "void" and v["over"]["result"] == "draw"
    assert em.get_bb(A) == 10_000 and em.get_bb(B) == 10_000


def test_level_after_five_goes_to_sudden_death(em):
    mid = start(em, 100)
    for mv in ["evade", "broadside", "fireship", "ram", "board"]:
        play_round(em, mid, mv, mv)
    v = D.match(A, mid, name_of)
    assert "over" not in v and v["round"] == 6 and v["hand"] == ["grapeshot", "chainshot"]
    play_round(em, mid, "grapeshot", "chainshot")
    assert D.match(A, mid, name_of)["over"]["result"] == "win"


def test_level_after_all_seven_is_a_draw_and_refunds(em):
    mid = start(em, 100)
    for mv in ["evade", "broadside", "fireship", "ram", "board", "grapeshot"]:
        play_round(em, mid, mv, mv)
    # the last card is forced, so it's played for both at once
    v = D.match(A, mid, name_of)
    assert v["over"]["result"] == "draw" and v["over"]["how"] == "draw"
    assert len(v["rounds"]) == 7
    assert em.get_bb(A) == 10_000 and em.get_bb(B) == 10_000


def test_a_clear_lead_ends_it_before_five(em):
    mid = start(em, 0)
    play_round(em, mid, "broadside", "broadside")
    play_round(em, mid, "evade", "evade")
    play_round(em, mid, "board", "ram")         # ram beats board: B leads 1-0, three to play
    assert "over" not in D.match(A, mid, name_of)
    play_round(em, mid, "chainshot", "grapeshot")   # 2-0 with one regulation round left
    v = D.match(B, mid, name_of)
    assert v["over"]["result"] == "win" and v["over"]["payout"] == 0


def test_forfeit_pays_the_other_captain(em):
    mid = start(em, 500)
    D.forfeit(B, mid)
    assert D.match(A, mid, name_of)["over"]["how"] == "forfeit"
    assert em.get_bb(A) == 10_000 + 500 - D_rake(1000) and em.get_bb(B) == 9_500


def D_rake(pot):
    from lib.economy.economy_manager import pvp_rake
    return pvp_rake(pot)


def test_who_may_take_which_challenge(em):
    c = D.challenge(A, 100, to=B)
    with pytest.raises(D.Refuse, match="own"):
        D.accept(A, c["id"])
    with pytest.raises(D.Refuse, match="someone else"):
        D.accept(C, c["id"])
    lobby = D.lobby(B, name_of, 10_000)
    assert lobby["forYou"][0]["from"]["name"] == "Anne" and lobby["open"] == []
    assert D.lobby(C, name_of, 10_000)["forYou"] == []
    # a new challenge replaces the old one
    c2 = D.challenge(A, 50)
    assert c["id"] not in D._challenges and D.lobby(C, name_of, 10_000)["open"][0]["id"] == c2["id"]
    with pytest.raises(D.Refuse):
        D.challenge(A, 100, to=A)
    with pytest.raises(D.Refuse, match="up to"):
        D.challenge(C, D.max_stake() + 1)
    assert em.remove_bb(C, 9_000, reason="spent")
    with pytest.raises(D.Refuse, match="don't have"):
        D.challenge(C, 2_000)


def test_one_duel_at_a_time_and_challenges_lapse(em):
    mid = start(em, 0)
    with pytest.raises(D.Refuse, match="Finish"):
        D.challenge(A, 0)
    c = D.challenge(C, 0)
    with pytest.raises(D.Refuse, match="Finish"):
        D.accept(B, c["id"])
    assert D.lobby(A, name_of, 0)["match"]["id"] == mid
    tick(em, D.CHALLENGE_SECONDS + 1)
    with pytest.raises(D.Refuse, match="gone"):
        D.accept(B, c["id"])


def test_a_match_survives_a_restart(em, monkeypatch):
    mid = start(em, 100)
    D.pick(A, mid, "ram")
    D._matches.clear()
    D._challenges.clear()
    monkeypatch.setattr(D, "_loaded", False)
    D._load()
    D.pick(B, mid, "board")
    assert D.match(A, mid, name_of)["rounds"][0]["result"] == "win"
