"""Generate HMS Crossword puzzles: backtracking fill for 5x5 minis, constrained to the
clued word bank in crossword_bank.py.

    python3 scripts/generate_crosswords.py data/words/crosswords.json

Every entry is guaranteed to have a clue because the fill can only use words the bank
already clues. Re-running appends nothing - it rewrites the file, so keep the old one if
you want to extend rather than replace.

Indexed by (length, position, letter) so candidate lookup is a set intersection rather
than a scan, and entries are chosen most-constrained-first, which is what makes the
interlocking 5x5 tractable at all.
"""
import random, sys, json, time
from collections import defaultdict
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from crossword_bank import BANK

import itertools

def _valid_layouts(n, max_len, want=200):
    """180-degree symmetric black-square layouts where every entry is 3..max_len long and
    every white cell is crossed by BOTH an across and a down entry. A cell reachable from
    only one direction is unfair - there's no second way to get at it."""
    cells = [(r, c) for r in range(n) for c in range(n)]
    mirror = lambda p: (n - 1 - p[0], n - 1 - p[1])

    def ents(black):
        out = []
        for r in range(n):
            c = 0
            while c < n:
                if (r, c) in black: c += 1; continue
                s0 = c
                while c < n and (r, c) not in black: c += 1
                if c - s0 >= 2: out.append(("across", [(r, x) for x in range(s0, c)]))
        for c in range(n):
            r = 0
            while r < n:
                if (r, c) in black: r += 1; continue
                s0 = r
                while r < n and (r, c) not in black: r += 1
                if r - s0 >= 2: out.append(("down", [(x, c) for x in range(s0, r)]))
        return out

    def ok(black):
        es = ents(black)
        if any(not (3 <= len(cs) <= max_len) for _k, cs in es):
            return False
        white = {p for p in cells if p not in black}
        if {c for k, cs in es if k == "across" for c in cs} != white: return False
        if {c for k, cs in es if k == "down" for c in cs} != white: return False
        start = next(iter(white)); seen = {start}; stack = [start]
        while stack:
            r, c = stack.pop()
            for d in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                q = (r + d[0], c + d[1])
                if q in white and q not in seen:
                    seen.add(q); stack.append(q)
        return seen == white

    out = []
    half = [p for p in cells if p < mirror(p)]
    for k in (4, 6, 8, 10, 12):
        for combo in itertools.combinations(half, k // 2):
            b = frozenset(combo) | frozenset(mirror(p) for p in combo)
            if len(b) == k and ok(b):
                out.append(b)
                if len(out) >= want:
                    return out
    return out

N = 5
PATTERNS = []

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

def fill(ents, rng, deadline, banned=frozenset()):
    grid, used = {}, set(banned)

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
            scored.append((len(cs), cs, e))
        scored.sort(key=lambda t: t[0])          # most constrained first
        _n, cs, e = scored[0]
        rest = [x for x in remaining if x is not e]
        rng.shuffle(cs)
        for w in cs[:25]:
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
        return grid if rec(list(ents)) else None
    except TimeoutError:
        return None

def build(seed, budget=2.0, banned=frozenset()):
    rng = random.Random(seed)
    pats = list(PATTERNS); rng.shuffle(pats)
    for pat in pats:
        ents = entries(pat, globals()['N'])
        if not all(len(c) >= 3 for _k, _n, c in ents):
            continue
        g = fill(ents, rng, time.time() + budget, banned)
        if g:
            words = [{"num": num, "dir": kind, "answer": (a := "".join(g[c] for c in cells)),
                      "clue": BANK[a], "cells": [list(c) for c in cells]}
                     for kind, num, cells in ents]
            return {"black": sorted([list(b) for b in pat]), "entries": words}
    return None

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Generate HMS Crossword puzzles.")
    ap.add_argument("out")
    ap.add_argument("--size", type=int, default=6, help="grid size (default 6)")
    ap.add_argument("--count", type=int, default=25)
    ap.add_argument("--max-len", type=int, default=6, help="longest entry the bank can fill")
    a = ap.parse_args()

    globals()["N"] = a.size
    PATTERNS[:] = _valid_layouts(a.size, a.max_len)
    print(f"{len(PATTERNS)} valid {a.size}x{a.size} layouts")
    if not PATTERNS:
        raise SystemExit("no layouts satisfy those constraints")

    made, sigs, seed = [], [], 0
    t0 = time.time()
    while len(made) < a.count and seed < 40000 and time.time() - t0 < 420:
        hot = set()
        for sg in sigs[-6:]:
            hot |= sg
        p = build(seed, banned=frozenset(hot)) or build(seed, banned=frozenset())
        seed += 1
        if not p:
            continue
        sig = set(e["answer"] for e in p["entries"])
        if any(len(sig & prev) > 3 for prev in sigs):
            continue
        sigs.append(sig)
        p["id"] = len(made) + 1
        p["size"] = a.size
        made.append(p)
        print(f"  {len(made):>2}/{a.count}  seed {seed-1:<5} {len(p['entries'])} clues  "
              f"{'/'.join(e['answer'] for e in p['entries'][:4])}...", flush=True)
    print(f"built {len(made)} in {time.time()-t0:.0f}s")
    json.dump(made, open(a.out, "w"), indent=1)
