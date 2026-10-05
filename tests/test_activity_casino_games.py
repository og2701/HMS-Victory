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


# --- penny falls ---------------------------------------------------------------------------
@pytest.fixture
def pf(em, tmp_path, monkeypatch):
    from lib.activities.casino.games import pennyfalls as PF
    monkeypatch.setattr(PF, "_FILE", str(tmp_path / "activity_pennyfalls.json"))
    monkeypatch.setattr(PF, "_loaded", True)
    monkeypatch.setattr(config, "PENNYFALLS_SEED_COINS", 110)
    monkeypatch.setattr(config, "PENNYFALLS_GOLD_COINS", 10)       # the tests' sums were written for 10
    monkeypatch.setattr(config, "PENNYFALLS_GOLD_EVERY", 150)
    PF._machines.clear()
    PF._live.clear()
    casino._watchers.clear()
    clock = [1_000_000.0]
    monkeypatch.setattr(PF.time, "time", lambda: clock[0])
    PF.clock = clock
    return PF


def test_penny_falls_buys_plays_and_cashes_out(em, pf):
    out = play("pennyfalls", "deal", {"bet": 200})
    assert out["table"]["cup"] == 20 and em.get_bb(UID) == 9_800
    assert out["table"]["board"] == {"coins": 110, "golds": 0, "fed": 0, "every": 150}
    assert casino.table(UID, "pennyfalls")["goldValue"] == 10
    pf.clock[0] += 10
    out = play("pennyfalls", "sync", {"dropped": 12, "won": 9, "lost": 1, "aims": {"middle": 10, "left": 2, "tap": 99}})
    assert out["table"]["cup"] == 20 - 12 + 9 and out["table"]["board"]["coins"] == 110 + 12 - 10
    pf.clock[0] += 5
    out = play("pennyfalls", "cashout", {"dropped": 2})
    assert out["round"]["payout"] == 15 * 10 and em.get_bb(UID) == 9_800 + 150
    assert rows() == [("pennyfalls", 200, 150, "lose")]
    assert "Davy Jones' Locker coins" in reasons() and "Davy Jones' Locker cashout" in reasons()
    assert pf.machine(UID)["coins"] == 112 + 2 and pf.machine(UID)["fed"] == 14
    from database import DatabaseManager
    row = DatabaseManager.fetch_one(
        "SELECT bought, staked, payout, dropped, coins_won, coins_lost, golds_given, board_before, board_after, "
        "trimmed, aim_left, aim_middle, aim_right, aim_tap FROM pennyfalls_cups WHERE user_id = ?", (str(UID),))
    assert tuple(row) == (20, 200, 150, 14, 9, 1, 0, 110, 114, 0, 2, 10, 0, 12)   # aims capped at what was dropped


def test_penny_falls_counts_in_the_banks_figures(em, pf):
    from lib.economy.bank_manager import BankManager
    play("pennyfalls", "deal", {"bet": 200})
    pf.clock[0] += 10
    play("pennyfalls", "cashout", {"dropped": 5, "won": 4})
    ledger = BankManager.get_ledger_stats()
    assert (ledger["pennyfalls_in"], ledger["pennyfalls_out"], ledger["pennyfalls_net"]) == (200, 190, 10)
    assert BankManager._game_amounts(100, "Davy Jones' Locker coins")[-2] == 100


def test_the_bank_backfills_penny_falls_from_the_ledger_once(em):
    import database
    from database import DatabaseManager
    from lib.economy.bank_manager import BankManager
    for reason, amount in (("Davy Jones' Locker coins", -300), ("Davy Jones' Locker cashout", 250),
                           ("Davy Jones' Locker coins", -100), ("Mines bet", -50)):
        DatabaseManager.execute("INSERT INTO user_transactions (user_id, ts, amount, balance_after, reason) "
                                "VALUES (?, 0, ?, 0, ?)", (str(UID), amount, reason))
    # the bank as it was before it had columns for the game
    DatabaseManager.execute("ALTER TABLE bank DROP COLUMN total_pennyfalls_in")
    DatabaseManager.execute("ALTER TABLE bank DROP COLUMN total_pennyfalls_out")
    database.init_db()
    ledger = BankManager.get_ledger_stats()
    assert (ledger["pennyfalls_in"], ledger["pennyfalls_out"]) == (400, 250)
    database.init_db()                                   # a later boot doesn't count it again
    assert BankManager.get_ledger_stats()["pennyfalls_in"] == 400


def test_penny_falls_tops_up_a_cup(em, pf):
    play("pennyfalls", "deal", {"bet": 100})
    out = play("pennyfalls", "buy", {"coins": 20})
    assert out["table"]["cup"] == 30 and out["table"]["staked"] == 300 and em.get_bb(UID) == 9_700
    play("pennyfalls", "cashout")
    with pytest.raises(casino.Refuse, match="whole number"):
        play("pennyfalls", "deal", {"bet": 105})


def test_penny_falls_drops_a_gold_coin_every_so_often_across_cups(em, pf, monkeypatch):
    monkeypatch.setattr(config, "PENNYFALLS_GOLD_EVERY", 10)
    play("pennyfalls", "deal", {"bet": 1_000})
    pf.clock[0] += 60
    out = play("pennyfalls", "sync", {"dropped": 25})
    assert out["table"]["release"] == 2 and out["table"]["board"]["golds"] == 2 and out["table"]["board"]["fed"] == 5
    pf.clock[0] += 60
    out = play("pennyfalls", "cashout", {"dropped": 4, "wonGold": 1})
    assert out["table"]["release"] == 0 and pf.machine(UID)["fed"] == 9 and pf.machine(UID)["golds"] == 1
    assert out["round"]["payout"] == (100 - 29 + 10) * 10          # a gold coin is worth 10 coins
    play("pennyfalls", "deal", {"bet": 100})
    pf.clock[0] += 60
    out = play("pennyfalls", "sync", {"dropped": 1})               # the count carried over the cups
    assert out["table"]["release"] == 1 and pf.machine(UID)["fed"] == 0


def test_penny_falls_trims_what_cant_have_happened(em, pf):
    play("pennyfalls", "deal", {"bet": 100})
    pf.clock[0] += 1
    out = play("pennyfalls", "sync", {"dropped": 50})                   # 10 in the cup, 1s to drop them
    assert out["table"]["dropped"] == 10 and out["table"]["cup"] == 0
    out = play("pennyfalls", "sync", {"won": 500, "wonGold": 3})         # more than the machine holds
    floor = pf.floor()
    assert out["table"]["won"] == 120 - floor and pf.machine(UID)["coins"] == floor and pf.machine(UID)["golds"] == 0


def test_penny_falls_books_the_side_gaps_share(em, pf):
    play("pennyfalls", "deal", {"bet": 1_000})                           # 100 coins
    for _ in range(10):                                                   # 300 off, none down the sides
        pf.clock[0] += 60
        play("pennyfalls", "sync", {"dropped": 30, "won": 30})
    out = play("pennyfalls", "cashout")
    least = int(0.08 * 300 - 3 * (300 * 0.08 * 0.92) ** 0.5)              # 9: well under the ~30 they take
    assert out["table"]["payout"] == (100 - least) * 10 and pf.machine(UID)["day_lost"] == least
    play("pennyfalls", "deal", {"bet": 100})                              # honest luck isn't touched
    pf.clock[0] += 60
    play("pennyfalls", "sync", {"dropped": 10, "won": 8, "lost": 2})
    assert play("pennyfalls", "cashout")["table"]["payout"] == 80


def _layout(coins, golds):
    import base64
    import struct
    packed = [struct.pack("<8h", 0, i, 30, 0, 0, 0, 0, 32767) for i in range(coins)]
    packed += [struct.pack("<8h", 1, i, 30, 0, 0, 0, 0, 32767) for i in range(golds)]
    return base64.b64encode(b"".join(packed)).decode()


def test_penny_falls_keeps_the_board_only_if_it_adds_up(em, pf):
    play("pennyfalls", "deal", {"bet": 100})
    pf.clock[0] += 10
    play("pennyfalls", "sync", {"dropped": 5, "won": 2, "layout": _layout(999, 0), "phase": 1.5})   # wrong count
    assert "layout" not in casino.table(UID, "pennyfalls")
    pf.clock[0] += 10
    play("pennyfalls", "sync", {"dropped": 2, "layout": _layout(115, 0), "phase": 2.5})           # 110 + 5 - 2 + 2
    t = casino.table(UID, "pennyfalls")
    assert t["layout"] == _layout(115, 0) and t["phase"] == 2.5
    out = play("pennyfalls", "sync", {"dropped": 1, "layout": "not a board"})
    assert "layout" not in out                                                        # moves don't carry it
    assert casino.table(UID, "pennyfalls")["layout"] == _layout(115, 0)


def test_penny_falls_shows_the_open_cup_and_who_stepped_away(em, pf):
    play("pennyfalls", "deal", {"bet": 200})
    s = sessions._sittings[UID]
    assert (s.open_net, s.open_note) == (0, "20 coins in the cup")
    pf.clock[0] += 10
    play("pennyfalls", "sync", {"dropped": 5, "won": 8})
    assert (s.open_net, s.open_note) == (30, "23 coins in the cup")
    sessions._finish(s)
    head = sessions.live_view(s, sessions.IMAGE).children[0].content
    assert "stepped away from **Davy Jones' Locker** with 23 coins in the cup" in head
    from lib.activities.casino import cards
    assert cards.draw_live("pennyfalls", "Davy Jones' Locker", "cups", [], None, 30, "23 coins in the cup", "STEPPED AWAY")


def test_penny_falls_can_be_spectated_from_replayed_batches(em, pf):
    import lib.activities.casino as casino_mod
    play("pennyfalls", "deal", {"bet": 100})
    pf.clock[0] += 5
    assert play("pennyfalls", "sync", {"dropped": 1})["table"]["watched"] is False
    casino_mod.watch(UID, "pennyfalls", "Tester")                               # someone looks
    pf.clock[0] += 5
    live = {"layout": _layout(111, 0), "phase": 3.5, "span": 2500, "drops": [[120, 0, 1.5, 0.3, 0, 0, 0, 7, 4, 0]]}
    out = play("pennyfalls", "sync", {"dropped": 1, "live": live})
    assert out["table"]["watched"] is True
    seen = casino_mod.watch(UID, "pennyfalls", "Tester")
    assert seen["live"]["seq"] == 1 and seen["live"]["drops"] == [[120, 0, 1.5, 0.3, 0, 0, 0, 7, 4, 0]]
    play("pennyfalls", "sync", {"dropped": 1, "live": {**live, "drops": "nonsense"}})   # ignored
    assert casino_mod.watch(UID, "pennyfalls", "Tester")["live"]["seq"] == 1


def test_penny_falls_caps_a_cup_and_a_day(em, pf, monkeypatch):
    monkeypatch.setattr(config, "PENNYFALLS_CUP_MAX_NET", 300)
    monkeypatch.setattr(config, "PENNYFALLS_DAY_MAX_NET", 500)
    monkeypatch.setattr(config, "PENNYFALLS_FLOOR", 0)             # wins this size need the whole shelf
    play("pennyfalls", "deal", {"bet": 100})
    out = play("pennyfalls", "cashout", {"won": 60})
    assert out["round"]["payout"] == 100 + 300 and "cup limit" in out["round"]["outcome"]
    play("pennyfalls", "deal", {"bet": 100})
    out = play("pennyfalls", "cashout", {"won": 40})
    assert out["round"]["payout"] == 100 + 200 and "daily limit" in out["round"]["outcome"]


# --- Plinko ---------------------------------------------------------------------------------
def test_plinko_pays_the_slot_the_ball_lands_in(em, monkeypatch):
    from lib.activities.casino.games import plinko as P
    path = iter([1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 1])                 # eleven rights: the slot next to the edge
    monkeypatch.setattr(P._rng, "randint", lambda a, b: next(path))
    out = play("plinko", "deal", {"bet": 100, "risk": "high"})
    t = out["table"]
    assert (t["slot"], t["mult"], t["payout"], t["net"]) == (11, 22.0, 2200, 2100)
    assert len(t["path"]) == P.ROWS and out["round"]["payout"] == 2200 and not out["inPlay"]
    assert em.get_bb(UID) == 10_000 - 100 + 2200
    assert rows() == [("plinko", 100, 2200, "22x high")]
    assert reasons()[-2:] == ["Plinko bet", "Plinko win"]


def test_plinko_rounds_down_and_pays_a_fifth_in_the_middle(em, monkeypatch):
    from lib.activities.casino.games import plinko as P
    path = iter([1, 0] * 6)                                            # six and six: the middle slot
    monkeypatch.setattr(P._rng, "randint", lambda a, b: next(path))
    out = play("plinko", "deal", {"bet": 37, "risk": "high"})
    assert (out["table"]["slot"], out["table"]["payout"]) == (6, 7)   # 0.2x of 37 is 7.4: rounded down
    assert rows() == [("plinko", 37, 7, "0.2x high")]


def test_plinko_refuses_a_made_up_risk(em):
    with pytest.raises(casino.Refuse, match="low, medium or high"):
        play("plinko", "deal", {"bet": 100, "risk": "extreme"})
    assert em.get_bb(UID) == 10_000 and rows() == []


def test_plinko_keeps_a_small_edge_at_every_risk():
    from lib.activities.casino.games import plinko as P
    for risk in P.RISKS:
        table = P.TENTHS[risk]
        assert len(table) == P.ROWS + 1 and table == table[::-1], risk          # 13 slots, the same both sides
        assert 0.96 <= P.returns(risk) <= 0.98, risk
    assert P.Plinko.max_multiplier == 150


def test_plinko_counts_in_the_banks_figures(em, monkeypatch):
    from lib.activities.casino.games import plinko as P
    from lib.economy.bank_manager import BankManager
    path = iter([0] * 12)                                              # all lefts: the far corner
    monkeypatch.setattr(P._rng, "randint", lambda a, b: next(path))
    play("plinko", "deal", {"bet": 10, "risk": "low"})
    ledger = BankManager.get_ledger_stats()
    assert (ledger["plinko_in"], ledger["plinko_out"], ledger["plinko_net"]) == (10, 80, -70)
    assert BankManager._game_amounts(100, "Plinko bet")[-1] == 100
    assert sum(BankManager._game_amounts(100, "Plinko win")) == 100


def test_plinko_only_posts_the_far_edges_as_big_wins():
    from lib.activities.casino.base import Round
    assert not sessions.is_big(Round(100, 800, "8x"), "plinko")          # 8x would be big anywhere else
    assert sessions.is_big(Round(100, 800, "8x"), "slots")
    assert sessions.is_big(Round(100, 2200, "22x"), "plinko")
    assert sessions.is_big(Round(1000, 8000, "8x"), "plinko")            # but a big enough sum still is


def test_every_game_has_rules_and_a_label(em):
    for key, a in casino.registry().items():
        assert a.label and a.rules(), key
        t = casino.table(UID, key)
        assert t["table"] is None and t["limits"]["min"] >= 1
