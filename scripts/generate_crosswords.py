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

N = 5
PATTERNS = [
    # Staircase corners: entry lengths run 3-4-5-4-3 both ways, so the fill leans on the
    # 3s and 4s instead of demanding five interlocking 5-letter words. Fills in ms rather
    # than minutes, and reads like a normal mini.
    frozenset({(0, 0), (0, 1), (1, 0), (3, 4), (4, 3), (4, 4)}),
    frozenset({(0, 3), (0, 4), (1, 4), (3, 0), (4, 0), (4, 1)}),
    # Single-notch corners: 4-5-5-5-4.
    frozenset({(0, 0), (4, 4)}),
    frozenset({(0, 4), (4, 0)}),
    frozenset({(0, 0), (0, 4), (4, 0), (4, 4)}),
]

BY_LEN = defaultdict(set)
IDX = defaultdict(set)
for w in BANK:
    BY_LEN[len(w)].add(w)
    for i, ch in enumerate(w):
        IDX[(len(w), i, ch)].add(w)

def entries(black):
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
        ents = entries(pat)
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
    made, sigs, seed = [], [], 0
    t0 = time.time()
    while len(made) < 25 and seed < 20000 and time.time() - t0 < 420:
        # Soft diversity: bias each fill away from words already used, but allow a few
        # to repeat. A hard ban starves the search - the vocabulary that actually
        # interlocks in a 5x5 is far smaller than the bank's 1,600 words.
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
        made.append(p)
        print(f"  {len(made):>2}/25  seed {seed-1:<5} {len(p['entries'])} clues  "
              f"{'/'.join(e['answer'] for e in p['entries'][:4])}...", flush=True)
    print(f"built {len(made)} in {time.time()-t0:.0f}s")
    json.dump(made, open(sys.argv[1] if len(sys.argv) > 1 else "puzzles.json", "w"), indent=1)
