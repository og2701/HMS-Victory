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


def test_dual_heritage_picked_at_human_speed_is_left_alone():
    """English and Scottish is a real answer. The old rule would not have caught it either,
    but only because it demanded all four - this one has to leave it deliberately."""
    assert D.onboarding_findings({ENG, SCO, BRITISH}, 45) == []


def test_dual_heritage_in_under_a_second_is_not_a_person():
    found = D.onboarding_findings({ENG, SCO, BRITISH}, 0.4)
    assert found, "instant contradictory picks went unflagged"
    assert "0.4s" in found[0], found


def test_three_home_nations_is_flagged_however_slowly_it_was_done():
    """The hole in the old rule: it needed all four, so picking three sailed through."""
    found = D.onboarding_findings({ENG, SCO, WAL}, 6000)
    assert found and "3 home nations" in found[0], found


def test_all_four_still_reads_clearly():
    found = D.onboarding_findings({ENG, SCO, WAL, NIR}, 1.0)
    assert found and "4 home nations" in found[0], found
    assert "Northern Irish" in found[0]


def test_status_roles_that_answer_the_same_question_are_flagged():
    found = D.onboarding_findings({VISITOR, BRITISH}, 3000)
    assert found and "Visitor" in found[0] and "British" in found[0], found


def test_one_status_role_is_fine():
    assert D.onboarding_findings({VISITOR}, 1.0) == []
    assert D.onboarding_findings({BRITISH, ENG}, 1.0) == []


def test_both_problems_are_reported_separately():
    found = D.onboarding_findings({ENG, SCO, WAL, NIR, BRITISH, COMMONWEALTH, VISITOR}, 0.3)
    assert len(found) == 2, f"expected the nations and the status clash: {found}"


def test_roles_we_do_not_care_about_are_ignored():
    assert D.onboarding_findings({UNRELATED, ENG}, 0.1) == []


def test_an_unknown_join_time_does_not_count_as_instant():
    """joined_at can be missing. Absent evidence must not stand in for the fast-pick tell."""
    assert D.onboarding_findings({ENG, SCO}, None) == []
    assert D.onboarding_findings({ENG, SCO, WAL}, None), "the count rule still applies"


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
