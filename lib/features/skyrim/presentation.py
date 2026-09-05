"""Small, deterministic text helpers for Skyrim's narrow Discord panels."""

import re

SUMMARY_LIMIT = 700
DETAIL_LIMIT = 1400


def pages(text, limit=DETAIL_LIMIT):
    """Preserve every character while paging at natural line boundaries."""
    chunks = []
    rest = str(text).strip()
    while len(rest) > limit:
        cut = rest.rfind("\n", 0, limit + 1)
        if cut < limit // 3:
            cut = rest.rfind(" ", 0, limit + 1)
        if cut < 1:
            cut = limit
        chunks.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest:
        chunks.append(rest)
    return chunks or [""]


def outcome(lines, limit=185):
    """One readable action outcome; full mechanics and history live in Inspect."""
    if not lines:
        return ""
    text = str(lines[-1]).replace("**", "").replace("-# ", "")
    for mark in ("❤️", "🩸", "🖤", "💛"):
        text = re.sub(f"(?:{mark}){{2,}}", lambda m: f"{mark} {m.group().count(mark)}", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    sentence = re.split(r"(?<=[.!?])\s+", text)[0]
    if len(sentence) <= limit:
        return sentence
    return text[:limit - 1].rsplit(" ", 1)[0] + "…"


def summary(text, limit=SUMMARY_LIMIT):
    """A conservative fallback for secondary menus; full text stays inspectable.

    Prefer headings and the beginning of each section over a wall of lore. Keep
    the last paragraph (purchase feedback or a requirement) visible as well.
    Main game screens supply purpose-written summaries instead.
    """
    if len(text) <= limit:
        return text
    sections = str(text).split("\n\n")
    first = pages(sections[0], min(300, limit))[0]
    tail = sections[-1]
    if tail == sections[0] or len(tail) > 240:
        tail = ""
    middle = []
    budget = limit - len(first) - len(tail) - 75
    for section in sections[1:-1 if tail else None]:
        bit = section.split("\n", 2)
        candidate = "\n".join(bit[:2])
        if len(candidate) > 220:
            candidate = outcome([candidate], 220)
        if len(candidate) + 2 <= budget:
            middle.append(candidate)
            budget -= len(candidate) + 2
    result = "\n\n".join([first] + middle + ([tail] if tail else []))
    return result + "\n\n-# Inspect for the complete list and details."


def quantity(items):
    return sum(max(0, int(n)) for n in (items or {}).values())


def short_label(label, limit=24):
    """Keep button labels short enough for three controls on a narrow client."""
    aliases = {
        "Abandon and delve anew": "Replace adventure",
        "Hall of Legends - retirement awaits": "Hall of Legends",
        "Claim this week's favour": "Claim favour",
        "Retire them, forever": "Confirm retirement",
        "The deep way": "Deep route",
        "The safe way": "Safe route",
        "Return to your bout": "Open bout",
        "Return to your duel": "Open duel",
        "Descend the Soul Cairn": "Soul Cairn",
        "Back away slowly": "Back away",
        "About that cheese...": "Ask about cheese",
    }
    label = aliases.get(str(label), str(label))
    return label if len(label) <= limit else label[:limit - 1].rsplit(" ", 1)[0] + "…"
