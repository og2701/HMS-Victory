"""The most anyone could score in Spitfire or Climb HMS Victory in a given time, flying or
climbing perfectly through that day's level.

Both games build their level from the day's seed with the same small random (the page's rng()),
so the bot can build it too. Spitfire's score is just how many gaps you've flown through, and the
plane's speed depends only on the score, so the most a run can have scored after t seconds is
exact. Climb's height depends on which spars you land on; the most is found by searching the
rigging for the quickest way up, letting the sailor steer anywhere (which no real climb can), so
it's a ceiling a real climb stays under.

The level code here mirrors src/spitfire/game.ts and src/climb/game.ts in ukplace-activities:
change either and change this too, or honest runs could be cut down.
"""

from __future__ import annotations

import bisect
import heapq
import math
from functools import lru_cache

MASK = 0xFFFFFFFF


def _imul(a: int, b: int) -> int:
    return (a * b) & MASK


def rng(seed: str):
    """The page's seeded random (a 32-bit hash of the seed into mulberry32)."""
    h = (1779033703 ^ len(seed)) & MASK
    for ch in seed:
        h = _imul(h ^ ord(ch), 3432918353)
        h = ((h << 13) | (h >> 19)) & MASK
    a = h

    def r() -> float:
        nonlocal a
        a = (a + 0x6D2B79F5) & MASK
        t = _imul(a ^ (a >> 15), 1 | a)
        t = ((t + _imul(t ^ (t >> 7), 61 | t)) & MASK) ^ t
        return ((t ^ (t >> 14)) & MASK) / 4294967296

    return r


# ---- Spitfire -----------------------------------------------------------------------------
SP_STEP = 1 / 120
SP_FIRST = 560


def spitfire_gates(seed: str, count: int) -> list[float]:
    """Where the first `count` gaps are (in the order the page draws its random numbers)."""
    r = rng(f"barrage:{seed}")
    xs, x = [], float(SP_FIRST)
    for n in range(count):
        d = 1 - math.exp(-n / 45)
        r()                         # where the gap sits
        if n >= 20:
            r()                     # whether it bobs
        r()                         # the wire's sway
        r()                         # the bob's phase
        xs.append(x)
        x += 250 - 50 * d + r() * 30
    return xs


@lru_cache(maxsize=8)
def _spitfire_times(seed: str, most: int = 1000) -> tuple[float, ...]:
    """When each point comes, flying flat out from take-off."""
    xs = spitfire_gates(seed, most + 1)
    dist, score, speed, step, times = 0.0, 0, 165.0, 0, []
    while score < most and step < 120 * 3600:
        step += 1
        dist += speed * SP_STEP
        speed = 165 + 85 * (1 - math.exp(-score / 50))
        while score < most and dist > xs[score] + 34:
            score += 1
            times.append(step * SP_STEP)
    return tuple(times)


def spitfire_max(seed: str, seconds: float) -> int:
    """The most gaps anyone could have flown through `seconds` after take-off."""
    return bisect.bisect_right(_spitfire_times(seed), seconds)


# ---- Climb HMS Victory --------------------------------------------------------------------
PER_METRE = 50
GRAVITY = 1900
JUMP, SPRING, BOOST, BOOST_TIME = 930, 1480, 2100, 1.25
CLIMB_TOP = 2000 * PER_METRE        # the score ceiling, in world units


def climb_footholds(seed: str, up_to: float = CLIMB_TOP + 4000) -> list[tuple[float, str]]:
    """Everything the sailor can bounce off, as (height, what it can launch them with): the deck,
    the spars (some with a sail or a cannon, which only fire if you land near the middle), and the
    gulls. Rotten spars break, so they're left out."""
    r = rng(f"rigging:{seed}")
    out: list[tuple[float, str]] = [(0.0, "jump")]
    next_y = 0.0
    while next_y < up_to:
        metres = next_y / PER_METRE
        d = min(1.0, metres / 600)
        gap = (48 + 130 * d) * (0.55 + 0.45 * r()) + 14
        next_y += gap
        y = next_y
        roll = r()
        move = metres > 15 and roll < 0.06 + 0.32 * d
        r()                         # x
        r()                         # phase
        if move:
            r()                     # range
            r()                     # speed
        extra = r()
        launch = "sail" if metres > 8 and extra < 0.07 else "cannon" if metres > 60 and extra > 0.988 else "jump"
        out.append((y, launch))
        if metres > 25 and r() < 0.1 + 0.22 * d:
            r()                     # the rotten spar's x
            r()                     # and its height
        if metres > 70 and r() < 0.035 + 0.06 * d:
            r()                     # which way the gull flies
            r()                     # x
            gull_y = y + 70 + r() * 60
            r()                     # speed
            r()                     # wingbeat
            out.append((gull_y + 4, "jump"))      # a gull can be stamped on from just above it
    out.sort()
    return out


LAUNCHES = {"jump": ("jump",), "sail": ("jump", "sail"), "cannon": ("jump", "cannon")}


def _flight(launch: str) -> tuple[float, float]:
    """(how high a launch carries you, how long it takes to get there)."""
    if launch == "cannon":
        rise = BOOST * BOOST_TIME + BOOST * BOOST / (2 * GRAVITY)
        return rise, BOOST_TIME + BOOST / GRAVITY
    v = SPRING if launch == "sail" else JUMP
    return v * v / (2 * GRAVITY), v / GRAVITY


def _risen(launch: str, t: float) -> float:
    """How far above the foothold you are `t` seconds after it launched you (on the way up)."""
    if launch == "cannon":
        if t <= BOOST_TIME:
            return BOOST * t
        t -= BOOST_TIME
        return BOOST * BOOST_TIME + min(BOOST * t - GRAVITY * t * t / 2, BOOST * BOOST / (2 * GRAVITY))
    v = SPRING if launch == "sail" else JUMP
    t = min(t, v / GRAVITY)
    return v * t - GRAVITY * t * t / 2


@lru_cache(maxsize=8)
def _climb_arrivals(seed: str) -> tuple[tuple[float, float, str], ...]:
    """The soonest each foothold can be landed on, as (time, height, launch), by the quickest
    route up: from each one, any foothold the launch carries you over can be landed on as you
    come back down (steering assumed perfect)."""
    holds = climb_footholds(seed)
    ys = [y for y, _ in holds]
    best = [math.inf] * len(holds)
    best[0] = -JUMP / GRAVITY          # on the deck behind the title, maybe already at the top of a bounce
    queue = [(best[0], 0)]
    while queue:
        t, i = heapq.heappop(queue)
        if t > best[i]:
            continue
        y, kind = holds[i]
        for launch in LAUNCHES[kind]:
            rise, up = _flight(launch)
            lo = bisect.bisect_left(ys, y - JUMP * JUMP / GRAVITY)
            hi = bisect.bisect_right(ys, y + rise)
            for j in range(lo, hi):
                if j == i:
                    continue
                arrive = t + up + math.sqrt(2 * (y + rise - ys[j]) / GRAVITY)
                if arrive < best[j]:
                    best[j] = arrive
                    heapq.heappush(queue, (arrive, j))
    return tuple((best[i], y, kind) for i, (y, kind) in enumerate(holds) if best[i] < math.inf)


def climb_max(seed: str, seconds: float) -> int:
    """The most metres anyone could have climbed `seconds` after starting."""
    top = 0.0
    for t, y, kind in _climb_arrivals(seed):
        if t <= seconds:
            top = max(top, y + max(_risen(launch, seconds - t) for launch in LAUNCHES[kind]))
    return int(top // PER_METRE)
