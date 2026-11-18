"""Bank Job's loud tasks, one game a role (lib/activities/heist_games/loud_roles.py): each is made as plain public
data, a perfect timeline scores 1, a timeline cut off scores as it stands, rubbish (junk, out of order, from the
future, too fast) is refused and changes nothing, the dev bots can finish one, and the replay lands on exactly the
numbers in the golden timelines (tests/data/heist_loud_golden.json), which the page's copy of the steps
(ukplace-activities scripts/heist-loud-golden.ts) checks against the same file. Then through a real job: the
run-down's keyed by the game, a task's clock can't be beaten, and one left alone is scored when its time's up."""

import copy
import json
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OPENAI_TOKEN", "mock-token")

from lib.activities import heist as H  # noqa: E402
from lib.activities.heist_games import Refuse  # noqa: E402
from lib.activities.heist_games import loud_roles as LR  # noqa: E402
from lib.activities.heist_games.loud import LOUD  # noqa: E402

from tests.test_heist import A, B, crew, em, name_of, tick  # noqa: E402,F401

GOLDEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "heist_loud_golden.json")
KINDS = [pytest.param(role, id=f"{role}-{g.kind}") for role, g in LR.GAMES.items()]


def fresh(role, seed=1, size="s"):
    g = LR.GAMES[role]
    return g, g.make(random.Random(seed), size, {})


@pytest.mark.parametrize("role", KINDS)
def test_each_role_has_its_own_loud_game(role):
    g = LOUD[role]
    assert g is LR.GAMES[role] and g.key == "loud" and g.role == role and g.title == g.title.upper()
    assert f"loud:{role}:{g.kind}" in H.briefs.BRIEFS


@pytest.mark.parametrize("role", KINDS)
@pytest.mark.parametrize("size", ["s", "big"])
def test_made_and_shown_as_plain_data(role, size):
    for seed in range(6):
        g, p = fresh(role, seed, size)
        assert json.loads(json.dumps(p)) == p and json.loads(json.dumps(g.public(p))) == g.public(p)
        assert not g.done(p) and g.score(p) < 1
        lo, hi = (10, 13) if size == "s" else (15, 18)
        assert lo <= g.secs(p) <= hi, (seed, g.secs(p))


@pytest.mark.parametrize("role", KINDS)
@pytest.mark.parametrize("size", ["s", "big"])
def test_a_perfect_timeline_scores_one(role, size):
    for seed in range(12):
        g, p = fresh(role, seed, size)
        for mv in g.solve(copy.deepcopy(p)):
            g.move(p, json.loads(json.dumps(mv)))
        assert g.done(p) and g.score(p) == pytest.approx(1.0), (seed, p.get("size"))


@pytest.mark.parametrize("role", KINDS)
def test_cut_off_partway_it_scores_as_it_stands(role):
    for seed in range(6):
        g, p = fresh(role, seed)
        ev = g.solve(copy.deepcopy(p))[0]["ev"]
        half = g.secs(p) * 500
        g.move(p, {"ev": [e for e in ev if e[0] <= half], "at": half})
        assert not g.done(p)
        assert 0.2 < g.score(p) < 0.85, (seed, g.score(p))
        # and the rest of it later finishes the job off
        g.move(p, {"ev": [e for e in ev if e[0] > half], "end": True})
        assert g.score(p) == pytest.approx(1.0)


@pytest.mark.parametrize("role", KINDS)
def test_rubbish_is_refused_and_changes_nothing(role):
    g, p = fresh(role)
    w = g.WIDTH
    ok = [g.solve(copy.deepcopy(p))[0]["ev"][0]]
    T = g.secs(p) * 1000
    bad = [{"ev": "nope"}, {"ev": [[1]]}, {"ev": [[1.5] * w]}, {"ev": [[True] * w]}, {"ev": [None]}, {"ev": [[10 ** 12] * w]},
           {"ev": [[-5] + ok[0][1:]]}, {"ev": [[T + 2000] + ok[0][1:]]},                     # before the start, after the end
           {"ev": [[200] + ok[0][1:], [100] + ok[0][1:]]},                                   # out of order
           {"ev": [[i * 20] + ok[0][1:] for i in range(40)]},                                # 50 a second
           {"ev": ok, "_now": ok[0][0] - LR.GRACE - 50},                                     # from the future
           {"ev": [], "end": True, "_now": T - LR.GRACE - 500},                              # over before it's over
           {"ev": [], "at": "soon"}, {"ev": [[0] + [5000] * (w - 1)]}]
    for body in bad:
        before = copy.deepcopy(p)
        with pytest.raises(Refuse):
            g.move(p, body)
        assert p == before, body
    g.move(p, {"ev": ok, "_now": ok[0][0] + 100})
    with pytest.raises(Refuse):                                                               # nothing before what's in
        g.move(p, {"ev": [[ok[0][0] - 1] + ok[0][1:]]})


@pytest.mark.parametrize("role", KINDS)
def test_the_dev_bots_can_finish_one_and_skill_shows(role):
    worse, better = [], []
    for seed in range(8):
        g, p = fresh(role, seed, "big" if seed % 2 else "s")
        q = copy.deepcopy(p)
        rng = random.Random(seed)
        g.move(p, json.loads(json.dumps(g.bot(p, rng, 0.35))))
        g.move(q, json.loads(json.dumps(g.bot(q, rng, 0.95))))
        assert g.done(p) and g.done(q) and 0.0 <= g.score(p) <= 1.0
        worse.append(g.score(p))
        better.append(g.score(q))
    assert sum(better) > sum(worse)


# ---- the golden timelines: the page's steps land on the same numbers -------------------------------------------------

def golden_cases():
    """A schedule and a so-so player's timeline for each game with steps, and what the replay comes to."""
    out = []
    for role, g in LR.GAMES.items():
        if not hasattr(g, "trace"):
            continue
        for seed, size in ((3, "s"), (8, "big")):
            p = g.make(random.Random(seed), size, {})
            ev = g.lazy(p, random.Random(seed), 0.6)
            out.append({"kind": g.kind, "seed": seed, "size": size, "pub": {k: v for k, v in g.public(p).items() if k != "ev"},
                        "ev": ev, "trace": g.trace({**p, "ev": ev}, g.steps(p))})
    return out


def test_the_replay_lands_on_the_golden_numbers():
    """Regenerate with HEIST_GOLDEN=1 (then copy the file to ukplace-activities/scripts/heist-loud-golden.json)."""
    cases = golden_cases()
    if os.environ.get("HEIST_GOLDEN"):
        os.makedirs(os.path.dirname(GOLDEN), exist_ok=True)
        with open(GOLDEN, "w") as f:
            json.dump(cases, f, separators=(",", ":"))
    if not cases:
        return
    with open(GOLDEN) as f:
        gold = json.load(f)
    assert [(c["kind"], c["seed"], c["size"]) for c in gold] == [(c["kind"], c["seed"], c["size"]) for c in cases]
    for want, got in zip(gold, cases):
        assert want["pub"] == got["pub"] and want["ev"] == got["ev"]
        assert want["trace"] == got["trace"], want["kind"]       # exactly: every number, to the last bit


# ---- through a job ---------------------------------------------------------------------------------------------------

def loud_job(em, roles=("lookout", "muscle")):
    rid = crew(em, players=(A, B), stake=0, target="post")
    r = H._rooms[rid]
    for u, role in zip((A, B), roles):
        r["roles"][str(u)] = role
    for u in (A, B):
        H.vote(u, rid, "front", "loud")
    tick(em, H.MOVE_SECONDS + 0.01)
    H.room(A, rid, name_of)
    return rid, r


def test_a_loud_tasks_run_down_is_keyed_by_its_game(em):
    rid, r = loud_job(em)
    th = H._current(r, A)
    assert th["role"] == "lookout" and H._game_key(th) == f"loud:lookout:{LR.GAMES['lookout'].kind}"
    view = H.room(A, rid, name_of)["puzzle"]
    assert view["briefing"]["key"] == H._game_key(th) and view["briefing"]["full"]
    assert view["briefing"]["how"] == H.briefs.BRIEFS[H._game_key(th)][0]


def test_someone_who_met_the_old_loud_game_gets_the_new_ones_full_run_down(em):
    H._met[str(A)] = {"loud:lookout": 1_000_000.0, "loud:lookout:pop": 1_000_000.0}
    rid, r = loud_job(em)
    assert H._current(r, A)["brief"] == "full"


def test_a_loud_tasks_clock_cant_be_beaten(em):
    rid, r = loud_job(em)
    th = H._current(r, A)
    H.play(A, rid, {"ob": th["id"], "go": True})
    g = LOUD["lookout"]
    ev = g.solve(copy.deepcopy(th["p"]))[0]["ev"]
    with pytest.raises(H.Refuse):                     # the whole thing played at once, the moment it starts
        H.play(A, rid, {"ob": th["id"], "ev": ev, "end": True})
    tick(em, 1.0)
    H.play(A, rid, {"ob": th["id"], "ev": [e for e in ev if e[0] <= 1000], "at": 1000})
    tick(em, g.secs(th["p"]))
    H.play(A, rid, {"ob": th["id"], "ev": [e for e in ev if e[0] > 1000], "end": True})
    assert th["state"] == "done" and th["score"] == pytest.approx(1.0)


def test_a_loud_task_left_alone_is_scored_when_its_time_is_up(em):
    rid, r = loud_job(em)
    th = H._current(r, A)
    H.play(A, rid, {"ob": th["id"], "go": True})
    g = LOUD["lookout"]
    ev = g.solve(copy.deepcopy(th["p"]))[0]["ev"][:1]
    tick(em, 2.0)
    H.play(A, rid, {"ob": th["id"], "ev": ev, "at": 2000})
    tick(em, g.secs(th["p"]) + H.LOUD_GRACE - 2.0 - 0.5)
    H.room(A, rid, name_of)
    assert th["state"] == "active"
    tick(em, 1.0)
    H.room(A, rid, name_of)
    assert th["state"] == "done" and 0 < th["score"] < 1               # as it stood: the one round called
    assert H._current(r, A) is not th


def test_practice_bots_take_about_as_long_as_a_person():
    assert H.BOT_LOUD == (9.0, 18.0)
