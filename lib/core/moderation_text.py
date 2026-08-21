import re
import unicodedata
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ModerationMatch:
    label: str
    matched_text: str
    normalized_text: str


_CONFUSABLES = str.maketrans(
    {
        "а": "a",
        "ɑ": "a",
        "α": "a",
        "о": "o",
        "ο": "o",
        "с": "c",
        "ϲ": "c",
        "е": "e",
        "ε": "e",
        "і": "i",
        "ι": "i",
        "ı": "i",
        "ӏ": "i",
        "ѕ": "s",
        "р": "p",
        "ρ": "p",
        "х": "x",
        "χ": "x",
        "у": "y",
        "γ": "y",
        "к": "k",
        "κ": "k",
        "м": "m",
        "т": "t",
        "τ": "t",
        "н": "h",
        "η": "n",
        "п": "n",
        "г": "r",
        "β": "b",
        "μ": "u",
        "ν": "v",
        "!": "i",
        "|": "i",
        "@": "a",
        "$": "s",
    }
)

_ZERO_WIDTH = {
    "\u200b",
    "\u200c",
    "\u200d",
    "\ufeff",
    "\u2060",
}

_BLOCKED_PATTERNS = [
    (
        "racial slur",
        re.compile(
            r"(?<![a-z0-9])"
            r"(?:"
            r"n+[\s._-]*[i1!|]+[\s._-]*g+[\s._-]*g+[\s._-]*(?:[e3]+[\s._-]*r+|[a4@]+)"
            r"|p+[\s._-]*[a4@]+[\s._-]*k+[\s._-]*[i1!|]+(?:[\s._-]*s+)?"
            r"|k+[\s._-]*[i1!|y]+[\s._-]*k+[\s._-]*[e3]+(?:[\s._-]*s+)?"
            r"|c+[\s._-]*h+[\s._-]*[i1!|]+[\s._-]*n+[\s._-]*k+(?:[\s._-]*[y|i1!|]+|[\s._-]*s+)?"
            r"|g+[\s._-]*[o0]{2,}[\s._-]*k+(?:[\s._-]*s+)?"
            r"|s+[\s._-]*p+[\s._-]*[i1!|]+[\s._-]*c+(?:k+)?(?:[\s._-]*s+)?"
            r"|c+[\s._-]*[o0]{2,}[\s._-]*n+(?:[\s._-]*s+)?"
            r")"
            r"(?![a-z0-9])"
        ),
    ),
    (
        "homophobic slur",
        re.compile(
            r"(?<![a-z0-9])"
            r"(?:"
            r"f+[\s._-]*[a4@]+[\s._-]*g+[\s._-]*(?:g+[\s._-]*)?[o0e3i1!|]+[\s._-]*t+(?:[\s._-]*s+|[\s._-]*r+[\s._-]*y+)?"
            r"|d+[\s._-]*y+[\s._-]*k+[\s._-]*[e3]+(?:[\s._-]*s+)?"
            r")"
            r"(?![a-z0-9])"
        ),
    ),
    (
        "transphobic slur",
        re.compile(
            r"(?<![a-z0-9])"
            r"s+[\s._-]*h+[\s._-]*[e3]+[\s._-]*m+[\s._-]*[a4@]+[\s._-]*l+[\s._-]*[e3]+(?:[\s._-]*s+)?"
            r"(?![a-z0-9])"
        ),
    ),
]


def normalize_moderation_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text.casefold()).translate(_CONFUSABLES)
    chars = []
    for char in normalized:
        if char in _ZERO_WIDTH or unicodedata.category(char).startswith("M"):
            continue
        chars.append(char if char.isalnum() else " ")
    return re.sub(r"\s+", " ", "".join(chars)).strip()


def find_blocked_moderation_match(text: str) -> Optional[ModerationMatch]:
    normalized = normalize_moderation_text(text)
    for label, pattern in _BLOCKED_PATTERNS:
        match = pattern.search(normalized)
        if match:
            return ModerationMatch(
                label=label,
                matched_text=match.group(0),
                normalized_text=normalized,
            )
    return None
