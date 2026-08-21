"""Onboarding selfbot detection: the thresholds, and the ways the old rule was wrong.

The rule is only worth having if it fires on scripts and not on people, so the tests that
matter are the honest answers - one nationality, dual heritage picked slowly, a moderator
handing out a role - all of which have to stay quiet.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from lib.core import detection_rules as D

ENG, SCO, WAL, NIR = (config.ROLES.ENGLISH, config.ROLES.SCOTTISH,
                      config.ROLES.WELSH, config.ROLES.NORTHERN_IRISH)
BRITISH, COMMONWEALTH, VISITOR = (config.ROLES.BRITISH, config.ROLES.COMMONWEALTH,
                                  config.ROLES.VISITOR)
UNRELATED = 1234567890


def test_one_nationality_is_nobody_s_business():
    assert D.onboarding_findings({ENG, BRITISH}, 0.2) == []
    assert D.onboarding_findings({SCO, BRITISH}, 900) == []


def test_dual_and_triple_nationality_are_ordinary():
    """The whole point of the rewrite. Plenty of people are two or three of these, and a
    rule that treats that as evidence is worse than no rule at all."""
    assert D.onboarding_findings({ENG, SCO, BRITISH}, 45) == []
    assert D.onboarding_findings({ENG, SCO, WAL}, 600) == []
    assert D.onboarding_findings({SCO, NIR, BRITISH}, 30) == []


def test_taking_every_home_nation_is_not_an_answer():
    found = D.onboarding_findings({ENG, SCO, WAL, NIR}, 6000)
    assert found and "every home nation" in found[0], found
    assert "Northern Irish" in found[0]


def test_taking_every_status_role_is_not_an_answer():
    found = D.onboarding_findings({BRITISH, COMMONWEALTH, VISITOR}, 6000)
    assert found and "every status role" in found[0], found


def test_two_of_the_three_status_roles_is_left_alone():
    """British and Commonwealth together is odd, but odd is not automated."""
    assert D.onboarding_findings({BRITISH, COMMONWEALTH}, 6000) == []
    assert D.onboarding_findings({VISITOR, BRITISH}, 3000) == []


def test_several_roles_within_a_few_seconds_is_not_a_person():
    found = D.onboarding_findings({ENG, SCO, BRITISH}, 1.2)
    assert found, "instant picks went unflagged"
    assert "1.2s after joining" in found[0], found


def test_the_ordinary_path_done_quickly_is_still_fine():
    """One nationality plus one status is what everybody picks, and someone decisive gets
    through it fast. If that trips the rule, every joiner trips it."""
    assert D.onboarding_findings({ENG, BRITISH}, 0.2) == []
    assert D.onboarding_findings({VISITOR}, 0.1) == []
    assert D.onboarding_findings({ENG}, 0.3) == []


def test_the_same_three_picks_taken_slowly_are_fine():
    """Speed is the whole signal - the picks themselves are an ordinary dual-national answer."""
    assert D.onboarding_findings({ENG, SCO, BRITISH}, 45) == []


def test_taking_the_lot_reports_every_reason():
    found = D.onboarding_findings({ENG, SCO, WAL, NIR, BRITISH, COMMONWEALTH, VISITOR}, 0.3)
    assert len(found) == 3, f"expected nations, status and speed: {found}"


def test_roles_we_do_not_care_about_do_not_count_towards_speed():
    assert D.onboarding_findings({UNRELATED, ENG}, 0.1) == []


def test_an_unknown_join_time_does_not_count_as_instant():
    """joined_at can be missing. Absent evidence must not stand in for the fast-pick tell."""
    assert D.onboarding_findings({ENG, SCO}, None) == []
    assert D.onboarding_findings({ENG, SCO, WAL, NIR}, None), "the all-of-them rule still applies"


def test_a_member_is_only_alerted_on_once_per_window():
    D._onboarding_flagged.clear()
    assert D.claim_onboarding_flag(555) is True
    assert D.claim_onboarding_flag(555) is False, "the same member was alerted on twice"


def test_the_claim_expires_and_prunes_itself():
    """The old dict grew a permanent entry for every member whose roles ever changed."""
    D._onboarding_flagged.clear()
    ttl = D._cfg("ONBOARDING_FLAG_TTL_HOURS", 6) * 3600
    now = time.time()
    assert D.claim_onboarding_flag(777, now=now) is True
    assert D.claim_onboarding_flag(777, now=now + ttl + 1) is True, "the flag never expired"
    D.claim_onboarding_flag(888, now=now + ttl * 5)
    assert 777 not in D._onboarding_flagged, "expired entries were not pruned"


def test_releasing_a_claim_lets_a_later_real_one_through():
    """A moderator granting the role burns the check; handing the slot back means the
    member's own selection an hour later still gets seen."""
    D._onboarding_flagged.clear()
    assert D.claim_onboarding_flag(999) is True
    D.release_onboarding_flag(999)
    assert D.claim_onboarding_flag(999) is True


def test_the_delay_is_written_for_a_human_to_read():
    assert D.since_join(0.42) == "0.4s"
    assert D.since_join(180) == "3 min"
    assert D.since_join(7200) == "2 hours"
    assert D.since_join(172800) == "2 days"
    assert D.since_join(None) == "unknown"


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
            import traceback
            print(f"ERROR {name}: {e!r}")
            traceback.print_exc()
    print(f"\n{passed}/{len(tests)} passed")
    return passed == len(tests)


if __name__ == "__main__":
    sys.exit(0 if _run_all() else 1)
