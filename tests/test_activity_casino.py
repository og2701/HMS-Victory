"""The casino inside the activity: hands go through the game's own stake and payout paths,
pay exactly once, keep hidden cards hidden, survive a restart, and add up in #casino."""

import asyncio
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OPENAI_TOKEN", "mock-token")

import config
from lib.activities import auth, casino, server
from lib.activities.casino import base, sessions
from commands.economy import blackjack as B

SECRET = "test-secret"
WORKSHOP = 1141037835445616640
UID = 4242


@pytest.fixture
def economy(tmp_path, monkeypatch):
    import database
    if database.DatabaseManager._connection is not None:
        database.DatabaseManager._connection.close()
        database.DatabaseManager._connection = None
    monkeypatch.setattr(database, "DB_FILE", str(tmp_path / "test.db"))
    database.init_db()
    import lib.economy.economy_manager as em
    em._HIST_LAST.clear()
    assert em.add_bb(UID, 5_000, reason="seed", taxable=False)
    monkeypatch.setattr(base, "_FILE", str(tmp_path / "activity_casino.json"))
    base._games.clear()
    base._finished.clear()
    base._locks.clear()
    base._recent.clear()
    monkeypatch.setattr(base, "_loaded", True)
    sessions._sittings.clear()
    monkeypatch.setattr(config, "BLACKJACK_MIN_BET", 5)
    monkeypatch.setattr(config, "BLACKJACK_MAX_BET", 1_000)
    from lib.economy import reserve_policy
    monkeypatch.setattr(reserve_policy, "max_casino_bet", lambda game_max_multiplier=3.0, static_max=0: static_max)
    yield em
    database.DatabaseManager._connection.close()
    database.DatabaseManager._connection = None


def stack(monkeypatch, player, dealer, rest=()):
    """Fix the next deck: the player gets `player`, the dealer `dealer`, then `rest` in order."""
    # BlackjackGame.new pops player, player, dealer, dealer from the end.
    deck =list(reversed(list(rest))) + [dealer[1], dealer[0], player[1], player[0]]
    monkeypatch.setattr(B, "_fresh_deck", lambda: list(deck))


def move(action, body=None):
    return asyncio.run(casino.move(UID, "Tester", "blackjack", action, body or {}))


def results():
    from database import DatabaseManager
    return DatabaseManager.fetch_all("SELECT game, staked, payout, outcome FROM casino_results")


# --- money --------------------------------------------------------------------------------
def test_a_deal_takes_the_stake_and_hides_the_hole_card(economy, monkeypatch):
    stack(monkeypatch, ["TH", "6C"], ["KS", "7D"], rest=["2C"] * 10)
    out = move("deal", {"bet": 500})
    assert economy.get_bb(UID) == 4_500
    t = out["table"]
    assert t["dealer"] == ["KS", None]           # the 7 stays on the server
    assert t["dealerTotal"] == 10
    assert t["player"] == ["TH", "6C"] and t["playerTotal"] == 16
    assert "deck" not in t
    assert out["inPlay"] and out["balance"] == 4_500


def test_a_won_hand_pays_once_through_the_blackjack_paths(economy, monkeypatch):
    stack(monkeypatch, ["TH", "9C"], ["KS", "7D"], rest=["2C"] * 10)
    move("deal", {"bet": 500})
    out = move("stand")
    assert out["table"]["over"] and out["table"]["outcome"] == "win"
    assert out["round"] == {"staked": 500, "payout": 1_000, "net": 500, "outcome": "Win"}
    assert economy.get_bb(UID) == 5_500
    assert results() == [("blackjack", 500, 1_000, "win")]
    with pytest.raises(casino.Refuse):
        move("stand")                               # nothing left to settle twice
    assert economy.get_bb(UID) == 5_500


def test_double_down_takes_a_second_stake(economy, monkeypatch):
    stack(monkeypatch, ["5H", "6C"], ["KS", "7D"], rest=["TD"] + ["2C"] * 10)
    move("deal", {"bet": 400})
    out = move("double")
    assert out["table"]["doubled"] and out["table"]["staked"] == 800
    assert out["table"]["outcome"] == "win"          # 21 against 17
    assert economy.get_bb(UID) == 5_000 - 800 + 1_600


def test_a_natural_settles_on_the_deal(economy, monkeypatch):
    stack(monkeypatch, ["AH", "KC"], ["9S", "7D"])
    out = move("deal", {"bet": 200})
    assert out["table"]["over"] and out["table"]["outcome"] == "blackjack"
    assert economy.get_bb(UID) == 5_000 + 300
    assert not out["inPlay"]


def test_bets_are_checked_before_anything_moves(economy, monkeypatch):
    stack(monkeypatch, ["TH", "6C"], ["KS", "7D"], rest=["2C"] * 10)
    for bad, why in ((2, "minimum"), (2_000, "maximum"), ("lots", "whole number")):
        with pytest.raises(casino.Refuse, match=why):
            move("deal", {"bet": bad})
    economy.remove_bb(UID, 4_900, reason="test drain")
    with pytest.raises(casino.Refuse, match="enough"):
        move("deal", {"bet": 500})
    assert results() == []


def test_one_hand_at_a_time(economy, monkeypatch):
    stack(monkeypatch, ["TH", "6C"], ["KS", "7D"], rest=["2C"] * 10)
    with pytest.raises(casino.Refuse, match="Deal a new one"):
        move("hit")
    move("deal", {"bet": 100})
    with pytest.raises(casino.Refuse, match="Finish"):
        move("deal", {"bet": 100})


def test_a_second_tap_while_the_first_is_running_is_turned_away(economy, monkeypatch):
    stack(monkeypatch, ["TH", "6C"], ["KS", "7D"], rest=["2C"] * 10)

    async def both():
        lock = base._lock(UID)
        await lock.acquire()
        try:
            with pytest.raises(casino.Busy):
                await casino.move(UID, "Tester", "blackjack", "deal", {"bet": 100})
        finally:
            lock.release()
    asyncio.run(both())


def test_an_open_hand_survives_a_restart(economy, monkeypatch):
    stack(monkeypatch, ["TH", "6C"], ["KS", "7D"], rest=["2C"] * 10)
    move("deal", {"bet": 100})
    base._games.clear()
    monkeypatch.setattr(base, "_loaded", False)
    out = casino.table(UID, "blackjack")
    assert out["inPlay"] and out["table"]["player"] == ["TH", "6C"]
    assert move("stand")["table"]["over"]


# --- #casino -------------------------------------------------------------------------------
def test_rounds_add_up_into_one_sitting_and_a_big_win_gets_its_own_post(monkeypatch):
    sessions._sittings.clear()
    big = []
    monkeypatch.setattr(sessions, "_schedule", lambda s: None)
    monkeypatch.setattr(sessions, "_spawn", lambda coro: big.append(coro) or coro.close())
    sessions.record(1, "blackjack", "Blackjack", "hands", base.Round(100, 200, "Win"))
    sessions.record(1, "blackjack", "Blackjack", "hands", base.Round(100, 0, "Loss"))
    sessions.record(1, "blackjack", "Blackjack", "hands", base.Round(100, 600, "Lucky"))
    s = sessions._sittings[1]
    assert (s.rounds, s.net, s.best_net, s.best_text) == (3, 500, 500, "Lucky")
    assert len(big) == 1                       # only the 6x round
    assert sessions.summary(1, "blackjack") == {"rounds": 3, "net": 500}
    assert sessions.summary(1, "mines") == {"rounds": 0, "net": 0}
    sessions.record(1, "mines", "Mines", "rounds", base.Round(100, 0, ""))
    assert s.done and sessions._sittings[1].key == "mines"


def test_a_sitting_keeps_each_round_and_the_last_table_for_its_picture(monkeypatch):
    sessions._sittings.clear()
    monkeypatch.setattr(sessions, "_schedule", lambda s: None)
    monkeypatch.setattr(sessions, "_spawn", lambda coro: coro.close())
    sessions.record(1, "mines", "Mines", "rounds", base.Round(100, 149, "1.49x"), {"revealed": [1]})
    sessions.record(1, "mines", "Mines", "rounds", base.Round(100, 900, "9.00x"), {"revealed": [2]})
    s = sessions._sittings[1]
    assert [h["net"] for h in s.history] == [49, 800]
    assert [h["big"] for h in s.history] == [False, True]
    assert s.last_view == {"revealed": [2]}


def test_the_live_line_shows_its_picture_and_falls_back_to_text():
    s = sessions.Sitting(1, "mines", "Mines", "rounds", rounds=2, net=50)
    pictured = sessions.live_view(s, sessions.IMAGE)
    kinds = [type(c).__name__ for c in pictured.children]
    assert kinds == ["TextDisplay", "MediaGallery", "ActionRow"]
    assert "is playing **Mines**" in pictured.children[0].content
    text = sessions.live_view(s)
    assert [type(c).__name__ for c in text.children] == ["Container", "ActionRow"]


def test_a_channel_that_refuses_files_still_gets_the_text_line(monkeypatch):
    import discord
    from lib.activities import launcher
    sent = []

    async def post_view(ch, view, files=None):
        if files:
            raise discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Missing Permissions")
        sent.append([type(c).__name__ for c in view.children])
        return 99

    async def picture(s):
        return b"png"

    monkeypatch.setattr(launcher, "post_view", post_view)
    monkeypatch.setattr(sessions, "_channel", lambda: 1)
    monkeypatch.setattr(sessions, "_picture", picture)
    monkeypatch.setattr(sessions, "_no_files_until", 0.0)
    s = sessions.Sitting(1, "mines", "Mines", "rounds", rounds=1, net=50, dirty=True)
    asyncio.run(sessions._flush(s))
    assert s.message_id == 99 and sent == [["Container", "ActionRow"]]
    assert sessions._no_files_until > 0


def test_the_live_strip_is_drawn_without_a_browser():
    from PIL import Image
    from lib.activities.casino import cards
    history = [{"net": n, "big": False} for n in (50, -100, 30)]
    png = cards.draw_live("mines", "Mines", "rounds", history, None)
    assert Image.open(__import__("io").BytesIO(png)).size == (1640, 680)


def test_every_game_has_a_final_board_and_a_summary():
    from lib.activities.casino import cards
    round_ = {"net": 50, "staked": 100, "payout": 150, "multiple": 1.5, "outcome": "Win", "big": False}
    tables = {
        "blackjack": {"player": ["AS", "KH"], "dealer": ["9C", "TD"], "playerTotal": 21, "dealerTotal": 19},
        "higherlower": {"run": ["9H", "4C"], "steps": 1},
        "videopoker": {"cards": ["JS", "JH", "KC", "4D", "2D"], "held": [True, True, False, False, False],
                       "hand": "Jacks or Better"},
        "reddog": {"first": "4C", "second": "JH", "third": "8S", "spread": 6, "pair": False, "consecutive": False},
        "tcp": {"player": ["QH", "QS", "4D"], "dealer": ["KC", "8D", "2S"], "playerHand": "Pair", "dealerHand": "King high"},
        "roulette": {"number": 17, "color": "black", "bets": {"black": 50, "straight:17": 10}, "won": ["black"]},
        "slots": {"reels": ["crown", "lion", "cherry"], "mult": 0},
        "mines": {"tiles": 24, "cols": 4, "revealed": [1], "hit": 2, "minesAt": [2, 7, 9]},
        "chest": {"tier": 1, "tiers": [{"name": "Wood", "mult": 1}, {"name": "Silver", "mult": 1.8}], "outcome": "win"},
        "glass": {"scene": "<svg></svg>"},
        "blockade": {"outcome": "lose", "mult": 1.5, "caught": 1.62},
        "darts": {"throws": [{"label": "T20", "value": 60}], "total": 60},
        "penalty": {"goals": 2, "shots": 5},
    }
    assert set(tables) == set(casino.ORDER)
    for key, table in tables.items():
        page = cards.result_html(key, key.title(), "1 round", round_, table)
        assert "class=\"panel\"" in page and "+50" in page, key
        assert cards.tile_html(key).count("class=\"tile\"") == 1
    mines = cards.board_html("mines", tables["mines"])
    assert mines.count("mt coin") == 1 and mines.count("mt boom") == 1 and mines.count("mt mine") == 2
    history = [dict(round_, net=n) for n in (50, -100, 0, 150)]
    page = cards.summary_html("blackjack", "Blackjack", "hands", history, 12)
    assert "2 won · 1 lost" in page and "4 hands · 12 min" in page and "+100" in page


# --- the API -------------------------------------------------------------------------------
def _client(monkeypatch):
    monkeypatch.setenv("ACTIVITIES_SESSION_SECRET", SECRET)
    monkeypatch.setattr(config, "ACTIVITIES_ALLOWED_CHANNELS", [])
    monkeypatch.setattr(config, "ACTIVITIES_CASINO_CHANNELS", [WORKSHOP])
    from lib.core import restrictions
    monkeypatch.setattr(restrictions, "is_blocked", lambda uid, cmd: None)
    member = SimpleNamespace(display_name="Tester")
    guild = SimpleNamespace(get_member=lambda uid: member)
    return SimpleNamespace(maintenance_mode=False, session=None, get_guild=lambda gid: guild)


def _call(client, method, path, ch=WORKSHOP, body=None):
    from aiohttp.test_utils import TestClient, TestServer

    async def go():
        async with TestClient(TestServer(server.build_app(client))) as http:
            headers = {"Authorization": f"Bearer {auth.make_session(UID, ch, secret=SECRET)}"}
            r = await http.request(method, path, headers=headers, json=body)
            return r.status, await r.json()
    return asyncio.run(go())


def test_the_api_deals_and_plays(economy, monkeypatch):
    client = _client(monkeypatch)
    stack(monkeypatch, ["TH", "9C"], ["KS", "7D"], rest=["2C"] * 10)
    status, out = _call(client, "POST", "/api/casino/blackjack/deal", body={"bet": 250})
    assert status == 200 and out["table"]["dealer"] == ["KS", None]
    status, out = _call(client, "POST", "/api/casino/blackjack/stand")
    assert status == 200 and out["round"]["net"] == 250
    status, out = _call(client, "POST", "/api/casino/blackjack/stand")
    assert status == 422


def test_leaving_the_table_ends_the_sitting(economy, monkeypatch):
    client = _client(monkeypatch)
    sessions._sittings.clear()
    monkeypatch.setattr(sessions, "_schedule", lambda s: None)
    sessions.record(UID, "blackjack", "Blackjack", "hands", base.Round(100, 200, "Win"))
    s = sessions._sittings[UID]
    status, _ = _call(client, "POST", "/api/casino/mines/leave")     # another table: no effect
    assert status == 200 and not s.done
    status, _ = _call(client, "POST", "/api/casino/blackjack/here")
    assert status == 200 and not s.done
    status, _ = _call(client, "POST", "/api/casino/blackjack/leave")
    assert status == 200 and s.done and UID not in sessions._sittings


def test_a_table_that_stops_checking_in_is_closed_by_the_sweep(monkeypatch):
    sessions._sittings.clear()
    monkeypatch.setattr(sessions, "_schedule", lambda s: None)
    sessions.record(1, "darts", "Darts", "rounds", base.Round(100, 0, ""))
    s = sessions._sittings[1]
    s.seen -= sessions.GONE_AFTER - 10
    sessions.sweep()
    assert not s.done
    sessions.here(1, "darts")
    s.last -= sessions.GONE_AFTER + 5                # no rounds lately, but still checking in
    sessions.sweep()
    assert not s.done
    s.seen -= sessions.GONE_AFTER + 5
    sessions.sweep()
    assert s.done


def test_the_casino_stays_shut_outside_its_test_channels(economy, monkeypatch):
    client = _client(monkeypatch)
    status, out = _call(client, "GET", "/api/casino/blackjack", ch=999)
    assert status == 403
    status, home = _call(client, "GET", "/api/home", ch=999)
    assert status == 200 and home["casinoOpen"] is False


def test_home_lists_the_last_played_game_first(economy, monkeypatch):
    client = _client(monkeypatch)
    stack(monkeypatch, ["TH", "9C"], ["KS", "7D"], rest=["2C"] * 10)
    _call(client, "POST", "/api/casino/blackjack/deal", body={"bet": 100})
    _call(client, "POST", "/api/casino/blackjack/stand")
    status, home = _call(client, "GET", "/api/home")
    assert status == 200 and home["casinoOpen"]
    first = home["casino"][0]
    assert first["key"] == "blackjack" and first["lastNet"] == 100 and first["lastPlayed"]
    assert home["balance"] == 5_100


def test_opening_routes(monkeypatch):
    from lib.activities import launcher
    monkeypatch.setattr(launcher, "take_requested", lambda uid: None)
    assert server._game_for(1, {}) == "home"
    assert server._game_for(1, {"game": "casino:blackjack"}) == "casino:blackjack"
    assert server._game_for(1, {"game": "casino:nope"}) == "home"
    assert server._game_for(1, {"game": "crossword"}) == "crossword"
