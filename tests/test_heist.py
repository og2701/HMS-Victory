"""Bank Job: the crew and its roles, the targets' limits, every role's puzzle and how it's judged, the run-down
before each, quiet jobs and loud ones (the breach, loose cash), the vault's grab, safe time and scanner, its maths (the bank keeps its edge however anyone plays), settling buy-ins and takes, the clean
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


def clear_room(em, rid):
    """Walk into the room the crew's at and do everything that's a player's, perfectly (cutting the run-down short)."""
    r = H._rooms[rid]
    tick(em, H.MOVE_SECONDS + 0.01)
    for th in here(rid):
        if th["state"] == "active" and th.get("bot") is None:
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


def to_vault(em, rid, police_take=None, way=None, style="quiet"):
    """Vote on the way in, go through every room doing what's yours perfectly, and open the vault with the police
    coming at ``police_take`` (None: never)."""
    r = H._rooms[rid]
    present = [u for u in r["players"] if u not in r["left"]]
    for u in present:
        H.vote(u, rid, way or next(iter(H.rooms_of.routes(r["target"]))), style)
    assert r["state"] == "job"
    if style == "loud":
        loud_through(em, rid)
    while r["state"] == "job":
        clear_room(em, rid)
    assert r["state"] == "report"
    v = r["vault"]
    v["police"] = None if police_take is None else H.police_time(police_take, r["cap"], v["secs"], v["base"], v["safe"])
    tick(em, H.REPORT_SECONDS)
    H.room(r["host"], rid, name_of)
    assert r["state"] == "vault"


def loud_through(em, rid, loose=True):
    """A loud job's breach: everyone does every task they get, perfectly (loose cash too, if ``loose``), then waits."""
    r = H._rooms[rid]
    tick(em, H.MOVE_SECONDS + 0.01)
    for _ in range(200):
        if r["state"] != "job":
            return
        busy = False
        for u in [u for u in r["players"] if u not in r["left"] and u > 0]:
            th = H._current(r, u)
            if th is None or (th["mode"] == "loose" and not loose):
                continue
            busy = True
            H.play(u, rid, {"ob": th["id"], "go": True})
            for mv in moves(th["role"], th["p"]):
                if r["state"] == "job":
                    H.play(u, rid, {"ob": th["id"], **mv})
        if not busy:
            break
    tick(em, max(0.0, r.get("deadline", 0) - H.time.time()) + 0.01)
    H.room(r["host"], rid, name_of)


def secs_to(r, take):
    """Seconds into the vault until the piles reach ``take`` (past the safe time)."""
    v = r["vault"]
    return v["safe"] + H.time_at(take, r["cap"], v["secs"], v["base"])


def grab_to(em, rid, t, uids=(A, B, D), every=0.25):
    """Move on to ``t`` seconds into the vault, everyone in ``uids`` grabbing every ``every`` seconds."""
    r = H._rooms[rid]
    v = r["vault"]
    while em.clock[0] - v["start"] < t - 1e-9 and not r.get("over"):
        tick(em, min(every, t - (em.clock[0] - v["start"])))
        at = em.clock[0] - v["start"]
        for u in uids:
            if str(u) not in v["out"] and not r.get("over"):
                try:
                    H.grab(u, rid, [at])
                except H.Refuse:
                    pass


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
        H.vote(u, rid, "front", "quiet")
    tick(em, H.MOVE_SECONDS + 0.01)
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


def test_route_memorys_go_after_its_run_down_is_the_games_not_got_it(em):
    """The page's go at the end of Route Memory's showing reaches the game: it used to be taken for GOT IT, so the
    game never heard it, refused every turn (Watch the route first.) and the page sat on WATCH till time ran out."""
    rid = crew(em, stake=0)
    r = H._rooms[rid]
    for u in r["players"]:
        H.vote(u, rid, "front", "quiet")
    tick(em, H.MOVE_SECONDS + 0.01)
    th = next(t for t in here(rid) if t["state"] == "active")
    th.update(role="wheelman", p=H.heist_games.get("wheelman", "memory").make(random.Random(1), "m", None))
    th["p"]["game"] = "memory"
    H.play(th["who"], rid, {"ob": th["id"], "go": True, "brief": True})     # GOT IT
    assert not th["p"]["go"]
    tick(em, 6)                                                               # the showing
    H.play(th["who"], rid, {"ob": th["id"], "go": True, "brief": True})     # a late GOT IT: still not the game's
    assert not th["p"]["go"]
    H.play(th["who"], rid, {"ob": th["id"], "go": True})                    # the showing's over
    assert th["p"]["go"] and "route" not in H.view(r, th["who"], name_of)["puzzle"]["g"]
    H.play(th["who"], rid, {"ob": th["id"], "turns": th["p"]["route"][:2]})
    assert th["p"]["turns"] == th["p"]["route"][:2]


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


def _vault(e, cap=6.0, secs=28.0, safe=8.0):
    return {"start": 0.0, "secs": secs, "safe": safe, "base": 1 - e, "out": {}, "grab": {}}, cap


def _returns(e, cap, secs, safe, n, rng):
    """The police's moment for n jobs, and a function giving the mean payout of a strategy over them: leave at
    leave(i) seconds with the bag kept up ``keep`` of the way, plus ``loose`` of the buy-in paid on the way in."""
    v, cap = _vault(e, cap, secs, safe)
    end = v["safe"] + v["secs"]
    police = [H.police_time(H.police_take(e, rng.random() or 1e-12), cap, secs, v["base"], v["safe"]) for _ in range(n)]

    def rtp(leave, keep=1.0, loose=0.0):
        total = 0.0
        for i, p in enumerate(police):
            t = min(leave(i), end)
            if p is None or t < p:
                total += keep * H.pile_at(v, cap, t)
        return total / n + loose
    return v, cap, end, police, rtp


def test_the_vault_with_its_grab_safe_time_and_scanner_never_beats_the_edge():
    """The whole vault as it's played, simulated: the safe time (short or long), the climb, a bag grabbed well or
    badly, and every way of picking a moment to go (in the safe time, after it, at the cap, at random, or off the
    scanner). Nothing returns more than 1 - edge of the buy-in; a perfect grabber who goes after the safe time gets
    exactly that, and a slow one less."""
    rng = random.Random(11)
    n = 120_000
    for score, cap, secs, safe in ((0.0, 3.0, 26.0, H.SAFE_QUIET[0]), (0.6, 6.0, 28.0, 8.8),
                                   (1.0, 12.0 * 1.25 * 1.36, 32.0, H.SAFE_QUIET[1]), (1.0, 6.0, 28.0, H.LOUD_SAFE[2])):
        e = H.edge(score)
        best = 1 - e
        v, cap, end, police, rtp = _returns(e, cap, secs, safe, n, rng)
        assert all(p is None or p >= v["safe"] for p in police)          # never in the safe time
        for t in (1.0, v["safe"] / 2, v["safe"] - 0.01):                 # going in the safe time: sure, but below 1 - e
            got = rtp(lambda i, t=t: t)
            assert got == pytest.approx(best * t / v["safe"], abs=1e-9) and got < best
        for t in (v["safe"], v["safe"] + 0.3 * secs, v["safe"] + 0.7 * secs, end):      # after it: 1 - e, at most
            assert rtp(lambda i, t=t: t) == pytest.approx(best, abs=0.012), (score, t)
        for keep in (0.9, 0.6):                                           # a slow grabber: less
            assert rtp(lambda i: v["safe"] + 0.5 * secs, keep) == pytest.approx(keep * best, abs=0.012)
        jitter = [rng.uniform(0, end) for _ in range(n)]                # going at a random moment
        assert rtp(lambda i: jitter[i]) <= best + 0.01
        for h in (0.02, 0.05, 0.1, 0.2):                                 # off the scanner: go when the risk gets to h
            t = next((x / 20 for x in range(int(end * 20) + 1) if H.hazard(v, cap, x / 20) >= h), end)
            assert rtp(lambda i, t=t: t) <= best + 0.012, (score, h)


def test_a_loud_job_with_all_its_loose_cash_never_beats_a_perfect_quiet_one():
    """Loud: the vault at the loud edge, plus the most loose cash anyone can pick up, however they play the vault.
    The best of it is a perfect quiet job's 1 - EDGE_BEST, and nothing beats 1 - loud edge + LOOSE_MAX."""
    rng = random.Random(12)
    for score in (0.0, 0.5, 1.0):
        e = H.loud_edge(score)
        v, cap, end, police, rtp = _returns(e, 6.0, 28.0, H.LOUD_SAFE[2], 100_000, rng)
        for t in (v["safe"], v["safe"] + 10, end):
            got = rtp(lambda i, t=t: t, loose=H.LOOSE_MAX)
            assert got <= 1 - e + H.LOOSE_MAX + 0.012 and got <= 1 - H.EDGE_BEST + 0.012, (score, t)


def test_a_bag_is_never_worth_more_than_the_piles(em):
    """However the grabs come in (all of them, a few, late, out of order, from the future), the bag never passes what
    the piles hold, and grabbing every GRAB_WINDOW or quicker keeps it right up with them."""
    rng = random.Random(5)
    for trial in range(60):
        e = rng.uniform(H.EDGE_BEST, H.EDGE_WORST)
        v, cap = _vault(e, rng.choice([3.0, 6.0, 12.0]), 28.0, rng.uniform(4, 15))
        v["police"] = None
        room = {"vault": v, "cap": cap, "left": [], "bots": {}}
        every = rng.choice([0.1, 0.25, 0.3, 0.45, 1.0, 2.5])
        t = 0.0
        while t < v["safe"] + v["secs"]:
            t += every
            taps = [t] if rng.random() < 0.9 else [t + 5, t - 3, t - 0.1, t]      # some junk among them
            H._grab(room, A, taps, t)
            assert H.bag(room, A, t) <= H.pile_at(v, cap, t) + 1e-9
        if every <= H.GRAB_WINDOW:
            assert H.bag(room, A, t) == pytest.approx(H.pile_at(v, cap, t), rel=0.02)


def test_grabs_quicker_than_one_finger_dont_count():
    """Two hands at it (the space bar and the mouse together) or a doctored page sending a burst: a grab read twice
    (within pace.MIN_GAP of the last one) isn't one, and a flood is held to one finger's 12 a second, so it can't
    count towards a gold bar or move the bag on past what one quick finger's grabs would."""
    from lib.activities.heist_games import pace
    v, cap = _vault(H.EDGE_BEST, 6.0, 28.0, 8.0)
    v["police"] = None
    room = {"vault": v, "cap": cap, "left": [], "bots": {}}
    H._grab(room, A, [1.0, 1.0, 1.01, 1.03, 1.06, 1.069, 1.07, 1.1, 1.13, 1.2], 1.2)
    g = room["vault"]["grab"][str(A)]
    assert g["recent"] == [1.0, 1.06, 1.1, 1.2] and g["last"] == 1.2 and g["n"] == 4
    assert g["b"] == pytest.approx(1.2 + H.GRAB_WINDOW)
    # a run of grabs at one finger's pace all count
    room = {"vault": dict(v, grab={}), "cap": cap, "left": [], "bots": {}}
    taps = [round(2.0 + i * 0.08, 3) for i in range(12)]
    H._grab(room, A, taps, taps[-1])
    assert room["vault"]["grab"][str(A)]["recent"] == taps[-8:] and room["vault"]["grab"][str(A)]["n"] == 12
    # the same burst sent twice over (two inputs at once) makes no more of the bag than sent once
    once, twice = ({"vault": dict(v, grab={}), "cap": cap, "left": [], "bots": {}} for _ in range(2))
    H._grab(once, A, taps, taps[-1])
    H._grab(twice, A, sorted(taps + [x + 0.01 for x in taps]), taps[-1] + 0.01)
    assert H.bag(twice, A, 5.0) == pytest.approx(H.bag(once, A, 5.0))
    assert twice["vault"]["grab"][str(A)]["recent"] == once["vault"]["grab"][str(A)]["recent"]
    # a flood of 50 a second for two seconds: no more than 12 a second and the few in hand
    room = {"vault": dict(v, grab={}), "cap": cap, "left": [], "bots": {}}
    flood = [round(3.0 + i * 0.02, 3) for i in range(100)]
    H._grab(room, A, flood, flood[-1])
    assert room["vault"]["grab"][str(A)]["n"] <= 2 * pace.RATE + pace.BURST


def test_a_real_fingers_grabs_all_count():
    """A finger grabbing 6 to 8 times a second, its grabs wobbling (the page's clock nudged to the bot's, the
    network) and some stamped a little ahead of the bot's clock: every one counts."""
    v, cap = _vault(H.EDGE_BEST, 6.0, 28.0, 8.0)
    v["police"] = None
    rng = random.Random(9)
    for rate in (6, 7, 8):
        room = {"vault": dict(v, grab={}), "cap": cap, "left": [], "bots": {}}
        t, taps = 1.0, []
        while t < 12.0:
            taps.append(round(t + rng.uniform(-0.04, 0.04), 3))
            t += 1 / rate
        taps = sorted(taps)
        for k in range(0, len(taps), 3):
            lot = taps[k:k + 3]
            H._grab(room, A, lot, lot[-1] - 0.2)                         # the bot's clock 200ms behind the page's
        assert room["vault"]["grab"][str(A)]["n"] == len(taps), rate


def _bar_run(style, rate, seed, lazy=0.0):
    """A vault played by one way of going at the gold bars: "catch" taps each bar that shows, "never" stops grabbing
    for it but leaves it, "spam" just keeps hammering GRAB. ``rate`` grabs a second, skipping ``lazy`` of the time.
    Returns the room, and the most the bag ever came to against the piles."""
    rng = random.Random(seed)
    v, cap = _vault(rng.uniform(H.EDGE_BEST, H.EDGE_WORST), rng.choice([3.0, 6.0, 12.0]), 28.0, rng.uniform(4, 15))
    v.update(police=None, bar={})
    v["bars"] = H._bar_plan(v, rng)
    room = {"vault": v, "cap": cap, "left": [], "bots": {}}
    t, worst = 0.0, -1.0
    while t < v["safe"] + v["secs"]:
        t += 1.0 / rate
        H._bars(room, A, t)
        on = H._bar_now(room, A, t)
        if on is not None and style != "spam":
            if style == "catch" and on[2] == "live" and t >= on[1]["t"] + H.BAR_DELAY + 0.5:
                H._catch(room, A, on[0], t, t)
        elif rng.random() >= lazy:
            H._grab(room, A, [t], t)
        worst = max(worst, H.bag(room, A, t) - H.pile_at(v, cap, t))
    return room, worst


def test_gold_bars_top_a_bag_up_but_never_past_the_piles():
    """Gold bars are for anyone mashing GRAB faster than it can use: catch one and the bag is topped up towards the
    piles and held there a few seconds; keep hammering GRAB and it's fumbled. However they're played, the bag is
    never worth more than the piles, so nothing about them beats the edge."""
    states = lambda room: list(room["vault"]["bar"][str(A)].values())
    for seed in range(40):
        got = {}
        for style in ("catch", "never", "spam"):
            room, worst = _bar_run(style, 6.0, seed, lazy=0.35)             # mashing, but with gaps: a bag that falls behind
            assert worst <= 1e-6, (style, seed)
            got[style] = (room, states(room))
        assert got["catch"][1].count("caught") >= 1 and "fumbled" not in got["catch"][1]
        assert "caught" not in got["never"][1] and "caught" not in got["spam"][1]
        assert "fumbled" in got["spam"][1]                                  # mindless hammering drops them
        end = lambda room: H.bag(room, A, 99)
        assert end(got["catch"][0]) >= end(got["never"][0]) - 1e-9          # a caught bar only ever helps
        # a steady two a second (all GRAB can use) never sees a bar, and keeps right up with the piles without one
        room, worst = _bar_run("catch", 2.2, seed)
        assert set(states(room)) <= {"none"} and worst <= 1e-6
        v = room["vault"]
        assert end(room) == pytest.approx(H.pile_at(v, room["cap"], 99), rel=0.03)
    # a bar can't be caught unless it's showing, live and theirs: the page can't grant itself one
    room, _ = _bar_run("never", 6.0, 1)
    v = room["vault"]
    before = H.bag(room, A, 20.0)
    H._catch(room, A, 0, 20.0, 20.0)
    H._catch(room, A, 99, 20.0, 20.0)
    H._catch(room, A, True, 20.0, 20.0)
    assert H.bag(room, A, 20.0) == before and "caught" not in states(room)


def test_the_page_only_hears_of_a_gold_bar_while_its_showing(em):
    rid = crew(em, stake=0)
    to_vault(em, rid, police_take=None)
    r = H._rooms[rid]
    v = r["vault"]
    assert v["bars"]
    first = v["bars"][0]
    grab_to(em, rid, first["t"] - 0.2, uids=(A,))
    seen = H.room(A, rid, name_of)["vault"]
    assert "bar" not in seen and "bars" not in seen                    # not yet, and never the plan
    grab_to(em, rid, first["t"] + H.BAR_DELAY + 0.2, uids=(A,))        # Anne's mashing (eight a second): it's hers
    bar = H.room(A, rid, name_of)["vault"]["bar"]
    assert bar["k"] == 0 and bar["state"] in ("live", "fumbled") and 0 < bar["left"] <= H.BAR_WINDOW
    assert "bar" not in H.room(B, rid, name_of)["vault"]               # Ben wasn't grabbing: none for him
    text = json.dumps(H.room(A, rid, name_of))
    assert '"bars": [' not in text and '"police"' not in text


def test_the_scanner_is_the_honest_hazard():
    """What the scanner shows is the real chance the police come in the next second: no more, no less, and the same
    whatever this job's police draw is (it isn't in it)."""
    rng = random.Random(3)
    e = H.edge(0.5)
    v, cap = _vault(e, 6.0, 28.0)
    police = [H.police_time(H.police_take(e, rng.random() or 1e-12), cap, 28.0, v["base"], v["safe"]) for _ in range(200_000)]
    assert H.hazard(v, cap, 2.0) == 0.0                                   # the safe time
    for t in (v["safe"], 14.0, 22.0, 30.0):
        alive = [p for p in police if p is None or p > t]
        came = sum(1 for p in alive if p is not None and p <= t + 1.0)
        assert came / len(alive) == pytest.approx(H.hazard(v, cap, t), abs=0.01), t


def test_the_vault_shown_of_the_stake_in_play_pays_the_same_at_every_moment():
    """The page shows the bank's cut taken up front and the vault in multiples of the stake left in play (m' = m / base).
    Only the labels move: at every moment, leaving pays what it always did, stake x floor2(m), and m' x the stake in
    play is that payout; the police still come at m' = 1 / U, so the chance of reaching m' is 1 / m'."""
    rng = random.Random(11)
    for e in (0.01, 0.027, 0.045, 0.06, 0.08):
        for stake in (1, 37, 100, 250, 1000):
            v, cap = _vault(e, cap=rng.choice((3.0, 6.0, 12.0)), safe=rng.choice((4.0, 9.0, 12.0)))
            base, ip = v["base"], H.in_play(stake, v["base"])
            assert ip == int(stake * H._floor2(1 - e)) and stake - ip >= 0
            for t in [i * 0.37 for i in range(int((v["safe"] + v["secs"]) / 0.37) + 2)]:
                m = H.pile_at(v, cap, t)
                old = int(stake * H._floor2(m))                     # the payout before the rescale
                x = H.shown(m, base)
                assert x == H._floor2(m / base)
                assert abs(x * base - m) <= 0.01 * base + 1e-9     # m' is m over the stake in play, nothing else
                r = {"how": "escaped", "take": H._floor2(m), "payout": old, "shown": x}
                assert H.verdict(stake, base, r)["net"] == old - stake   # the end screen's sum is the real one
                if t < v["safe"]:
                    assert x < 1.0 + 1e-9                           # the safe time only gets your stake back
                else:
                    assert x >= 1.0 and old >= ip                   # after it, at least the stake in play
        for u in (1.0, 0.8, 0.5, 0.1, 1e-6):
            assert H.police_take(e, u) / (1 - e) == pytest.approx(1 / u)


def test_the_safe_time_ends_at_exactly_one_and_leaving_there_is_breaking_even(em):
    for e in (0.01, 0.0333, 0.045, 0.0712, 0.08):
        v, cap = _vault(e, safe=7.3)
        assert H.shown(H.pile_at(v, cap, v["safe"]), v["base"]) == 1.00
        assert H.shown(H.pile_at(v, cap, v["safe"] - 0.05), v["base"]) < 1.00
    rid = crew(em, stake=100)
    to_vault(em, rid, police_take=None)
    r = H._rooms[rid]
    v = r["vault"]
    H.grab(A, rid, [0.0])                                           # in on the first instant: a perfect bag
    grab_to(em, rid, v["safe"] + 0.001)
    H.leg_it(A, rid)
    t, take, x = v["out"][str(A)]
    assert x == 1.00 and take == H._floor2(H.bag(r, A, t))           # 1.00x shown, the old take paid
    page = H.view(r, A, name_of)
    assert page["vault"]["outX"][str(A)] == 1.00 and page["vault"]["inPlay"] == H.in_play(100, v["base"])
    assert page["report"]["cut"] == 100 - page["report"]["inPlay"] > 0
    grab_to(em, rid, v["safe"] + v["secs"] + 1, uids=(B, D))
    H.sweep()
    res = r["results"][str(A)]
    assert res["payout"] == int(100 * take) == H.in_play(100, v["base"])
    vd = H.view(r, A, name_of)["verdicts"][str(A)]
    cut = 100 - res["payout"]
    assert vd["kind"] == "even" and vd["net"] == -cut
    assert vd["text"] == f"Out at 1.00x: stake back, broke even less the bank's cut (-{cut})"
    assert H.verdict(100, v["base"], {"how": "escaped", "take": 1.2, "payout": 120, "shown": 1.25})["text"] == "Out at 1.25x: +20"
    assert H.verdict(100, v["base"], {"how": "nicked", "take": 0.0, "payout": 0})["text"] == "Nicked: -100"
    assert "before the stake was back" in H.verdict(100, v["base"], {"how": "escaped", "take": 0.5, "payout": 50, "shown": 0.52})["text"]
    from lib.activities import heist_card
    html = heist_card.game_page(r, {A: "Ann", B: "Ben", D: "Dot"})
    assert "BROKE EVEN" in html and f"+{-cut}" not in html and "+-" not in html
    assert f"NET -{cut}" in html and "CUT -" not in html
    # out a little past 1.00x, with more than the stake in play back but less than the buy-in: the figure against
    # the buy-in is the net, and the bank's cut is still the cut, each labelled as what it is
    ip = H.in_play(100, v["base"])
    assert cut >= 2 and ip == 100 - cut
    vd = H.verdict(100, v["base"], {"how": "escaped", "take": (ip + 1) / 100, "payout": ip + 1, "shown": 1.01})
    assert vd["kind"] == "even" and vd["net"] == 1 - cut
    assert vd["text"] == f"Out at 1.01x: +1 on the stake, net {1 - cut} (the bank's cut was {cut})"
    res.update(payout=ip + 1, take=(ip + 1) / 100, shown=1.01)
    html = heist_card.game_page(r, {A: "Ann", B: "Ben", D: "Dot"})
    assert f"BROKE EVEN · NET {1 - cut}" in html and f"CUT {1 - cut}" not in html and f"BANK'S CUT {cut} EACH" in html


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
        H.vote(u, rid, "roof", "quiet")
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
    H.vote(A, rid, "roof", "quiet")
    H.vote(B, rid, "drains", "quiet")
    H.vote(D, rid, "drains", "quiet")
    assert r["state"] == "job" and r["approach"] == "drains"
    assert [x["key"] for x in r["rooms"]] == H.rooms_of.routes("society")["drains"]["rooms"]
    r["over"] = True
    rid = crew(em, players=(A, B), stake=0, target="society")
    H.vote(A, rid, "roof", "quiet")
    H.vote(B, rid, "front", "quiet")
    assert H._rooms[rid]["approach"] == "roof"                    # a tie: the host's way


def test_a_room_goes_to_whoever_has_the_job_and_terry_does_the_rest(em):
    rid = crew(em, players=(A, B), stake=0, target="post")       # Anne the Hacker, Ben the Safecracker
    r = H._rooms[rid]
    for u in (A, B):
        H.vote(u, rid, "front", "quiet")
    things = here(rid)                                            # the shop: the bell, Maureen, the till
    assert {th["role"]: th["state"] for th in things if not th["spare"]} == {"lookout": "terry", "face": "terry", "muscle": "terry"}
    # and a spare job each for Anne and Ben, whose roles the shop hasn't got: they're on them as they walk in
    assert {th["role"]: (th["state"], th["who"]) for th in things if th["spare"]} == {"hacker": ("active", A), "safecracker": ("active", B)}
    with pytest.raises(H.Refuse, match="not in the room"):
        H.play(A, rid, {"ob": things[0]["id"], "step": True})
    tick(em, H.MOVE_SECONDS + 0.01)
    mine = next(th for th in things if th["spare"] and th["who"] == A)
    with pytest.raises(H.Refuse, match="Finish"):
        H.play(A, rid, {"ob": things[1]["id"], "step": True})   # her own spare first
    H.play(A, rid, {"ob": mine["id"], "go": True})
    for mv in moves(mine["role"], mine["p"]):
        H.play(A, rid, {"ob": mine["id"], **mv})
    H.play(A, rid, {"ob": things[1]["id"], "step": True})       # then Anne steps in for Maureen
    with pytest.raises(H.Refuse, match="Finish"):
        H.play(A, rid, {"ob": things[2]["id"], "step": True})
    th = things[1]
    assert th["state"] == "active" and th["go"] > H.time.time()   # a run-down for that one too
    H.play(A, rid, {"ob": th["id"], "go": True})
    for mv in moves("face", th["p"]):
        H.play(A, rid, {"ob": th["id"], **mv})
    assert th["state"] == "done" and th["score"] == pytest.approx(H.STEP_IN)    # not her job: a bit worse
    ben = next(th for th in things if th["spare"] and th["who"] == B)
    H.play(B, rid, {"ob": ben["id"], "go": True})
    for mv in moves(ben["role"], ben["p"]):
        H.play(B, rid, {"ob": ben["id"], **mv})
    tick(em, H.TERRY_AFTER[1] + 0.1)
    H.room(A, rid, name_of)
    assert r["at"] == 1                                           # Terry's done his bits: on to the office
    assert all(th["state"] == "done" and th["score"] == th["terry"] for th in r["rooms"][0]["things"]
               if th is not things[1] and not th["spare"])


def test_everyone_in_the_crew_has_something_to_do_in_every_room(em):
    rid = crew(em, players=(A, B, D), stake=0, target="vault")   # a Hacker, a Safecracker and a Lookout
    r = H._rooms[rid]
    for u in (A, B, D):
        H.vote(u, rid, "roof", "quiet")
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


def test_quiet_or_loud_is_the_crews_call_and_the_host_breaks_a_tie(em):
    rid = crew(em, players=(A, B), stake=0, target="post")
    r = H._rooms[rid]
    H.vote(A, rid, "front", "quiet")
    H.vote(B, rid, "front")
    assert r["state"] == "plan"                                  # Ben's not said quiet or loud yet
    with pytest.raises(H.Refuse, match="loud"):
        H.vote(B, rid, style="sneaky")
    H.vote(B, rid, style="loud")
    assert r["state"] == "job" and r["style"] == "quiet"         # one each: the host's call
    assert all(th["mode"] == "quiet" and th["p"].get("game") != "loud" for _, th in H._things(r))
    assert all(th["state"] in ("active", "terry") for th in here(rid))   # no quiet-or-loud choice per thing


def test_a_loud_job_is_a_breach_against_one_timer(em):
    rid = crew(em, players=(A, B, D), stake=100, target="society")
    r = H._rooms[rid]
    for u in (A, B, D):
        H.vote(u, rid, "roof", "loud")
    assert r["style"] == "loud"
    # a few tasks each, every one a quick bit of action, and nothing for Terry
    for u in (A, B, D):
        mine = H._tasks(r, u)
        assert 1 <= len(mine) <= H.LOUD_TASKS and all(th["p"]["game"] == "loud" and th["state"] in ("active", "waiting") for th in mine)
        assert [th["state"] for th in mine].count("active") == 1                  # one at a time
    assert all(th["who"] in (A, B, D) for _, th in H._things(r))
    b = r["breach"]
    t0 = r["deadline"] - r["arrive"]
    assert t0 == pytest.approx(b["t0"]) and b["safe"] == H.LOUD_SAFE[0]
    tick(em, H.MOVE_SECONDS + 0.01)
    # Anne does her first task: the timer comes down, the safe time goes up, and she's straight on to her next
    first = H._current(r, A)
    H.play(A, rid, {"ob": first["id"], "go": True})
    for mv in moves(first["role"], first["p"]):
        H.play(A, rid, {"ob": first["id"], **mv})
    assert first["state"] == "done" and first["score"] == pytest.approx(1.0)
    assert r["deadline"] - r["arrive"] == pytest.approx(t0 - H.LOUD_CUT)
    assert b["safe"] == pytest.approx(H.LOUD_SAFE[0] + H.LOUD_SAFE[1]) and b["done"] == 1
    nxt = H._current(r, A)
    assert nxt is not None and nxt is not first and nxt["brief"] == "short"   # she's met the game: the quick run-down
    # the timer runs out with Ben and Dot still on their first: everyone's in the vault, done or not
    tick(em, r["deadline"] - H.time.time() + 0.01)
    H.room(A, rid, name_of)
    assert r["state"] == "report"
    tasks = [th for _, th in H._things(r)]
    assert r["score"] == pytest.approx(sum(th["score"] for th in tasks) / len(tasks), abs=1e-4)
    assert r["edge"] == pytest.approx(H.loud_edge(r["score"]), abs=1e-4)
    assert r["vault"]["safe"] == pytest.approx(b["safe"])


def test_loose_cash_pays_whoever_picks_it_up_straight_away(em):
    rid = crew(em, players=(A, B, D), stake=500, target="post")
    r = H._rooms[rid]
    for u in (A, B, D):
        H.vote(u, rid, "front", "loud")
    before = em.get_bb(A)
    loud_through(em, rid)
    assert r["state"] in ("report", "vault")
    got = r["loose"][str(A)]
    assert 0 < got <= int(500 * H.LOOSE_MAX)                                    # a few UKPence, never past the cap
    assert em.get_bb(A) == before + got                                         # paid there and then
    extra = [th for th in r["extra"] if th["who"] == A]
    assert extra and all(th["mode"] == "loose" for th in extra) and len(extra) <= H.LOOSE_TASKS
    assert all(th["p"]["game"].startswith("loose-") for th in extra)
    assert 3 <= H._loose_each(r) <= 15 and H._loose_each({"stake": 1000}) == 15 and H._loose_each({"stake": 50}) < 4
    # nothing for loose cash on a free job, and none of it in practice is real money (below)
    r["over"] = True
    rid = crew(em, players=(A, B, D), stake=0, target="post")
    for u in (A, B, D):
        H.vote(u, rid, "front", "loud")
    loud_through(em, rid)
    assert not H._rooms[rid].get("extra") and not H._rooms[rid]["loose"]


def test_a_loud_jobs_best_with_all_its_loose_cash_is_no_better_than_a_quiet_ones():
    assert H.loud_edge(1.0) - H.LOOSE_MAX == pytest.approx(H.EDGE_BEST)
    assert H.loud_edge(0.0) == pytest.approx(H.EDGE_WORST) and H.loud_edge(1.0) > H.edge(1.0)


def test_every_game_has_its_run_down():
    for role in H.ROLES:
        for key in H.heist_games.keys(role):
            how, line = H.briefs.BRIEFS[f"{role}:{key}"]
            assert how and line, (role, key)
    for key, (how, line) in H.briefs.BRIEFS.items():
        # the goal in a few plain words, then what you do, and maybe what scores best: two or three lines
        assert 2 <= len(how) <= 3 and len(how[0]) <= 90 and len(line) <= 75, key
        assert not any(w in " ".join(how).lower() for w in ("going loud", "revs", "the zone")), key
    assert {f"loud:{r}" for r in H.ROLES} | {"loose:atm", "loose:till", "loose:box"} <= set(H.briefs.BRIEFS)


def test_a_puzzles_clock_waits_for_its_run_down(em):
    rid = crew(em, players=(A, B), stake=0, target="post")
    r = H._rooms[rid]
    for u in (A, B):
        H.vote(u, rid, "front", "quiet")
    rm = r["rooms"][0]
    mine = next(th for th in rm["things"] if th["who"] == A)
    # nobody's met a game yet: the full run-down, and the room's clock grows to fit it
    assert rm["brief"] == H.BRIEF_FULL
    assert r["deadline"] - r["arrive"] == pytest.approx(H.rooms_of.room_seconds(
        [(t["role"], t["name"], t["size"]) for t in rm["things"] if not t["spare"]]) + H.BRIEF_FULL)
    assert mine["state"] == "active" and mine["brief"] == "full" and mine["go"] == pytest.approx(r["arrive"] + H.BRIEF_FULL)
    tick(em, H.MOVE_SECONDS + 0.5)
    with pytest.raises(H.Refuse, match="run-down"):
        H.play(A, rid, {"ob": mine["id"], **moves(mine["role"], mine["p"])[0]})
    tick(em, 3)
    b = H.view(r, A, name_of)["puzzle"]["briefing"]
    assert b["full"] and b["how"] and b["line"] and b["left"] == pytest.approx(H.BRIEF_FULL - 3.5)
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
    H.vote(A, rid, "front", "quiet")
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
    grab_to(em, rid, secs_to(r, 1.6))
    H.leg_it(A, rid)
    assert r["vault"]["out"][str(A)][1] == pytest.approx(1.6, abs=0.05)    # kept up with the piles (bar the first quarter-second)
    with pytest.raises(H.Refuse, match="already out"):
        H.leg_it(A, rid)
    grab_to(em, rid, secs_to(r, 2.0) + 0.01, uids=(B, D))
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


def test_the_safe_time_is_safe_but_pays_less_than_the_buy_in(em):
    rid = crew(em, stake=100)
    to_vault(em, rid, police_take=None)
    r = H._rooms[rid]
    v = r["vault"]
    v["police"] = H.police_time(v["base"], r["cap"], v["secs"], v["base"], v["safe"])   # as early as they can come
    assert v["safe"] == pytest.approx(H.SAFE_QUIET[0] + (H.SAFE_QUIET[1] - H.SAFE_QUIET[0]) * r["score"], abs=0.01)
    assert v["police"] == pytest.approx(v["safe"])                  # and they can't come till it's over
    grab_to(em, rid, v["safe"] - 0.5)
    H.leg_it(A, rid)
    took = v["out"][str(A)][1]
    assert 0.8 < took < 1 - r["edge"]                              # out safe, with a bit less than the buy-in
    H.leg_it(B, rid)
    tick(em, 1.0)
    H.sweep()
    assert r["over"] and r["results"][str(D)]["how"] == "nicked" and r["results"][str(A)]["payout"] == int(100 * took)


def test_a_cleaner_quiet_job_gets_a_longer_safe_time(em):
    rid = crew(em, stake=0)
    r = H._rooms[rid]
    for u in (A, B, D):
        H.vote(u, rid, "front", "quiet")
    tick(em, 600)                                                    # nobody does a thing
    H.room(A, rid, name_of)
    assert r["vault"]["safe"] == pytest.approx(H.SAFE_QUIET[0] + (H.SAFE_QUIET[1] - H.SAFE_QUIET[0]) * r["score"], abs=0.01)
    assert r["vault"]["safe"] < H.SAFE_QUIET[0] + 3


def test_no_grabbing_no_cash(em):
    rid = crew(em, stake=100)
    to_vault(em, rid, police_take=None)
    r = H._rooms[rid]
    grab_to(em, rid, r["vault"]["safe"] + 5, uids=(A,))
    H.leg_it(A, rid)
    H.leg_it(B, rid)                                              # Ben never grabbed a thing
    assert r["vault"]["out"][str(B)][1] == 0.0 and r["vault"]["out"][str(A)][1] > 0.9
    v = H.view(r, D, name_of)["vault"]
    assert v["bag"] == 0.0 and v["base"] == pytest.approx(1 - r["edge"]) and v["risk"] > 0
    assert "police" not in v


def test_holding_your_nerve_to_the_cap_clears_the_vault(em):
    rid = crew(em, stake=100, target="society")
    to_vault(em, rid, police_take=None)
    r = H._rooms[rid]
    grab_to(em, rid, r["vault"]["safe"] + r["vault"]["secs"] + 1)
    H.sweep()
    assert r["over"] and all(x["how"] == "cleared" for x in r["results"].values())
    pay = r["results"][str(A)]["payout"]
    assert int(100 * r["cap"]) - 3 <= pay <= int(100 * r["cap"])
    # and the clean job bonus on top (taxed like any reward, once a balance is over 10k)
    assert 0 < em.get_bb(A) - (9_900 + pay) <= r["bonus"][str(A)]


def test_a_clean_job_pays_its_bonus_once_a_day(em):
    def job():
        rid = crew(em, stake=500)
        to_vault(em, rid, police_take=None)
        r = H._rooms[rid]
        grab_to(em, rid, secs_to(r, 1.7))
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
    grab_to(em, rid, secs_to(r, 1.2))
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
    grab_to(em, rid, secs_to(r, 1.5), uids=(A, B))
    assert H.bag(r, D, secs_to(r, 1.5)) == pytest.approx(H.WALKOFF_GRAB * 1.5, abs=0.01)    # the crew bag a bit for her
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
        H.vote(u, rid, "roof", "quiet")
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
    grab_to(em, rid, r["vault"]["safe"] + 2)
    H.sweep()                                         # saved two seconds into the climb
    at = H.time.time() - r["vault"]["start"]
    held = H.bag(r, A, at)
    tick(em, 600)                                     # the bot's away for ten minutes
    H._rooms.clear()
    H._loaded = False
    H._load()
    r = H._rooms[rid]
    assert not r["over"] and H.time.time() - r["vault"]["start"] == pytest.approx(at, abs=0.01)
    H.leg_it(A, rid)
    assert r["vault"]["out"][str(A)][1] == pytest.approx(H._floor2(held), abs=0.01)


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


def practice_to_vault(em, rid, police_take=None, way=None, style="quiet"):
    """Your vote makes the plan; then every room, you do your things and the bots do theirs; then the vault opens."""
    r = H._rooms[rid]
    H.vote(A, rid, way or next(iter(H.rooms_of.routes(r["target"]))), style)
    assert r["state"] == "job"                        # the bots back the plan, so it's made once you've voted
    if style == "loud":
        loud_through(em, rid)
    while r["state"] == "job":
        at = r["at"]
        clear_room(em, rid)
        if r["state"] == "job" and r["at"] == at:
            # yours are done; the bots are still on theirs, then they aren't
            assert any(th.get("bot") is not None and th["state"] == "active" for th in here(rid))
            tick(em, H._room_secs(r["rooms"][at]))
            H.room(A, rid, name_of)
    assert r["state"] in ("report", "vault")          # (a loud crew that cut the timer hard can be in already)
    v = r["vault"]
    v["police"] = None if police_take is None else H.police_time(police_take, r["cap"], v["secs"], v["base"], v["safe"])
    tick(em, max(0.0, v["start"] - H.time.time()))
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
    H.vote(A, rid, way, "loud")
    assert r["state"] == "job" and r["approach"] == way and r["style"] == "loud"


def test_a_bot_finishes_its_things_in_a_room_at_a_moment_of_its_own(em, testers):
    rid, r = practice(em, 0, "post", skill=0.6)
    H.vote(A, rid, next(iter(H.rooms_of.routes("post"))), "quiet")
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
    whole = v["safe"] + v["secs"]
    tick(em, v["safe"] + 0.6 * v["secs"])
    H.sweep()
    out = v["out"]
    assert {u for u, b in r["bots"].items() if b["leave"] <= 0.5} == set(out)
    for u in out:
        assert out[u][0] == pytest.approx(v["safe"] + r["bots"][u]["leave"] * v["secs"], abs=0.01)
        # a bot grabs as well as its skill: its bag is that share of the piles, and a bit more for any gold bars it caught
        piles = H.pile_at(v, r["cap"], out[u][0])
        assert H._floor2(r["bots"][u]["grab"] * piles) - 0.011 <= out[u][1] <= piles + 0.011
        assert out[u][1] == pytest.approx(H._floor2(H.bag(r, int(u), out[u][0])), abs=0.011)
    H.leg_it(A, rid)
    tick(em, whole)
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
    tick(em, r["vault"]["safe"] + r["vault"]["secs"])
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
    grab_to(em, rid, r["vault"]["safe"] + 0.85 * r["vault"]["secs"], uids=(A,))
    H.leg_it(A, rid)
    assert r["over"] and r["how"] == "clean"
    assert r["results"][str(A)]["payout"] > 1000      # what it would have been, in play money
    assert em.get_bb(A) == before and supply() == total
    assert r["bonus"] == {} and H.heat() == 0.0 and seen == []
    assert H.active() == 0                            # a practice job doesn't hold up a restart


def test_the_bots_do_a_loud_job_and_your_loose_cash_is_play_money(em, testers, monkeypatch):
    rid, r = practice(em, 1000, "post", skill=0.8)
    before = em.get_bb(A)
    for b in r["bots"].values():
        b["leave"] = 0.9
    practice_to_vault(em, rid, style="loud")
    bots = [th for _, th in H._things(r) if th["who"] < 0]
    assert bots and all(th["state"] == "done" for th in bots)          # every bot task done (or out of time)
    assert r["loose"].get(str(A), 0) > 0 and em.get_bb(A) == before   # loose cash in play money: nothing moved
    assert all(str(u) not in r["loose"] for u in r["bots"])             # bots don't go after loose cash
    grab_to(em, rid, max(H.time.time() - r["vault"]["start"], r["vault"]["safe"]) + 6, uids=(A,))
    H.leg_it(A, rid)
    assert r["vault"]["out"][str(A)][1] > 0                           # (only what she grabbed: she came in late)


def test_quick_chat_is_a_few_set_lines_and_not_too_often(em):
    rid = crew(em, players=(A, B), stake=0)
    H.say(A, rid, "hurry")
    with pytest.raises(H.Refuse, match="second"):
        H.say(A, rid, "nice")                                     # too soon
    with pytest.raises(H.Refuse, match="say"):
        H.say(B, rid, "<b>anything I like</b>")                  # only the set lines
    H.say(B, rid, "oops")
    chat = H.view(H._rooms[rid], B, name_of)["chat"]
    assert [(c["uid"], c["text"]) for c in chat] == [(str(A), "Hurry up!"), (str(B), "Oops")]
    tick(em, H.CHAT_EVERY)
    H.say(A, rid, "nice")
    tick(em, H.CHAT_SHOW)
    assert H.view(H._rooms[rid], B, name_of)["chat"] == []      # gone after a few seconds
    with pytest.raises(H.Refuse):
        H.say(D, rid, "hurry")                                    # not in the crew


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
