"""One pace for every tap race in the Bank Job (the vault's GRAB, a loud job's MASH GRAB, the Muscle's HEAVE), the
same as the page holds to (taps.ts in the page's repo, with the same numbers).

A finger tapping as fast as it can, six to ten taps a second, must never lose a tap, though its taps reach the bot
bunched up or spread out by the page's clock and the network. Two ways of tapping at once (the space bar and the
mouse, a doctored page's doubled burst) mustn't count double. So a tap counts unless it's within MIN_GAP of the last
one that counted (one press read twice), or the bucket's empty: it fills at RATE a second of tap time and holds
BURST, so a quick pair or a bunched run goes through and the average never goes over RATE. The old rule, nothing
within 70ms of the last one, cut a real thumb's taps whenever two of them landed close."""

MIN_GAP = 40        # ms: two taps closer than this are one press read twice
RATE = 12           # taps a second on average at most: more than any thumb keeps up
BURST = 4           # taps in hand over the rate


def paced(state: dict, t: float, rate: float = RATE, burst: float = BURST, min_gap: float = MIN_GAP,
          key: str = "pace") -> bool:
    """Whether a tap at time ``t`` (ms, in order) counts, keeping the pace's working in ``state[key]`` (JSON-able:
    [time of the last tap that counted, taps in hand]). Counting it takes one from the bucket."""
    last, have = state.get(key) or (None, float(burst))
    if last is not None:
        gap = t - last
        if gap < min_gap:
            return False
        have = min(float(burst), have + gap * rate / 1000.0)
    if have < 1.0:
        return False
    state[key] = [t, round(have - 1.0, 6)]
    return True
