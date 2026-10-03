"""Every casino game in the activity: a round moves the right money with the game's own
reason strings, records one casino_results row, and refuses the moves the slash command's
buttons would never offer."""

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OPENAI_TOKEN", "mock-token")

import config
from lib.activities import casino
from lib.activities.casino import base, sessions

UID = 5151


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
    assert economy.add_bb(UID, 10_000, reason="seed", taxable=False)
    monkeypatch.setattr(base, "_FILE", str(tmp_path / "activity_casino.json"))
    for d in (base._games, base._finished, base._locks, base._recent, sessions._sittings):
        d.clear()
    monkeypatch.setattr(base, "_loaded", True)
    monkeypatch.setattr(base, "MOVES_PER_SECOND", 1_000)
    monkeypatch.setattr(sessions, "_schedule", lambda s: None)
    from lib.economy import reserve_policy
    monkeypatch.setattr(reserve_policy, "max_casino_bet", lambda game_max_multiplier=3.0, static_max=0: static_max)
    monkeypatch.setattr(reserve_policy, "max_casino_net_payout", lambda static_max_net=0: 10**9)
    yield economy
    database.DatabaseManager._connection.close()
    database.DatabaseManager._connection = None


def play(key, action, body=None):
    return asyncio.run(casino.move(UID, "Tester", key, action, body or {}))


def rows():
    from database import DatabaseManager
    return DatabaseManager.fetch_all("SELECT game, staked, payout, outcome FROM casino_results")


def reasons():
    from database import DatabaseManager
    return [r[0] for r in DatabaseManager.fetch_all("SELECT reason FROM user_transactions WHERE user_id = ? ORDER BY id",
                                                    (str(UID),))]


# --- card games ---------------------------------------------------------------------------
def test_higher_or_lower_climbs_and_cashes_out(em, monkeypatch):
    from commands.economy import higher_lower as HL
    monkeypatch.setattr(HL, "_fresh_deck", lambda: ["2C", "3C", "KD", "9H", "7S"])   # 7S first, then 9H
    out = play("higherlower", "deal", {"bet": 100})
    assert out["table"]["current"] == "7S"
    with pytest.raises(casino.Refuse, match="at least one"):
        play("higherlower", "cashout")
    out = play("higherlower", "higher")            # 9H beats 7S
    assert out["table"]["steps"] == 1
    value = out["table"]["value"]
    out = play("higherlower", "cashout")
    assert out["round"]["payout"] == value and em.get_bb(UID) == 10_000 - 100 + value
    assert rows() == [("higherlower", 100, value, "win")]
    assert "Higher-Lower cash-out" in reasons()


def test_higher_or_lower_refuses_a_direction_with_no_odds(em, monkeypatch):
    from commands.economy import higher_lower as HL
    monkeypatch.setattr(HL, "_fresh_deck", lambda: ["2C", "3C", "KD", "9H", "7S"])
    play("higherlower", "deal", {"bet": 100})
    game = base._games[f"{UID}:higherlower"]
    game.mult_higher = None
    with pytest.raises(casino.Refuse, match="isn't on offer"):
        play("higherlower", "higher")


def test_video_poker_holds_and_pays_the_paytable(em, monkeypatch):
    from commands.economy import video_poker as VP
    # Deal JS JH 4D 9C KC, then draw JD QS 2C for the three swapped cards.
    deck = ["2C", "QS", "JD", "KC", "9C", "4D", "JH", "JS"]
    monkeypatch.setattr(VP.cb, "fresh_deck", lambda: list(deck))
    out = play("videopoker", "deal", {"bet": 200})
    assert out["table"]["cards"] == ["JS", "JH", "4D", "9C", "KC"] and out["table"]["pays"] == 1
    out = play("videopoker", "draw", {"held": [0, 1]})
    assert out["table"]["outcome"] == "Three of a Kind"
    assert em.get_bb(UID) == 10_000 - 200 + 600
    assert rows() == [("videopoker", 200, 600, "Three of a Kind")]


def test_red_dog_raise_takes_a_second_stake(em, monkeypatch):
    from commands.economy import red_dog as RD
    monkeypatch.setattr(RD.cb, "fresh_deck", lambda: ["8H", "JS", "4C"])   # 4 and J, then an 8
    out = play("reddog", "deal", {"bet": 100})
    assert out["table"]["spread"] == 6 and out["table"]["third"] is None
    out = play("reddog", "raise")
    assert out["table"]["staked"] == 200 and out["table"]["outcome"] == "win"
    assert em.get_bb(UID) == 10_000 - 200 + 400
    assert rows() == [("reddog", 200, 400, "win")]


def test_three_card_poker_fold_and_play(em, monkeypatch):
    from commands.economy import three_card_poker as TCP
    # player 8H 9S TD (a straight), dealer 2C 5D 7S (doesn't qualify)
    monkeypatch.setattr(TCP.cb, "fresh_deck", lambda: ["7S", "5D", "2C", "TD", "9S", "8H"])
    out = play("tcp", "deal", {"bet": 100})
    assert out["table"]["dealer"] == [None, None, None]
    out = play("tcp", "play")
    assert out["table"]["outcome"] == "dealer_no_qualify"
    assert out["table"]["payout"] == 300 + 100            # 3B base + straight bonus
    assert em.get_bb(UID) == 10_000 - 200 + 400
    play("tcp", "deal", {"bet": 100})
    out = play("tcp", "fold")
    assert out["table"]["outcome"] == "fold" and out["round"]["net"] == -100


# --- spins --------------------------------------------------------------------------------
def test_slots_spins_and_pays_in_one_move(em, monkeypatch):
    from commands.economy import slots as S
    monkeypatch.setattr(S, "spin_reels", lambda: ["crown", "crown", "crown"])
    out = play("slots", "deal", {"bet": 10})
    assert out["table"]["jackpot"] and out["table"]["payout"] == 250
    assert em.get_bb(UID) == 10_000 + 240
    assert rows() == [("slots", 10, 250, "25x")]
    assert not out["inPlay"]
    out = play("slots", "deal", {"bet": 10})               # spin again straight away
    assert len(rows()) == 2


def test_roulette_spins_a_solo_slip(em, monkeypatch):
    import lib.activities.casino.games.spins as spins
    monkeypatch.setattr(spins.random, "randint", lambda a, b: 17)
    out = play("roulette", "deal", {"bet": 30, "bets": {"straight:17": 10, "red": 10, "dozen2": 10}})
    t = out["table"]
    assert t["number"] == 17 and t["color"] == "black"
    assert set(t["won"]) == {"straight:17", "dozen2"}
    assert t["payout"] == 10 * 36 + 10 * 3
    assert em.get_bb(UID) == 10_000 - 30 + 390
    assert rows() == [("roulette", 30, 390, "17")]
    with pytest.raises(casino.Refuse):
        play("roulette", "deal", {"bet": 10, "bets": {"purple": 10}})
    with pytest.raises(casino.Refuse, match="changed"):
        play("roulette", "deal", {"bet": 20, "bets": {"red": 10}})


# --- ladders -------------------------------------------------------------------------------
def test_mines_reveal_cash_out_and_boom(em, monkeypatch):
    import commands.economy.mines as MI
    monkeypatch.setattr(MI.random, "sample", lambda pop, k: [0, 1, 2])
    monkeypatch.setattr(config, "MINES_DEFAULT_MINES", 3)
    out = play("mines", "deal", {"bet": 100})
    assert out["table"]["minesAt"] is None                  # layout stays on the server
    with pytest.raises(casino.Refuse, match="at least one coin"):
        play("mines", "cashout")
    out = play("mines", "reveal", {"tile": 10})
    value = out["table"]["value"]
    out = play("mines", "cashout")
    assert out["table"]["minesAt"] == [0, 1, 2]
    assert em.get_bb(UID) == 10_000 - 100 + value
    play("mines", "deal", {"bet": 100})
    out = play("mines", "reveal", {"tile": 0})
    assert out["table"]["outcome"] == "lose" and out["table"]["hit"] == 0
    assert rows() == [("mines", 100, value, "win"), ("mines", 100, 0, "lose")]
    assert "Mines cashout" in reasons()


def test_chest_upgrades_to_diamond(em, monkeypatch):
    import commands.economy.chest as CH
    monkeypatch.setattr(CH.random, "random", lambda: 0.0)
    play("chest", "deal", {"bet": 100})
    for _ in range(3):
        out = play("chest", "upgrade")
    assert out["table"]["over"] and out["table"]["tier"] == 3
    assert em.get_bb(UID) == 10_000 - 100 + 800
    assert rows() == [("chest", 100, 800, "win")]
    assert "Chest win (max tier)" in reasons()


def test_glass_bridge_steps_and_falls(em, monkeypatch):
    import commands.economy.glass_bridge as GB
    monkeypatch.setattr(GB.random, "choice", lambda opts: "R")
    out = play("glass", "deal", {"bet": 100})
    assert out["table"]["safe"] == [] and "<svg" in out["table"]["scene"]
    with pytest.raises(casino.Refuse, match="first panel"):
        play("glass", "cashout")
    out = play("glass", "right")
    assert out["table"]["safe"] == ["R"] and out["table"]["step"] == 1
    out = play("glass", "left")
    assert out["table"]["outcome"] == "lose" and out["table"]["fellOn"] == "L"
    assert rows() == [("glass", 100, 0, "lose")]


def test_blockade_sails_and_anchors(em, monkeypatch):
    import commands.economy.blockade as BR
    monkeypatch.setattr(BR, "_roll_bust", lambda: 100.0)
    play("blockade", "deal", {"bet": 100})
    for _ in range(5):
        out = play("blockade", "sail")
    assert out["table"]["mult"] == 2.01
    out = play("blockade", "anchor")
    paid = int(100 * 2.01)              # the bot's own int(bet * mult), float rounding and all
    assert out["round"]["payout"] == paid
    assert rows() == [("blockade", 100, paid, "win")]
    assert "Blockade Run cashout" in reasons()


def test_darts_stand_and_bust(em, monkeypatch):
    import commands.economy.darts as DA
    throws = iter([("Treble 20", 60), ("Treble 20", 60), ("Single 5", 5)])
    monkeypatch.setattr(DA, "_throw_one", lambda: next(throws))
    play("darts", "deal", {"bet": 100})
    with pytest.raises(casino.Refuse, match="first dart"):
        play("darts", "stand")
    out = play("darts", "throw")
    assert out["table"]["total"] == 60
    out = play("darts", "stand")
    assert out["round"]["payout"] == 800                     # 59-60 pays 8x
    play("darts", "deal", {"bet": 100})
    play("darts", "throw")
    out = play("darts", "throw")                             # 65 busts
    assert out["table"]["result"] == "bust"
    assert rows() == [("darts", 100, 800, "win"), ("darts", 100, 0, "lose")]


def test_penalties_score_and_cash_out(em, monkeypatch):
    import commands.economy.penalty as PE
    monkeypatch.setattr(PE.random, "random", lambda: 0.0)
    play("penalty", "deal", {"bet": 100})
    with pytest.raises(casino.Refuse, match="Score at least once"):
        play("penalty", "cashout")
    with pytest.raises(casino.Refuse, match="spot"):
        play("penalty", "shoot", {"spot": "moon"})
    out = play("penalty", "shoot", {"spot": "tl"})
    assert out["table"]["goals"] == 1 and out["table"]["lastResult"] == "goal"
    out = play("penalty", "cashout")
    assert out["round"]["payout"] == out["table"]["payout"] > 100
    assert "Penalty cashout" in reasons()


def test_every_game_has_rules_and_a_label(em):
    for key, a in casino.registry().items():
        assert a.label and a.rules(), key
        t = casino.table(UID, key)
        assert t["table"] is None and t["limits"]["min"] >= 1
