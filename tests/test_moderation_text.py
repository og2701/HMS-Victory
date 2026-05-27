from lib.core.moderation_text import (
    find_blocked_moderation_match,
    normalize_moderation_text,
)


def test_normalizes_cyrillic_homoglyph_bypass():
    text = "black piece of sh\u0456t n\u0456gg\u0435r"

    assert normalize_moderation_text(text) == "black piece of shit nigger"
    match = find_blocked_moderation_match(text)
    assert match is not None
    assert match.label == "racial slur"


def test_detects_spaced_punctuation_bypass():
    match = find_blocked_moderation_match("n.i.g.g.e.r")

    assert match is not None


def test_detects_zero_width_and_leetspeak_bypass():
    assert find_blocked_moderation_match("n\u200bi\u200bg\u200bg\u200be\u200br") is not None
    assert find_blocked_moderation_match("n!gg@") is not None


def test_avoids_common_false_positives():
    assert find_blocked_moderation_match("snigger") is None
    assert find_blocked_moderation_match("niggardly") is None
