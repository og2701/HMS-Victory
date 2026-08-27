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
    assert find_blocked_moderation_match("Pakistan") is None
    assert find_blocked_moderation_match("Pakistani politics") is None
    assert find_blocked_moderation_match("raccoon") is None
    assert find_blocked_moderation_match("despicable") is None
    assert find_blocked_moderation_match("conspicuous") is None
    assert find_blocked_moderation_match("spicy food") is None
    assert find_blocked_moderation_match("flame retardant") is None
    assert find_blocked_moderation_match("having a fag outside") is None
    assert find_blocked_moderation_match("chin up") is None
    assert find_blocked_moderation_match("custard tart") is None
    assert find_blocked_moderation_match("geological dike") is None
    assert find_blocked_moderation_match("tardy arrival") is None


def test_detects_expanded_slurs():
    # Additional racial slurs
    paki_match = find_blocked_moderation_match("you p.a.k.i")
    assert paki_match is not None and paki_match.label == "racial slur"

    kike_match = find_blocked_moderation_match("shut up k!ke")
    assert kike_match is not None and kike_match.label == "racial slur"

    chink_match = find_blocked_moderation_match("dirty ch!nk")
    assert chink_match is not None and chink_match.label == "racial slur"

    gook_match = find_blocked_moderation_match("filthy g00k")
    assert gook_match is not None and gook_match.label == "racial slur"

    spic_match = find_blocked_moderation_match("you sp!ck")
    assert spic_match is not None and spic_match.label == "racial slur"

    coon_match = find_blocked_moderation_match("dirty c00n")
    assert coon_match is not None and coon_match.label == "racial slur"

    # Homophobic slurs
    faggot_match = find_blocked_moderation_match("f.a.g.g.o.t")
    assert faggot_match is not None and faggot_match.label == "homophobic slur"

    faggot_leet = find_blocked_moderation_match("f@gg0t")
    assert faggot_leet is not None and faggot_leet.label == "homophobic slur"

    dyke_match = find_blocked_moderation_match("d y k e")
    assert dyke_match is not None and dyke_match.label == "homophobic slur"

    # Transphobic slurs
    shemale_match = find_blocked_moderation_match("shem@le")
    assert shemale_match is not None and shemale_match.label == "transphobic slur"

    # Verifying removed terms do not trigger automated filter
    assert find_blocked_moderation_match("retard") is None
    assert find_blocked_moderation_match("tranny") is None


# --- words that are only slurs in some contexts ---------------------------------------
# "or dick van dyke" earned somebody a 24 hour automated timeout for a homophobic slur.
# On a British server the collisions are not exotic: Offa's Dyke and Devil's Dyke are
# landmarks and Dyke Road is in Brighton.
def test_dick_van_dyke_is_not_a_slur():
    assert find_blocked_moderation_match("or dick van dyke") is None
    assert find_blocked_moderation_match("Dick Van Dyke") is None
    assert find_blocked_moderation_match("he grew a van dyke beard") is None


def test_british_landmarks_and_streets_are_not_slurs():
    for text in ("walked Offa's Dyke last summer", "Devils Dyke is near Brighton",
                 "the shop on Dyke Road", "we took the dyke path"):
        assert find_blocked_moderation_match(text) is None, text


def test_the_slur_itself_is_still_caught():
    for text in ("dyke", "you dyke", "dykes", "d y k e", "d.y.k.e"):
        m = find_blocked_moderation_match(text)
        assert m is not None and m.label == "homophobic slur", text


def test_an_exempt_hit_does_not_hide_a_real_one():
    """search() would stop at the exempted match and pass the message. finditer keeps
    looking, which is the whole reason the exemption is safe to have."""
    m = find_blocked_moderation_match("meet me on dyke road you faggot")
    assert m is not None and m.label == "homophobic slur"


def test_the_report_says_normalised_not_normalized():
    """The bot's own americanisms filter corrects normalize -> normalise, so it was pulling
    members up for writing exactly what it wrote itself."""
    import pathlib as _p
    src = _p.Path("lib/bot/event_handlers.py").read_text()
    assert '"Normalised message"' in src
    assert "Normalized message" not in src

