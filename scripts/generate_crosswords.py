"""Generate HMS Crossword puzzles and add them to the puzzle file as a new date-gated set.

    python3 scripts/generate_crosswords.py data/words/crosswords.json --from 2027-04-08 --hard

Fills every layout it may use, in parallel, from the clued word bank in crossword_bank.py,
then picks the hardest grids that stay fresh (few answers shared with each other or with the
set before, no word worn out) and orders them so the layout changes every day. Rewards and
rules carry over from the last set. Nothing already in the file is touched.

Every entry is guaranteed a clue because the fill can only use words the bank already clues.
It's indexed by (length, position, letter) so candidate lookup is a set intersection rather
than a scan, and entries are chosen most-constrained-first, which is what makes the
interlocking grid tractable at all.
"""
import random, sys, json, time
from collections import Counter, defaultdict
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from crossword_bank import BANK, HARD, VOCAB

import itertools


def _all_layouts(n, max_len):
    """Every n x n layout where each white run, across and down, is 3..max_len long - so every
    white cell is crossed by BOTH an across and a down entry (a cell reachable from only one
    direction is unfair: there's no second way to get at it) - and the white cells all connect.

    Built a row at a time from the row patterns whose own runs are legal, the columns checked as
    they grow. This used to allow only layouts that look the same upside down: 41 of them at 6x6,
    of which the bank fills six, and one of those so much more easily than the rest that it ran 25
    days in 30. Without the symmetry there are 4,069, and hundreds fill."""
    def runs_ok(bits):
        run = 0
        for b in list(bits) + [1]:
            if b:
                if 0 < run < 3:
                    return False
                run = 0
            else:
                run += 1
                if run > max_len:
                    return False
        return True

    rows = [r for r in itertools.product((0, 1), repeat=n) if runs_ok(r)]
    out = []

    def dfs(done, col):
        if len(done) == n:
            if any(0 < x < 3 for x in col):
                return
            black = frozenset((r, c) for r in range(n) for c in range(n) if done[r][c])
            white = {(r, c) for r in range(n) for c in range(n)} - black
            if not white:
                return
            start = next(iter(white)); seen = {start}; stack = [start]
            while stack:
                r, c = stack.pop()
                for q in ((r + 1, c), (r - 1, c), (r, c + 1), (r, c - 1)):
                    if q in white and q not in seen:
                        seen.add(q); stack.append(q)
            if seen == white:
                out.append(black)
            return
        for row in rows:
            nxt = []
            for c in range(n):
                if row[c]:
                    if 0 < col[c] < 3:
                        break
                    nxt.append(0)
                else:
                    if col[c] + 1 > max_len:
                        break
                    nxt.append(col[c] + 1)
            else:
                dfs(done + [row], nxt)

    dfs([], [0] * n)
    return out


def _tidy(black, n):
    """Black squares that shape the grid rather than spoil it. Blocks of black at the edges are
    fine - they give the grid its outline - but not a 2x2 in the middle, a 3x3 lump anywhere, or
    a whole black row or column, which is just a smaller grid."""
    sq = lambda r, c, k: {(r + i, c + j) for i in range(k) for j in range(k)} <= black
    inner = any(sq(r, c, 2) for r in range(1, n - 2) for c in range(1, n - 2))
    lump = any(sq(r, c, 3) for r in range(n - 2) for c in range(n - 2))
    line = (any(all((r, c) in black for c in range(n)) for r in range(n))
            or any(all((r, c) in black for r in range(n)) for c in range(n)))
    return not (inner or lump or line)


N = 5
# Harder-tier words to plant, by length (see fill). Empty means plant nothing.
PLANT = {}

def _reindex():
    """Rebuild the lookup indexes after the word bank is swapped (see --hard)."""
    BY_LEN.clear()
    IDX.clear()
    for w in BANK:
        BY_LEN[len(w)].add(w)
        for i, ch in enumerate(w):
            IDX[(len(w), i, ch)].add(w)


BY_LEN = defaultdict(set)
IDX = defaultdict(set)
for w in BANK:
    BY_LEN[len(w)].add(w)
    for i, ch in enumerate(w):
        IDX[(len(w), i, ch)].add(w)

def entries(black, N=None):
    N = N or globals()['N']
    nums, n = {}, 0
    for r in range(N):
        for c in range(N):
            if (r, c) in black:
                continue
            a = (c == 0 or (r, c - 1) in black) and c + 1 < N and (r, c + 1) not in black
            d = (r == 0 or (r - 1, c) in black) and r + 1 < N and (r + 1, c) not in black
            if a or d:
                n += 1; nums[(r, c)] = n
    out = []
    for (r, c), num in sorted(nums.items()):
        if (c == 0 or (r, c - 1) in black) and c + 1 < N and (r, c + 1) not in black:
            cells, cc = [], c
            while cc < N and (r, cc) not in black:
                cells.append((r, cc)); cc += 1
            out.append(["across", num, cells])
    for (r, c), num in sorted(nums.items()):
        if (r == 0 or (r - 1, c) in black) and r + 1 < N and (r + 1, c) not in black:
            cells, rr = [], r
            while rr < N and (rr, c) not in black:
                cells.append((rr, c)); rr += 1
            out.append(["down", num, cells])
    return out

def fill(ents, rng, deadline, banned=frozenset(), plant=None):
    """plant is an (entry, word) pair written into the grid before the search starts."""
    grid, used = {}, set(banned)
    todo = list(ents)
    if plant:
        e0, w0 = plant
        for i, cell in enumerate(e0[2]):
            grid[cell] = w0[i]
        used.add(w0)
        todo = [e for e in todo if e is not e0]

    def cands(cells):
        L = len(cells)
        pool = None
        for i, cell in enumerate(cells):
            ch = grid.get(cell)
            if ch is None:
                continue
            s = IDX.get((L, i, ch))
            if not s:
                return []
            pool = s if pool is None else (pool & s)
            if not pool:
                return []
        pool = BY_LEN[L] if pool is None else pool
        return [w for w in pool if w not in used]

    def rec(remaining):
        if time.time() > deadline:
            raise TimeoutError
        if not remaining:
            return True
        scored = []
        for e in remaining:
            cs = cands(e[2])
            if not cs:
                return False                     # dead end, prune immediately
            # Ties broken at random, not by insertion order. On an empty grid every entry of
            # the same length has the same candidate count, so a stable sort opens every
            # search on the same slot and the fill keeps re-finding the same handful of
            # solutions - 2100 builds collapsed to 123 distinct grids, which is not enough
            # pool to pick a month of puzzles out of. Starting elsewhere costs nothing.
            scored.append(((len(cs), rng.random()), cs, e))
        scored.sort(key=lambda t: t[0])          # most constrained first
        _n, cs, e = scored[0]
        rest = [x for x in remaining if x is not e]
        rng.shuffle(cs)
        for w in cs[:80]:
            snap = {c: grid.get(c) for c in e[2]}
            for i, cell in enumerate(e[2]):
                grid[cell] = w[i]
            used.add(w)
            if rec(rest):
                return True
            used.discard(w)
            for cell, v in snap.items():
                if v is None: grid.pop(cell, None)
                else: grid[cell] = v
        return False

    try:
        return grid if rec(todo) else None
    except TimeoutError:
        return None


def _plant(ents, rng, banned):
    """Pick a harder-tier word and a slot to write it into, before anything else is placed.

    Reaching for the hard words during the search was tried and dropped - preferring a
    324-word tier inside a 1,935-word bank sent the search into corners it could not close,
    and throughput fell from 35 puzzles to 9. Fixing one slot up front constrains the search
    rather than fighting it. Over the same 3,200 seeds:

        no plant   3,200 filled   336 distinct   1.5 harder answers per grid, floor 0
        plant      1,350 filled   420 distinct   2.2 harder answers per grid, floor 1

    Fewer seeds get anywhere, and it is still the better deal: more distinct grids, harder
    ones, and six times quicker per grid built, because a planted slot prunes the tree
    before the search starts rather than after it has committed.
    """
    if not PLANT:
        return None
    slots = [e for e in ents if PLANT.get(len(e[2]))]
    if not slots:
        return None
    e0 = rng.choice(slots)
    pool = [w for w in PLANT[len(e0[2])] if w not in banned]
    return (e0, rng.choice(pool)) if pool else None

def _setup(hard, n):
    """The grid size and, in hard mode, the word bank, set in each worker (they start fresh).

    Restricting the FILL to the indirect-clue bank - not just the clue lookup - is what guarantees
    every entry in the puzzle gets an indirect clue. Swapping clues in afterwards would leave any
    word without one still showing its dictionary definition."""
    globals()["N"] = n
    if hard:
        BANK.clear()
        BANK.update(HARD)
        _reindex()
        PLANT.clear()
        for w in sorted(VOCAB):
            if w in BANK:
                PLANT.setdefault(len(w), []).append(w)


def _fill_layout(job):
    """Every distinct grid one layout fills in `secs` seconds."""
    li, black, secs = job
    ents = entries(black, N)
    t0, seed, seen, out = time.time(), 0, set(), []
    while time.time() - t0 < secs:
        rng = random.Random(li * 7919 + seed)
        seed += 1
        g = fill(ents, rng, min(time.time() + 1.5, t0 + secs), frozenset(), _plant(ents, rng, frozenset()))
        if not g:
            continue
        words = [{"num": num, "dir": kind, "answer": (a := "".join(g[c] for c in cells)),
                  "clue": BANK[a], "cells": [list(c) for c in cells]}
                 for kind, num, cells in ents]
        sig = frozenset(w["answer"] for w in words)
        if sig not in seen:
            seen.add(sig)
            out.append({"black": sorted([list(b) for b in black]), "entries": words})
    return out


def _sig(p):
    return frozenset(e["answer"] for e in p["entries"])


def _layout(p):
    return json.dumps(sorted(p["black"]))


def _pick(pool, before, count, overlap, word_cap, layout_uses):
    """The hardest grids (most harder-tier answers) that share no more than `overlap` answers with
    any other picked or with `before`, wear no word out past `word_cap` uses, and use each layout
    once before any is used twice (up to `layout_uses`). A mirror image of a grid has the same
    words, so it never gets in as a second puzzle."""
    rng = random.Random(11)
    hardness = lambda p: sum(e["answer"] in VOCAB for e in p["entries"])
    cands = sorted(pool, key=lambda p: (-hardness(p), rng.random()))
    picked, sigs, used, words = [], [_sig(p) for p in before], Counter(), Counter()
    for rnd in range(1, layout_uses + 1):
        for p in cands:
            if len(picked) >= count:
                return picked
            sig, k = _sig(p), _layout(p)
            if used[k] >= rnd or any(words[w] >= word_cap for w in sig):
                continue
            if all(len(sig & q) <= overlap for q in sigs):
                picked.append(p); sigs.append(sig); used[k] += 1; words.update(sig)
    return picked


def _arrange(picked, before, gap=6, layout_gap=30, tries=300):
    """An order where no word comes back within `gap` days, no layout within `layout_gap`, and the
    number of black squares changes day to day where it can - carrying on from the end of the set
    before. Many shuffles are tried; the one that breaks those rules least wins."""
    def attempt(seed):
        rng = random.Random(seed)
        days, left, bad = list(before[-layout_gap:]), picked[:], 0
        rng.shuffle(left)
        while left:
            recent = set().union(*[_sig(p) for p in days[-gap:]]) if days else set()
            near = {_layout(p) for p in days[-layout_gap:]}
            nb = len(days[-1]["black"]) if days else -1
            good = [i for i, p in enumerate(left) if not (_sig(p) & recent) and _layout(p) not in near]
            i = next((i for i in good if len(left[i]["black"]) != nb), good[0] if good else 0)
            bad += not good
            days.append(left.pop(i))
        return bad, days[len(before[-layout_gap:]):]
    return min((attempt(s) for s in range(tries)), key=lambda t: t[0])


if __name__ == "__main__":
    import argparse, datetime, os
    from multiprocessing import Pool
    ap = argparse.ArgumentParser(description="Add a set of HMS Crossword puzzles to the puzzle file.")
    ap.add_argument("file", help="the puzzle file (data/words/crosswords.json); the new set goes on the end")
    ap.add_argument("--from", dest="start", required=True, help="the day the new set starts, YYYY-MM-DD")
    ap.add_argument("--count", type=int, default=183, help="puzzles, one a day (default 183: six months)")
    ap.add_argument("--size", type=int, default=6, help="grid size (default 6)")
    ap.add_argument("--max-len", type=int, default=6, help="longest entry the bank can fill")
    ap.add_argument("--blacks", default="10-14", help="black squares a layout may have (default 10-14)")
    ap.add_argument("--min-clues", type=int, default=10, help="fewest clues a grid may have")
    ap.add_argument("--symmetric", action="store_true", help="only layouts that look the same upside down")
    ap.add_argument("--seconds", type=float, default=6, help="seconds spent filling each layout")
    ap.add_argument("--overlap", type=int, default=5, help="most answers two puzzles may share")
    ap.add_argument("--word-cap", type=int, help="most times one word may appear (default: once per 18 days)")
    ap.add_argument("--layout-uses", type=int, default=2, help="most times one layout may appear in the set")
    ap.add_argument("--hard", action="store_true",
                    help="fill only from words with an indirect (cryptic-lite) clue")
    ap.add_argument("--dry-run", action="store_true", help="report what it would add, write nothing")
    a = ap.parse_args()

    doc = json.load(open(a.file, encoding="utf-8"))
    last = doc["sets"][-1]
    start = datetime.date.fromisoformat(a.start)
    if start <= datetime.date.fromisoformat(last["from"]):
        raise SystemExit(f"--from must be after the last set's start, {last['from']}")
    lo, hi = (int(x) for x in a.blacks.split("-"))
    n = a.size
    layouts = [b for b in _all_layouts(n, a.max_len) if lo <= len(b) <= hi and _tidy(b, n)
               and len(entries(b, n)) >= a.min_clues
               and (not a.symmetric or b == {(n - 1 - r, n - 1 - c) for r, c in b})]
    print(f"{len(layouts)} layouts to fill, {a.seconds:g}s each, on {os.cpu_count()} cores")
    t0 = time.time()
    with Pool(os.cpu_count(), initializer=_setup, initargs=(a.hard, n)) as workers:
        grids = [g for out in workers.imap_unordered(_fill_layout, [(i, b, a.seconds) for i, b in enumerate(layouts)])
                 for g in out]
    pool = list({_sig(g): g for g in grids}.values())
    filled = len({_layout(g) for g in pool})
    print(f"{len(pool)} distinct grids from {filled} layouts in {time.time() - t0:.0f}s")

    before = last["puzzles"] if int(last.get("size", 5)) == n else []
    picked = _pick(pool, before, a.count, a.overlap, a.word_cap or max(2, a.count // 18), a.layout_uses)
    if len(picked) < a.count:
        raise SystemExit(f"only {len(picked)} fresh grids: give each layout longer (--seconds), "
                         f"or loosen --overlap / --word-cap / --layout-uses")
    bad, days = _arrange(picked, before)
    puzzles = [{"black": p["black"], "entries": p["entries"], "id": i + 1, "size": n} for i, p in enumerate(days)]

    hs = [sum(e["answer"] in VOCAB for e in p["entries"]) for p in puzzles]
    words = Counter(w for p in puzzles for w in _sig(p))
    end = start + datetime.timedelta(days=len(puzzles) - 1)
    print(f"{len(puzzles)} puzzles, {a.start} to {end}, in {len({_layout(p) for p in puzzles})} layouts")
    print(f"harder-tier answers per grid: mean {sum(hs) / len(hs):.2f}, fewest {min(hs)}")
    print(f"most-used words: {', '.join(f'{w} x{k}' for w, k in words.most_common(5))}")
    print(f"days that had to bend the spacing rules: {bad}")
    if a.dry_run:
        raise SystemExit(0)
    doc["sets"].append({"from": a.start, "size": n, "rewards": last.get("rewards"),
                        "wrong_per_tier": last.get("wrong_per_tier", 0), "max_hints": last.get("max_hints", 0),
                        "puzzles": puzzles})
    with open(a.file, "w", encoding="utf-8") as f:
        f.write(json.dumps(doc, indent=1))
    print(f"added to {a.file}")
