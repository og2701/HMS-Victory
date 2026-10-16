"""Bank Job: the crew and its roles, the targets' limits, every role's puzzle and how it's judged, the
vault's maths (the bank keeps its edge however anyone plays), settling buy-ins and takes, the clean
job bonus, and the clocks pausing over a restart."""

import json
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OPENAI_TOKEN", "mock-token")

from lib.activities import heist as H  # noqa: E402
from lib.economy.activity_badges import heist_over as real_heist_over  # noqa: E402

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
    monkeypatch.setattr(H, "_FILE", str(tmp_path / "heist.json"))
    H._rooms.clear()
    H._posts.clear()
    H._bonus.clear()
    H._heat.update(v=0.0, at=0.0)
    monkeypatch.setattr(H, "_loaded", True)
    monkeypatch.setattr(H, "_board_cache", (0.0, []))
    monkeypatch.setattr(H, "tenure_ok", lambda uid: True)
    from lib.economy import activity_badges
    monkeypatch.setattr(activity_badges, "heist_over", lambda room: None)    # badges pay rewards; tested below
    clock = [1_000_000.0]
    monkeypatch.setattr(H.time, "time", lambda: clock[0])
    economy.clock = clock
    yield economy
    database.DatabaseManager._connection.close()
    database.DatabaseManager._connection = None


def tick(em, s):
    em.clock[0] += s


def supply():
    from database import DatabaseManager
    return DatabaseManager.fetch_one("SELECT SUM(balance) FROM ukpence")[0]


def crew(em, players=(A, B, D), stake=100, target="post"):
    rid = H.open_room(players[0], stake, target)["id"]
    for p in players[1:]:
        H.join(p, rid)
    H.start(players[0], rid)
    return rid


def solve(rid, uid):
    """Play the role's puzzle perfectly, peeking at the answer the way only the bot can."""
    r = H._rooms[rid]
    role = r["roles"][str(uid)]
    p = r["job"][role]
    if role == "hacker":
        H.play(uid, rid, {"code": p["secret"]})
    elif role == "safecracker":
        for n in list(p["secret"]):
            H.play(uid, rid, {"n": n})
    elif role == "lookout":
        H.play(uid, rid, {"seq": p["seq"]})
    elif role == "wheelman":
        H.play(uid, rid, {"path": cheapest_path(p["costs"])})
    elif role == "face":
        H.play(uid, rid, {"ready": True})
        for q in list(p["questions"]):
            H.play(uid, rid, {"answer": q["answer"]})
    elif role == "muscle":
        H.play(uid, rid, {"load": best_load(p["bags"], p["cap"])})


def cheapest_path(costs):
    import heapq
    start, end = (H.GRID - 1, 0), (0, H.GRID - 1)
    best, prev, todo = {start: 0}, {}, [(0, start)]
    while todo:
        d, cell = heapq.heappop(todo)
        if cell == end:
            break
        r, c = cell
        for n in ((r + 1, c), (r - 1, c), (r, c + 1), (r, c - 1)):
            if 0 <= n[0] < H.GRID and 0 <= n[1] < H.GRID and costs[n[0]][n[1]] is not None:
                nd = d + costs[n[0]][n[1]]
                if nd < best.get(n, 1 << 30):
                    best[n], prev[n] = nd, cell
                    heapq.heappush(todo, (nd, n))
    path, cell = [end], end
    while cell != start:
        cell = prev[cell]
        path.append(cell)
    return [list(x) for x in reversed(path)]


def best_load(bags, cap):
    from itertools import combinations
    best, pick = -1, []
    for n in range(len(bags) + 1):
        for combo in combinations(range(len(bags)), n):
            w = sum(bags[i]["w"] for i in combo)
            v = sum(bags[i]["v"] for i in combo)
            if w <= cap and v > best:
                best, pick = v, list(combo)
    return pick


def to_vault(em, rid, police_take=None, approach="quiet"):
    """Vote, do the job perfectly, and open the vault with the police coming at ``police_take`` (None: never)."""
    r = H._rooms[rid]
    here = [u for u in r["players"] if u not in r["left"]]
    for u in here:
        H.vote(u, rid, approach)
    assert r["state"] == "job"
    for u in here:
        solve(rid, u)
    assert r["state"] == "report"
    v = r["vault"]
    v["police"] = None if police_take is None else (0.0 if police_take <= 1 else
                                                   v["secs"] * H.math.log(police_take) / H.math.log(r["cap"]))
    tick(em, H.REPORT_SECONDS)
    H.room(r["host"], rid, name_of)
    assert r["state"] == "vault"


def secs_to(r, take):
    return r["vault"]["secs"] * H.math.log(take) / H.math.log(r["cap"])


# ---- the puzzles ------------------------------------------------------------------------------

def test_the_keypad_says_how_close_a_guess_is():
    assert H._feedback("1234", "1234") == (4, 0)
    assert H._feedback("1234", "4321") == (0, 4)
    assert H._feedback("1122", "1212") == (2, 2)
    assert H._feedback("1111", "1222") == (1, 0)


def test_every_puzzle_scores_one_when_played_perfectly_and_nothing_when_not_played():
    rng = random.Random(4)
    for _ in range(30):
        for role, make in H.MAKERS.items():
            p = make(rng)
            assert H._score(role, p) == 0.0 and not H._done(role, p)
    room = {"id": "x"}
    for seed in range(20):
        rng = random.Random(seed)
        for role, make in H.MAKERS.items():
            p = make(rng)
            if role == "hacker":
                H._move(role, p, {"code": p["secret"]})
            elif role == "safecracker":
                for n in list(p["secret"]):
                    H._move(role, p, {"n": n})
            elif role == "lookout":
                H._move(role, p, {"seq": p["seq"]})
            elif role == "wheelman":
                H._move(role, p, {"path": cheapest_path(p["costs"])})
            elif role == "face":
                H._move(role, p, {"ready": True})
                for q in list(p["questions"]):
                    H._move(role, p, {"answer": q["answer"]})
            elif role == "muscle":
                H._move(role, p, {"load": best_load(p["bags"], p["cap"])})
            assert H._done(role, p) and H._score(role, p) == pytest.approx(1.0), (role, seed)
    assert room


def test_the_safe_says_which_way_and_how_warm():
    p = {"secret": [50, 10, 90], "dials": [], "open": 0}
    H._move("safecracker", p, {"n": 20})
    assert p["dials"][-1]["said"] == "cold" and p["dials"][-1]["way"] == 1
    H._move("safecracker", p, {"n": 45})
    assert p["dials"][-1]["said"] == "hot"
    H._move("safecracker", p, {"n": 52})
    assert p["dials"][-1]["said"] == "click" and p["open"] == 1
    with pytest.raises(H.Refuse):
        H._move("safecracker", p, {"n": 100})


def test_a_route_has_to_join_up_and_a_van_cant_be_overloaded():
    costs = [[1] * H.GRID for _ in range(H.GRID)]
    costs[4][0] = None
    assert H._route_cost(costs, [[5, 0], [4, 0]]) is None                     # into a roadblock
    assert H._route_cost(costs, [[5, 0], [5, 2]]) is None                     # a jump
    straight = [[5, c] for c in range(H.GRID)] + [[r, 5] for r in range(4, -1, -1)]
    assert H._route_cost(costs, straight) == 10 == H._cheapest(costs)
    p = H._muscle(random.Random(1))
    with pytest.raises(H.Refuse):
        H._move("muscle", p, {"load": list(range(len(p["bags"])))})
    with pytest.raises(H.Refuse):
        H._move("muscle", p, {"load": [0, 0]})


def test_the_face_only_sees_the_story_until_they_go_in():
    p = H._face(random.Random(2))
    seen = H._public("face", p)
    assert "story" in seen and "questions" not in seen
    with pytest.raises(H.Refuse):
        H._move("face", p, {"answer": 0})
    H._move("face", p, {"ready": True})
    seen = H._public("face", p)
    assert "story" not in seen and len(seen["questions"]) == 1 and "answer" not in seen["questions"][0]


def test_the_page_never_sees_an_answer(em):
    rid = crew(em, stake=0)
    r = H._rooms[rid]
    for u in r["players"]:
        H.vote(u, rid, "quiet")
    for u in r["players"]:
        text = json.dumps(H.view(r, u, name_of))
        role = r["roles"][str(u)]
        if role == "hacker":
            assert r["job"]["hacker"]["secret"] not in text.replace('"len"', "")
        assert '"secret"' not in text and '"best"' not in text and '"police"' not in text


# ---- the vault's maths -------------------------------------------------------------------------

def test_the_bank_keeps_its_edge_however_long_anyone_waits():
    """Leaving at m pays m with chance (1 - edge)/m: the return is 1 - edge at every target."""
    rng = random.Random(7)
    for score in (0.0, 0.6, 1.0):
        e = H.edge(score)
        draws = [H.police_take(e, rng.random() or 1e-12) for _ in range(200_000)]
        for target in (1.01, 1.5, 3.0, 8.0):
            rtp = sum(target for m in draws if m > target) / len(draws)
            assert rtp == pytest.approx(1 - e, abs=0.01), (score, target)
    assert H.edge(1.0) == pytest.approx(H.EDGE_BEST) and H.edge(0.0) == pytest.approx(H.EDGE_WORST)


def test_the_take_climbs_from_one_to_the_cap():
    assert H.take_at(0, 6.0, 30) == 1.0
    assert H.take_at(15, 4.0, 30) == pytest.approx(2.0)
    assert H.take_at(30, 6.0, 30) == pytest.approx(6.0) == H.take_at(99, 6.0, 30)


def test_a_role_nobody_took_is_winged_and_the_approach_moves_the_weights(em):
    rid = crew(em, players=(A, B), stake=0, target="society")
    r = H._rooms[rid]
    r["approach"] = "quiet"
    r["twist"] = {}
    r["job"] = {"hacker": {"score": 1.0}, "safecracker": {"score": 1.0}}
    quiet = H.job_score(r)
    r["approach"] = "loud"
    loud = H.job_score(r)
    assert quiet > loud > H.WINGING_IT
    assert quiet == pytest.approx(0.44 + 0.56 * H.WINGING_IT)


# ---- the crew -----------------------------------------------------------------------------------

def test_roles_are_one_each_and_the_host_picks_the_target(em):
    rid = H.open_room(A, 100, "post", role="face")["id"]
    H.join(B, rid, "face")                       # taken: B gets the first free job instead
    r = H._rooms[rid]
    assert r["roles"] == {str(A): "face", str(B): "hacker"}
    with pytest.raises(H.Refuse):
        H.pick_role(B, rid, "face")
    H.pick_role(B, rid, "muscle")
    with pytest.raises(H.Refuse):
        H.pick_target(B, rid, "vault")
    H.pick_target(A, rid, "vault")
    assert r["target"] == "vault"


def test_each_target_needs_its_crew_and_a_stake_needs_two(em):
    rid = H.open_room(A, 100, "vault")["id"]
    H.join(B, rid)
    with pytest.raises(H.Refuse, match="crew of 3"):
        H.start(A, rid)
    solo = H.open_room(D, 50, "post")["id"]
    with pytest.raises(H.Refuse, match="two"):
        H.start(D, solo)
    H.pick_target(D, solo, "post", 0)
    H.start(D, solo)                              # a free post office job on your own is practice
    assert H._rooms[solo]["state"] == "plan"


def test_buy_ins_are_capped_by_target_and_by_the_banks_spare_reserves(em, monkeypatch):
    import lib.economy.reserve_policy as rp
    monkeypatch.setattr(rp, "bank_reserves", lambda: 1_000_000)
    assert H.max_stake("post") == 1000 and H.max_stake("vault") == 500
    monkeypatch.setattr(rp, "bank_reserves", lambda: 160_000)    # 80k spare: 12k of exposure
    assert H.max_stake("vault") == 100 and H.max_stake("post") == 500
    monkeypatch.setattr(rp, "bank_reserves", lambda: 80_000)
    assert H.max_stake("post") == 0
    with pytest.raises(H.Refuse, match="Free jobs only"):
        H.open_room(A, 50, "post")


def test_the_heat_shrinks_the_vault_and_cools_off(em):
    base = H.cap_for("society")
    H._warm(1.0, H.time.time())
    hot = H.cap_for("society")
    assert hot < base and H.heat_label(H.heat()) == "Lockdown"
    tick(em, H.HEAT_HALF_LIFE)
    assert H.heat() == pytest.approx(0.5, abs=0.01)


# ---- settling -----------------------------------------------------------------------------------

def test_buy_ins_go_in_at_the_start_and_takes_come_out_at_the_end(em):
    before = supply()
    rid = crew(em, stake=100)
    assert em.get_bb(A) == em.get_bb(B) == em.get_bb(D) == 9_900
    to_vault(em, rid, police_take=2.0)
    r = H._rooms[rid]
    tick(em, secs_to(r, 1.6))
    H.leg_it(A, rid)
    with pytest.raises(H.Refuse, match="already out"):
        H.leg_it(A, rid)
    tick(em, secs_to(r, 2.0) - secs_to(r, 1.6) + 0.01)
    H.sweep()
    assert r["over"] and r["how"] == "nicked"
    assert r["results"][str(A)]["how"] == "escaped" and r["results"][str(B)]["how"] == "nicked"
    assert em.get_bb(A) == 9_900 + int(100 * r["results"][str(A)]["take"])
    assert em.get_bb(B) == 9_900 and em.get_bb(D) == 9_900
    with pytest.raises(H.Refuse, match="police"):
        H.leg_it(B, rid)
    for _ in range(3):                              # a settled job never pays twice
        H.sweep()
        H.room(A, rid, name_of)
    assert em.get_bb(A) == 9_900 + int(100 * r["results"][str(A)]["take"])
    assert supply() == before


def test_nobody_gets_out_when_the_police_are_waiting(em):
    rid = crew(em, stake=50)
    to_vault(em, rid, police_take=0.95)
    with pytest.raises(H.Refuse):
        H.leg_it(A, rid)
    H.sweep()
    r = H._rooms[rid]
    assert r["over"] and all(x["how"] == "nicked" for x in r["results"].values())


def test_holding_your_nerve_to_the_cap_clears_the_vault(em):
    rid = crew(em, stake=100, target="society")
    to_vault(em, rid, police_take=None)
    r = H._rooms[rid]
    tick(em, r["vault"]["secs"] + 1)
    H.sweep()
    assert r["over"] and all(x["how"] == "cleared" for x in r["results"].values())
    assert r["results"][str(A)]["payout"] == int(100 * r["cap"])
    # and the clean job bonus on top (taxed like any reward, once a balance is over 10k)
    assert 0 < em.get_bb(A) - (9_900 + int(100 * r["cap"])) <= r["bonus"][str(A)]


def test_a_clean_job_pays_its_bonus_once_a_day(em):
    def job():
        rid = crew(em, stake=500)
        to_vault(em, rid, police_take=None)
        r = H._rooms[rid]
        tick(em, secs_to(r, 1.7))
        for u in (A, B, D):
            H.leg_it(u, rid)
        return r
    r = job()
    assert r["how"] == "clean" and r["bonus"] == {str(A): 50, str(B): 50, str(D): 50}
    take = int(500 * r["results"][str(A)]["take"])
    assert 0 < em.get_bb(A) - (10_000 - 500 + take) <= 50     # the bonus, less the wealth tax over 10k
    r = job()
    assert r["how"] == "clean" and r["bonus"] == {}
    tick(em, 86_400)
    assert job()["bonus"]


def test_getting_out_too_early_isnt_a_clean_job(em):
    rid = crew(em, stake=100)
    to_vault(em, rid, police_take=None)
    r = H._rooms[rid]
    tick(em, secs_to(r, 1.2))
    for u in (A, B, D):
        H.leg_it(u, rid)
    assert r["over"] and r["how"] == "done" and not r["bonus"]


def test_a_free_job_moves_no_money(em):
    before = {u: em.get_bb(u) for u in (A, B, D)}
    rid = crew(em, stake=0)
    to_vault(em, rid, police_take=None)
    tick(em, 60)
    H.sweep()
    assert H._rooms[rid]["over"] and {u: em.get_bb(u) for u in (A, B, D)} == before


def test_someone_who_walks_off_stays_in_the_vault(em):
    rid = crew(em, stake=100)
    r = H._rooms[rid]
    H.leave(D, rid)
    to_vault(em, rid, police_take=2.5)
    tick(em, secs_to(r, 1.5))
    H.leg_it(A, rid)
    H.leg_it(B, rid)
    assert not r["over"]                              # Dot's still inside
    tick(em, 60)
    H.sweep()
    assert r["results"][str(D)]["how"] == "nicked"


def test_a_crew_that_cant_pay_doesnt_go_in(em):
    rid = H.open_room(A, 1000, "post")["id"]
    H.join(B, rid)
    em.remove_bb(B, 9_500, reason="spent")
    with pytest.raises(H.Refuse):
        H.start(A, rid)
    assert em.get_bb(A) == 10_000 and H._rooms[rid]["state"] == "lobby"


# ---- restarts -----------------------------------------------------------------------------------

def test_the_clocks_pause_while_the_bot_is_away(em):
    rid = crew(em, stake=100)
    to_vault(em, rid, police_take=4.0)
    r = H._rooms[rid]
    tick(em, 2)
    H.sweep()                                         # saved two seconds into the vault
    at = H.time.time() - r["vault"]["start"]
    tick(em, 600)                                     # the bot's away for ten minutes
    H._rooms.clear()
    H._loaded = False
    H._load()
    r = H._rooms[rid]
    assert not r["over"] and H.time.time() - r["vault"]["start"] == pytest.approx(at, abs=0.01)
    H.leg_it(A, rid)
    assert r["vault"]["out"][str(A)][1] == pytest.approx(H._floor2(H.take_at(at, r["cap"], r["vault"]["secs"])), abs=0.01)


# ---- the lobby ----------------------------------------------------------------------------------

def test_the_lobby_shows_targets_heat_and_open_crews(em):
    rid = H.open_room(A, 100, "society")["id"]
    lb = H.lobby(B, name_of, 10_000)
    assert [t["key"] for t in lb["targets"]] == list(H.TARGETS)
    assert lb["open"][0]["id"] == rid and lb["open"][0]["target"] == "Building Society"
    assert lb["heat"]["label"] == "Quiet" and lb["room"] is None
    assert sum(t["tip"] for t in lb["targets"]) == 1


def test_the_badges_go_to_the_right_people(em, monkeypatch):
    from lib.economy import activity_badges
    got = []
    monkeypatch.setattr(activity_badges, "_award", lambda uid, badge: got.append((str(uid), badge)))
    room = {"id": "x", "stake": 100, "target": "vault", "how": "clean", "score": 0.95,
            "results": {"1": {"how": "escaped", "take": 8.2, "payout": 820}, "2": {"how": "escaped", "take": 1.6, "payout": 160},
                        "3": {"how": "cleared", "take": 12.0, "payout": 1200}, "4": {"how": "escaped", "take": 2.0, "payout": 200}}}
    real_heist_over(room)
    assert ("1", "heist_greedy") in got and ("2", "heist_greedy") not in got and ("3", "heist_greedy") in got
    assert {u for u, b in got if b == "heist_clean"} == {"1", "2", "3", "4"}
    assert {u for u, b in got if b == "heist_mastermind"} == {"1", "2", "3", "4"}
    got.clear()
    real_heist_over({**room, "stake": 0})
    assert got == []                                     # nothing for a free job


def test_the_money_is_booked_as_the_bank_jobs():
    from lib.economy.bank_manager import BankManager
    from lib.economy.statement import _categorize
    for reason in ("Bank Job buy-in", "Bank Job take", "Bank Job refund", "Bank Job clean job bonus"):
        assert _categorize(reason)[0] == "Casino", reason
        amounts = BankManager._game_amounts(100, reason)
        assert amounts[-1] == 100 and sum(amounts) == 100, reason
