"""Bank Job: the crew and its roles, the targets' limits, every role's puzzle and how it's judged, the run-down
before each, the vault's maths (the bank keeps its edge however anyone plays), settling buy-ins and takes, the clean
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
    H._met.clear()
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


def moves(role, p):
    """The perfect moves at a puzzle, peeking at the answer the way only the bot can."""
    g = H.heist_games.get(role, p.get("game"))
    if g:
        return g.solve(p)
    if role == "hacker":
        return [{"code": p["secret"]}]
    if role == "safecracker":
        return [{"n": n} for n in p["secret"]]
    if role == "lookout":
        return [{"seq": p["seq"]}]
    if role == "wheelman":
        return [{"path": cheapest_path(p["costs"])}]
    if role == "face":
        return ([] if p["ready"] else [{"ready": True}]) + [{"answer": q["answer"]} for q in p["questions"]]
    return [{"load": best_load(p["bags"], p["cap"])}]


def here(rid):
    r = H._rooms[rid]
    return [th for i, th in H._things(r) if i == r["at"]]


def clear_room(em, rid, how="quiet"):
    """Walk into the room the crew's at and do everything that's someone's, perfectly (or loudly)."""
    r = H._rooms[rid]
    tick(em, H.MOVE_SECONDS + 0.01)
    for th in here(rid):
        if th["state"] == "ready":
            H.play(th["who"], rid, {"ob": th["id"], "take": how})
            if how == "quiet":
                H.play(th["who"], rid, {"ob": th["id"], "go": True})       # GOT IT, past the run-down
                for mv in moves(th["role"], th["p"]):
                    H.play(th["who"], rid, {"ob": th["id"], **mv})
    if r["state"] == "job" and r["at"] == r.get("at") and any(th["state"] == "terry" for th in here(rid)):
        tick(em, H.TERRY_AFTER[1] + 0.1)                # Terry's things: wait for him
        H.room(r["host"], rid, name_of)


def cheapest_path(costs):
    import heapq
    g = len(costs)
    start, end = (g - 1, 0), (0, g - 1)
    best, prev, todo = {start: 0}, {}, [(0, start)]
    while todo:
        d, cell = heapq.heappop(todo)
        if cell == end:
            break
        r, c = cell
        for n in ((r + 1, c), (r - 1, c), (r, c + 1), (r, c - 1)):
            if 0 <= n[0] < g and 0 <= n[1] < g and costs[n[0]][n[1]] is not None:
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


def to_vault(em, rid, police_take=None, way=None):
    """Vote on the way in, go through every room doing what's yours perfectly, and open the vault with the police
    coming at ``police_take`` (None: never)."""
    r = H._rooms[rid]
    present = [u for u in r["players"] if u not in r["left"]]
    for u in present:
        H.vote(u, rid, way or next(iter(H.rooms_of.routes(r["target"]))))
    assert r["state"] == "job"
    while r["state"] == "job":
        clear_room(em, rid)
    assert r["state"] == "report"
    v = r["vault"]
    v["police"] = None if police_take is None else H.time_at(police_take, r["cap"], v["secs"])
    tick(em, H.REPORT_SECONDS)
    H.room(r["host"], rid, name_of)
    assert r["state"] == "vault"


def secs_to(r, take):
    return H.time_at(take, r["cap"], r["vault"]["secs"])


# ---- the puzzles ------------------------------------------------------------------------------

def test_the_keypad_says_how_close_a_guess_is():
    assert H._feedback("1234", "1234") == (4, 0)
    assert H._feedback("1234", "4321") == (0, 4)
    assert H._feedback("1122", "1212") == (2, 2)
    assert H._feedback("1111", "1222") == (1, 0)


def test_every_puzzle_scores_one_when_played_perfectly_and_nothing_when_not_played():
    rng = random.Random(4)
    story = H._story(rng)
    made = lambda role, size: H._make(role, size, rng, story) if size else H.MAKERS[role](rng)
    for _ in range(10):
        for role in H.MAKERS:
            for size in (None, "s", "m", "big", "loot"):
                p = made(role, size)
                assert H._score(role, p) == 0.0 and not H._done(role, p)
    for seed in range(15):
        rng = random.Random(seed)
        for role in H.MAKERS:
            for size in (None, "s", "m", "big"):
                p = made(role, size)
                for mv in moves(role, p):
                    H._move(role, p, mv)
                assert H._done(role, p) and H._score(role, p) == pytest.approx(1.0), (role, size, seed)


def test_the_safe_says_which_way_and_how_warm():
    p = {"secret": [50, 10, 90], "dials": [], "open": 0}
    H._move("safecracker", p, {"n": 20})
    assert p["dials"][-1]["said"] == "cold" and p["dials"][-1]["way"] == 1
    H._move("safecracker", p, {"n": 45})
    assert p["dials"][-1]["said"] == "hot"
    H._move("safecracker", p, {"n": 52})
    assert p["dials"][-1]["said"] == "hot" and p["open"] == 0          # two off: close, but it only clicks within one
    H._move("safecracker", p, {"n": 51})
    assert p["dials"][-1]["said"] == "click" and p["open"] == 1
    with pytest.raises(H.Refuse):
        H._move("safecracker", p, {"n": 100})


def test_a_route_has_to_join_up_and_a_van_cant_be_overloaded():
    costs = [[1] * H.GRID for _ in range(H.GRID)]
    costs[H.GRID - 2][0] = None
    assert H._route_cost(costs, [[H.GRID - 1, 0], [H.GRID - 2, 0]]) is None                     # into a roadblock
    assert H._route_cost(costs, [[H.GRID - 1, 0], [H.GRID - 1, 2]]) is None                     # a jump
    g = H.GRID - 1
    straight = [[g, c] for c in range(H.GRID)] + [[r, g] for r in range(g - 1, -1, -1)]
    assert H._route_cost(costs, straight) == 2 * g == H._cheapest(costs)
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
        H.vote(u, rid, "front")
    tick(em, H.MOVE_SECONDS + 0.01)
    for th in here(rid):
        if th["state"] == "ready":
            H.play(th["who"], rid, {"ob": th["id"], "take": "quiet"})
    for u in r["players"]:                    # in the run-down: not even the puzzle's public part yet
        p = H.view(r, u, name_of)["puzzle"]
        assert p["briefing"]["how"] and "g" not in p and "story" not in p and "len" not in p
    for th in here(rid):
        if th["state"] == "active":
            H.play(th["who"], rid, {"ob": th["id"], "go": True})
    for u in r["players"]:
        text = json.dumps(H.view(r, u, name_of))
        assert '"secret"' not in text and '"best"' not in text and '"police"' not in text and '"answer"' not in text
        assert '"terry":' not in text and '"p":' not in text


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
    assert H.take_at(15, 16.0, 30) == pytest.approx(2.0)                   # halfway in time, a quarter of the way in powers
    assert H.time_at(2.0, 16.0, 30) == pytest.approx(15) and H.time_at(1.0, 6.0, 30) == 0.0
    assert H.take_at(H.time_at(1.15, 8.0, 30), 8.0, 30) == pytest.approx(1.15)
    assert H.time_at(1.15, 8.0, 30) > 7                                      # the first few tenths take a while
    assert H.take_at(30, 6.0, 30) == pytest.approx(6.0) == H.take_at(99, 6.0, 30)


# ---- the rooms ----------------------------------------------------------------------------------

def test_no_role_plays_the_same_game_twice_in_a_heist(em):
    rid = crew(em, players=(A, B, D), stake=0, target="vault")
    r = H._rooms[rid]
    for u in (A, B, D):
        H.vote(u, rid, "roof")
    played: dict[str, list[str]] = {}
    for _, th in H._things(r):
        played.setdefault(th["role"], []).append(th["p"].get("game") or H.heist_games.FIRST[th["role"]])
    for role, games in played.items():
        n = len(H.heist_games.keys(role))
        for at in range(0, len(games), n):          # only once it's played them all does one come round again
            assert len(set(games[at:at + n])) == len(games[at:at + n])
    deal = H.heist_games.deal(random.Random(3), ["hacker"] * 20)
    assert set(deal) == set(H.heist_games.keys("hacker"))


def test_the_way_in_is_the_crews_vote_and_the_host_breaks_a_tie(em):
    rid = crew(em, players=(A, B, D), stake=0, target="society")
    r = H._rooms[rid]
    with pytest.raises(H.Refuse, match="way in"):
        H.vote(A, rid, "quiet")
    H.vote(A, rid, "roof")
    H.vote(B, rid, "drains")
    H.vote(D, rid, "drains")
    assert r["state"] == "job" and r["approach"] == "drains"
    assert [x["key"] for x in r["rooms"]] == H.rooms_of.routes("society")["drains"]["rooms"]
    r["over"] = True
    rid = crew(em, players=(A, B), stake=0, target="society")
    H.vote(A, rid, "roof")
    H.vote(B, rid, "front")
    assert H._rooms[rid]["approach"] == "roof"                    # a tie: the host's way


def test_a_room_goes_to_whoever_has_the_job_and_terry_does_the_rest(em):
    rid = crew(em, players=(A, B), stake=0, target="post")       # Anne the Hacker, Ben the Safecracker
    r = H._rooms[rid]
    for u in (A, B):
        H.vote(u, rid, "front")
    things = here(rid)                                            # the shop: the bell, Maureen, the till
    assert {th["role"]: th["state"] for th in things if not th["spare"]} == {"lookout": "terry", "face": "terry", "muscle": "terry"}
    # and a spare job each for Anne and Ben, whose roles the shop hasn't got
    assert {th["role"]: (th["state"], th["who"]) for th in things if th["spare"]} == {"hacker": ("ready", A), "safecracker": ("ready", B)}
    with pytest.raises(H.Refuse, match="not in the room"):
        H.play(A, rid, {"ob": things[0]["id"], "step": True})
    tick(em, H.MOVE_SECONDS + 0.01)
    mine = next(th for th in things if th["spare"] and th["who"] == A)
    with pytest.raises(H.Refuse, match="Finish"):
        H.play(A, rid, {"ob": things[1]["id"], "step": True})   # her own spare first
    H.play(A, rid, {"ob": mine["id"], "take": "loud"})
    H.play(A, rid, {"ob": things[1]["id"], "step": True})       # then Anne steps in for Maureen
    with pytest.raises(H.Refuse, match="Finish"):
        H.play(A, rid, {"ob": things[2]["id"], "step": True})
    th = things[1]
    H.play(A, rid, {"ob": th["id"], "take": "quiet"})
    H.play(A, rid, {"ob": th["id"], "go": True})
    for mv in moves("face", th["p"]):
        H.play(A, rid, {"ob": th["id"], **mv})
    assert th["state"] == "done" and th["score"] == pytest.approx(H.STEP_IN)    # not her job: a bit worse
    ben = next(th for th in things if th["spare"] and th["who"] == B)
    H.play(B, rid, {"ob": ben["id"], "take": "loud"})
    tick(em, H.TERRY_AFTER[1] + 0.1)
    H.room(A, rid, name_of)
    assert r["at"] == 1                                           # Terry's done his bits: on to the office
    assert all(th["state"] == "done" and th["score"] == th["terry"] for th in r["rooms"][0]["things"]
               if th is not things[1] and not th["spare"])


def test_everyone_in_the_crew_has_something_to_do_in_every_room(em):
    rid = crew(em, players=(A, B, D), stake=0, target="vault")   # a Hacker, a Safecracker and a Lookout
    r = H._rooms[rid]
    for u in (A, B, D):
        H.vote(u, rid, "roof")
    held = set(r["roles"].values())
    for rm in r["rooms"]:
        assert held <= {th["role"] for th in rm["things"]}
        assert all(th["role"] in held for th in rm["things"] if th["spare"])    # Terry never gets a spare
    # Terry's share of the alarm is what it'd be with no spares: the building's own things weigh 1 between them
    assert sum(th["w"] for _, th in H._things(r) if not th["spare"]) == pytest.approx(1.0, abs=1e-3)
    assert sum(th["w"] for _, th in H._things(r) if th["spare"]) > 0
    # the spares don't make a room's clock any longer
    office = r["rooms"][1]
    assert H._room_secs(office) == H.rooms_of.room_seconds([t for t in H.rooms_of.ROOMS[office["key"]][1]]) + office.get("brief", 0)


def test_going_in_loud_is_quick_but_the_alarm_jumps(em):
    rid = crew(em, players=(A, B, D), stake=0, target="post")
    for u in (A, B, D):
        H.vote(u, rid, "yard")
    r = H._rooms[rid]
    clear_room(em, rid, how="quiet")
    quiet = r["alarm"]
    clear_room(em, rid, how="loud")
    loud = r["alarm"] - quiet
    office = r["rooms"][1]["things"]
    mine = [th for th in office if th.get("mode") == "loud"]
    assert mine and loud > 0 and all(th["score"] == H.LOUD_SCORE for th in mine)


def test_every_game_has_its_run_down():
    for role in H.ROLES:
        for key in H.heist_games.keys(role):
            how, line = H.briefs.BRIEFS[f"{role}:{key}"]
            assert how and line, (role, key)


def test_a_puzzles_clock_waits_for_its_run_down(em):
    rid = crew(em, players=(A, B), stake=0, target="post")
    r = H._rooms[rid]
    for u in (A, B):
        H.vote(u, rid, "front")
    rm = r["rooms"][0]
    mine = next(th for th in rm["things"] if th["who"] == A)
    # nobody's met a game yet: the full run-down, and the room's clock grows to fit it
    assert rm["brief"] == H.BRIEF_FULL
    assert r["deadline"] - r["arrive"] == pytest.approx(H.rooms_of.room_seconds(
        [(t["role"], t["name"], t["size"]) for t in rm["things"] if not t["spare"]]) + H.BRIEF_FULL)
    tick(em, H.MOVE_SECONDS + 0.5)
    H.play(A, rid, {"ob": mine["id"], "take": "quiet"})
    assert mine["state"] == "active" and mine["brief"] == "full" and mine["go"] == pytest.approx(H.time.time() + H.BRIEF_FULL)
    with pytest.raises(H.Refuse, match="run-down"):
        H.play(A, rid, {"ob": mine["id"], **moves(mine["role"], mine["p"])[0]})
    tick(em, 3)
    b = H.view(r, A, name_of)["puzzle"]["briefing"]
    assert b["full"] and b["how"] and b["line"] and b["left"] == pytest.approx(H.BRIEF_FULL - 3)
    H.play(A, rid, {"ob": mine["id"], "go": True})                  # GOT IT: the clock starts now
    assert "briefing" not in H.view(r, A, name_of)["puzzle"]
    for mv in moves(mine["role"], mine["p"]):
        H.play(A, rid, {"ob": mine["id"], **mv})
    assert mine["state"] == "done" and mine["score"] == pytest.approx(1.0)
    # the next time Anne meets that game it's the quick one; a month on, the full one again
    th = {"role": mine["role"], "p": mine["p"]}
    assert H._brief_secs(A, th, H.time.time()) == H.BRIEF_SHORT
    assert H._brief_secs(A, th, H.time.time() + H.BRIEF_FORGET + 1) == H.BRIEF_FULL
    assert H._brief_secs(B, th, H.time.time()) == H.BRIEF_FULL     # Ben's not met it


def test_the_guard_wont_wait_on_the_cover_story_or_the_staff_names():
    p = H._face(random.Random(2))
    H._move("face", p, {"ready": True})
    H._move("face", p, {"slow": True})                              # too slow: wrong
    assert p["answers"] == [-1] and H._public("face", p)["patience"] == H.FACE_WAIT
    g = H.heist_games.get("face", "names")
    q = g.make(random.Random(3), "m", {})
    g.move(q, {"ready": True})
    assert g.public(q)["patience"] > 0
    g.move(q, {"slow": True, "round": 0})
    assert q["picks"] == [None] and g.score(q) == 0.0


def test_a_blown_alarm_sends_them_straight_to_the_vault(em):
    rid = crew(em, players=(A,), stake=0, target="post")
    H.vote(A, rid, "front")
    r = H._rooms[rid]
    r["alarm"] = H.ALARM_BLOWN - 0.01
    tick(em, 200)                                                 # nobody does anything
    H.room(A, rid, name_of)
    assert r["state"] in ("report", "vault") and r.get("blown") is not None and r["alarm"] >= H.ALARM_BLOWN
    assert all(th["state"] == "done" for _, th in H._things(r))


def test_loot_makes_the_vault_hold_more(em):
    rid = crew(em, players=(A, B, D), stake=0, target="post")
    r = H._rooms[rid]
    base = r["cap"]
    r["roles"][str(D)] = "muscle"                                 # Dot loads the tills
    to_vault(em, rid, police_take=None, way="front")
    loots = sum(1 for _, th in H._things(r) if th["size"] == "loot")   # the till, a spare in the office, the bags
    assert loots == 3 and r["loot"] == pytest.approx(H.LOOT_MAX) and r["cap"] == pytest.approx(round(base * (1 + H.LOOT_MAX), 2))


def test_the_alarm_is_the_job_score_and_sets_the_edge(em):
    rid = crew(em, players=(A, B, D), stake=0, target="post")
    to_vault(em, rid, police_take=None)
    r = H._rooms[rid]
    assert r["score"] == pytest.approx(1 - r["alarm"], abs=1e-3)
    assert r["edge"] == pytest.approx(H.edge(r["score"]), abs=1e-4)
    roles = {x["role"]: x for x in H.view(r, A, name_of)["report"]["roles"]}
    assert roles["hacker"]["uid"] == str(A) and roles["muscle"]["dave"]


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
    import config
    import lib.economy.reserve_policy as rp
    floor = config.RESERVE_FLOOR                                # what's spare is what's above the reserve floor
    monkeypatch.setattr(rp, "bank_reserves", lambda: floor + 1_000_000)
    assert H.max_stake("post") == 1000 and H.max_stake("vault") == 500
    monkeypatch.setattr(rp, "bank_reserves", lambda: floor + 80_000)    # 80k spare: 12k of exposure, loot and all
    assert H.max_stake("vault") == 50 and H.max_stake("post") == 250
    monkeypatch.setattr(config, "RESERVE_FLOOR", 80_000)                # the floor moves, the limits move with it
    monkeypatch.setattr(rp, "bank_reserves", lambda: 160_000)
    assert H.max_stake("vault") == 50 and H.max_stake("post") == 250
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

def test_a_rooms_clocks_pause_while_the_bot_is_away(em):
    rid = crew(em, players=(A, B), stake=0, target="society")
    for u in (A, B):
        H.vote(u, rid, "roof")
    r = H._rooms[rid]
    tick(em, 3)
    H.sweep()
    left = r["deadline"] - H.time.time()
    tick(em, 900)
    H._rooms.clear()
    H._loaded = False
    H._load()
    r = H._rooms[rid]
    assert r["state"] == "job" and r["at"] == 0 and r["deadline"] - H.time.time() == pytest.approx(left, abs=0.01)


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
    assert lb["open"][0]["id"] == rid and lb["open"][0]["target"] == "High Street Branch"
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


def test_the_wheelmans_town_punishes_the_obvious_way():
    rng = random.Random(7)
    towns = [H._wheelman(rng) for _ in range(200)]
    assert all(H._cheapest(t["costs"]) == t["best"] for t in towns)
    trap = [t for t in towns if (H._straight_on(t["costs"]) or 999) >= t["best"] + 2]
    assert len(trap) >= 195                        # the odd town falls back to any with a way through
    flat = [[1] * H.GRID for _ in range(H.GRID)]
    assert H._straight_on(flat) == H._cheapest(flat) == 2 * (H.GRID - 1)


# ---- practice -----------------------------------------------------------------------------------

@pytest.fixture
def testers(monkeypatch):
    import config
    monkeypatch.setattr(config, "HEIST_TESTERS", [A])
    monkeypatch.setattr(config, "HEIST_LIVE", False)


def practice(em, stake=100, target="post", role="hacker", skill=None):
    """A tester's practice job, sent in: the bots are in, and (if ``skill``) all that good."""
    rid = H.open_room(A, stake, target, role=role, practice=True)["id"]
    H.start(A, rid)
    r = H._rooms[rid]
    if skill is not None:
        for b in r["bots"].values():
            b["skill"] = skill
    return rid, r


def practice_to_vault(em, rid, police_take=None, way=None):
    """Your vote makes the plan; then every room, you do your things and the bots do theirs; then the vault opens."""
    r = H._rooms[rid]
    H.vote(A, rid, way or next(iter(H.rooms_of.routes(r["target"]))))
    assert r["state"] == "job"                        # the bots back the plan, so it's made once you've voted
    while r["state"] == "job":
        at = r["at"]
        clear_room(em, rid)
        if r["state"] == "job" and r["at"] == at:
            # yours are done; the bots are still on theirs, then they aren't
            assert any(th.get("bot") is not None and th["state"] == "active" for th in here(rid))
            tick(em, H._room_secs(r["rooms"][at]))
            H.room(A, rid, name_of)
    assert r["state"] == "report"
    v = r["vault"]
    v["police"] = None if police_take is None else H.time_at(police_take, r["cap"], v["secs"])
    tick(em, H.REPORT_SECONDS)
    H.room(A, rid, name_of)
    assert r["state"] == "vault"
    return r


def test_a_tester_on_their_own_gets_a_crew_of_bots_for_practice(em, testers):
    rid, r = practice(em, 500, "vault", role="safecracker", skill=0.95)   # the Vault needs three, 500 needs two
    bots = [u for u in r["players"] if u != A]
    assert len(r["players"]) == H.SEATS and all(u < 0 for u in bots)
    assert sorted(r["roles"].values()) == sorted(H.ROLES) and r["roles"][str(A)] == "safecracker"
    v = H.view(r, A, name_of)
    assert v["practice"] and v["crew"][0]["name"] == "Anne" and not v["crew"][0].get("bot")
    assert all(m["bot"] and m["name"] in H.BOT_NAMES and m["voted"] for m in v["crew"][1:])
    practice_to_vault(em, rid)
    rep = H.view(r, A, name_of)["report"]
    assert not any(x["dave"] for x in rep["roles"])   # no Terry: every job's taken
    theirs = [th for _, th in H._things(r) if th.get("bot") is not None]
    assert theirs and all(th["who"] < 0 and th["state"] == "done" and th["score"] == th["bot"] for th in theirs)


def test_the_bots_back_the_way_in_you_pick(em, testers):
    rid, r = practice(em, 0, "society")
    way = list(H.rooms_of.routes("society"))[-1]
    assert r["state"] == "plan" and H.view(r, A, name_of)["votes"][way] == 0
    H.vote(A, rid, way)
    assert r["state"] == "job" and r["approach"] == way


def test_a_bot_finishes_its_things_in_a_room_at_a_moment_of_its_own(em, testers):
    rid, r = practice(em, 0, "post", skill=0.6)
    H.vote(A, rid, next(iter(H.rooms_of.routes("post"))))
    rm = r["rooms"][0]
    theirs = [th for th in rm["things"] if th["who"] is not None and th["who"] < 0]
    secs = H._room_secs(rm) - rm["brief"]
    assert theirs and all(th["state"] == "active" for th in theirs)
    assert all(r["arrive"] + H.BOT_PACE[0] * secs <= th["at"] <= r["arrive"] + H.BOT_PACE[1] * secs + 0.01 for th in theirs)
    first = min(theirs, key=lambda th: th["at"])
    tick(em, first["at"] - em.clock[0] + 0.01)
    H.room(A, rid, name_of)
    assert first["state"] == "done" and first["score"] == first["bot"]


def test_bot_crewmates_leg_it_from_the_vault_on_their_own(em, testers):
    rid, r = practice(em, 100, "post", skill=0.95)
    for b, f in zip(r["bots"].values(), [0.2, 0.5, 0.8, 1.0, 0.95]):
        b["leave"] = f
    practice_to_vault(em, rid)
    v = r["vault"]
    tick(em, 0.6 * v["secs"])
    H.sweep()
    out = v["out"]
    assert {u for u, b in r["bots"].items() if b["leave"] <= 0.5} == set(out)
    for u in out:
        assert out[u][0] == pytest.approx(r["bots"][u]["leave"] * v["secs"], abs=0.01)
    H.leg_it(A, rid)
    tick(em, v["secs"])
    H.sweep()
    assert r["over"]
    stayed = [u for u, b in r["bots"].items() if b["leave"] >= 1.0]
    assert stayed and all(r["results"][u]["how"] == "cleared" for u in stayed)
    assert all(r["results"][u]["how"] == "escaped" for u in r["bots"] if u not in stayed)


def test_a_bot_that_waits_too_long_is_nicked(em, testers):
    rid, r = practice(em, 0, "post", skill=0.95)
    for b, f in zip(r["bots"].values(), [0.05, 0.9, 0.9, 0.9, 0.9]):
        b["leave"] = f
    practice_to_vault(em, rid, police_take=1.5)
    tick(em, r["vault"]["secs"])
    H.sweep()
    assert r["over"]
    res = [r["results"][u]["how"] for u in r["bots"]]
    assert res.count("escaped") == 1 and res.count("nicked") == 4
    assert r["results"][str(A)]["how"] == "nicked"


def test_practice_moves_no_money_and_is_posted_and_kept_nowhere(em, testers, monkeypatch):
    from lib.activities import archive
    from lib.economy import activity_badges, casino_stats
    seen = []
    monkeypatch.setattr(archive, "keep", lambda *a, **k: seen.append("archive"))
    monkeypatch.setattr(casino_stats, "record_result", lambda *a, **k: seen.append("stats"))
    monkeypatch.setattr(activity_badges, "heist_over", lambda room: seen.append("badges"))
    monkeypatch.setattr(H, "listeners", [lambda event, room: seen.append(event)])
    assert em.remove_bb(A, 10_000, reason="spent")    # skint: play money doesn't care
    before, total = em.get_bb(A), supply()
    rid, r = practice(em, 1000, "post", skill=0.95)
    for b in r["bots"].values():
        b["leave"] = 0.8
    practice_to_vault(em, rid)
    tick(em, 0.85 * r["vault"]["secs"])
    H.leg_it(A, rid)
    assert r["over"] and r["how"] == "clean"
    assert r["results"][str(A)]["payout"] > 1000      # what it would have been, in play money
    assert em.get_bb(A) == before and supply() == total
    assert r["bonus"] == {} and H.heat() == 0.0 and seen == []
    assert H.active() == 0                            # a practice job doesn't hold up a restart


def test_practice_crews_are_hidden_and_closed_to_others(em, testers):
    rid = H.open_room(A, 100, "society", practice=True)["id"]
    assert H.lobby(B, name_of, 10_000)["open"] == []
    with pytest.raises(H.Refuse, match="practice"):
        H.join(B, rid)
    assert H.lobby(A, name_of, 0)["room"]["practice"]
    assert H._rooms[rid]["ch"] is None


def test_only_testers_can_practise(em, testers):
    assert H.lobby(A, name_of, 0)["practice"] and not H.lobby(B, name_of, 0)["practice"]
    with pytest.raises(H.Refuse, match="testers"):
        H.open_room(B, 0, "post", practice=True)
    assert H._rooms == {}


def test_heist_opens_only_for_testers_until_its_live(testers, monkeypatch):
    import config
    from lib.activities import launcher
    assert launcher._not_open_to(A, "heist") is None
    assert "isn't open yet" in launcher._not_open_to(B, "heist")
    assert "isn't open yet" in launcher._not_open_to(B, "heist:abcd1234")
    assert launcher._not_open_to(B, "kart") is None
    monkeypatch.setattr(config, "HEIST_LIVE", True)
    assert launcher._not_open_to(B, "heist") is None
    assert not H.can_practise(B)                      # practice stays with the testers when it's live


def test_heist_has_its_command_and_its_testers():
    import config
    assert config.ACTIVITIES_LAUNCH_COMMANDS["heist"][0] == "heist"
    assert {404634271861571584, 1453319508587450514} <= set(config.HEIST_TESTERS)


def test_an_old_one_puzzle_job_is_called_off_on_load(em, monkeypatch, tmp_path):
    old = {"id": "abcd1234", "host": A, "players": [A, -1], "left": [], "stake": 100, "target": "post", "state": "job",
           "roles": {str(A): "hacker", "-1": "safecracker"}, "votes": {}, "over": False, "practice": True,
           "job": {"hacker": {}, "safecracker": {}}, "deadline": 2_000_000.0}
    with open(H._FILE, "w") as f:
        json.dump({"rooms": {"abcd1234": old}, "saved": 999_990.0}, f)
    monkeypatch.setattr(H, "_loaded", False)
    H._load()
    r = H._rooms["abcd1234"]
    assert r["over"] and r["how"] == "closed"
    H.sweep()                                         # and nothing trips over it
    assert H.lobby(A, name_of, 0)["room"] is None
