"""Which flags actually do something when you react with them.

Reacting with an unmapped flag is a silent no-op - no message, no log, no error. Four
people reacted with 🇪🇦 on the same message and nothing happened, and it took reading the
raw message payload to find out why: 🇪🇦 is Ceuta & Melilla, not Spain, and the two are
almost indistinguishable at the size Discord renders them.
"""
import os
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.core.constants import FLAG_LANGUAGE_MAPPINGS as M


def test_the_lookalike_flags_are_mapped():
    """🇪🇦 next to 🇪🇸 is the trap that started this."""
    assert M.get("🇪🇦"), "Ceuta & Melilla still does nothing"
    assert M["🇪🇦"] == M["🇪🇸"], "the two Spain flags should behave the same"
    assert M.get("🇵🇷") == "Spanish"


def test_the_spain_flags_ask_for_castellano_and_the_others_do_not():
    """Peninsular Spanish out of the Mexican flag would be the wrong joke."""
    assert M["🇪🇸"].startswith("Castellano") and "vosotros" in M["🇪🇸"]
    for latam in ("🇲🇽", "🇦🇷", "🇨🇴", "🇵🇷", "🇨🇱"):
        assert M[latam] == "Spanish", f"{latam} should not be peninsular"


def test_the_spanish_speaking_world_is_covered():
    for flag in ("🇪🇸", "🇲🇽", "🇦🇷", "🇨🇴", "🇨🇱", "🇵🇪", "🇻🇪", "🇧🇴", "🇺🇾",
                 "🇵🇾", "🇬🇹", "🇭🇳", "🇳🇮", "🇵🇦", "🇸🇻", "🇨🇷", "🇩🇴", "🇨🇺", "🇪🇦", "🇵🇷"):
        target = M.get(flag, "")
        assert "Spanish" in target or target.startswith("Castellano"), \
            f"{flag} is unmapped or is not Spanish of some kind"


def test_every_value_is_a_non_empty_instruction():
    """An empty or None value fails the `if not target_language` check and is the same
    silent no-op as being missing."""
    for flag, target in M.items():
        assert isinstance(target, str) and target.strip(), flag


def test_the_keys_really_are_flags_or_deliberate_style_picks():
    style = {"🏴‍☠️", "🤓", "🥷", "🎩", "🏰", "🦴", "🎭"}
    for key in M:
        if key in style:
            continue
        # regional indicator pairs, or the tag-sequence flags for the home nations
        first = unicodedata.name(key[0], "")
        assert "REGIONAL INDICATOR" in first or "WAVING BLACK FLAG" in first, \
            f"{key!r} is neither a flag nor a listed style"


def test_no_flag_maps_to_a_bare_country_name():
    """The value is fed to the model as a target language, so 'Brazil' would ask it to
    translate into a country."""
    for flag, target in M.items():
        assert target not in {"Spain", "Brazil", "France", "Germany", "Japan"}, flag


def _run_all():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL  {name}: {e}")
        except Exception as e:
            print(f"ERROR {name}: {e!r}")
    print(f"\n{passed}/{len(tests)} passed")
    return passed == len(tests)


if __name__ == "__main__":
    sys.exit(0 if _run_all() else 1)
