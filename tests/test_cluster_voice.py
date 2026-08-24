"""The two voice checks: a join batch in one call, and a brand new member going straight in.

They sit at different strengths on purpose. The batch rule makes a claim and carries a ban
button, so most of its tests are the cases that must stay silent - joining a Discord because
your mates are already in a call is one of the commonest honest reasons anyone joins at all.
The fast-join rule only says "look at this", and its tests are mostly about it not being
dressed up as more than that.
"""
import datetime
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from commands.moderation import join_clusters as J

BATCH = [f"{i}" for i in range(100, 105)]          # five accounts made together
OUTSIDER = "900"


def _state(members=None, **extra):
    state = {
        "clusters": [{
            "created_from": 1700000000, "created_to": 1700000720, "spread": 720,
            "tight": True,
            "members": [{"user_id": u, "joined_at": 1700100000} for u in (members or BATCH)],
        }],
        "banned": [], "quarantined": [], "dismissed_ids": [],
    }
    state.update(extra)
    return state


def test_one_of_a_batch_in_a_call_is_nothing():
    """Somebody from a flagged batch using voice is not evidence of anything."""
    assert J.voice_cluster_finding(BATCH[0], [BATCH[0], OUTSIDER, "901"], _state()) is None


def test_a_member_of_no_batch_is_nothing():
    assert J.voice_cluster_finding(OUTSIDER, [OUTSIDER, "901", "902"], _state()) is None


def test_a_busy_channel_of_ordinary_people_is_nothing():
    """The channel being full is not the signal - it has to be the same batch."""
    crowd = [str(n) for n in range(900, 930)] + [BATCH[0]]
    assert J.voice_cluster_finding(BATCH[0], crowd, _state()) is None


def test_two_from_one_batch_in_the_same_call_is_the_finding():
    found = J.voice_cluster_finding(BATCH[0], [BATCH[0], BATCH[1], OUTSIDER], _state())
    assert found, "two of a batch in one call went unreported"
    assert found["together"] == [BATCH[0], BATCH[1]]
    assert found["size"] == len(BATCH), "the alert should say how big the batch is"
    assert found["key"] == 1700000000, "the key has to match the batch's ban button"


def test_only_the_ones_actually_in_the_call_count():
    found = J.voice_cluster_finding(BATCH[0], BATCH[:3] + [OUTSIDER], _state())
    assert found["together"] == BATCH[:3]
    assert OUTSIDER not in found["together"]


def test_accounts_already_banned_do_not_prop_up_a_finding():
    """Otherwise the last one left in the call keeps re-reporting a batch already dealt
    with, every time somebody hops between channels."""
    state = _state(banned=BATCH[1:])
    assert J.voice_cluster_finding(BATCH[0], BATCH, state) is None


def test_a_batch_cleared_as_not_a_raid_stays_quiet():
    state = _state(dismissed_ids=BATCH)
    assert J.voice_cluster_finding(BATCH[0], BATCH, state) is None


def test_a_batch_only_partly_cleared_can_still_be_flagged():
    """Dismissal covers the accounts it was pressed on. Two later arrivals from the same
    registration run are new information."""
    state = _state(dismissed_ids=BATCH[3:])
    found = J.voice_cluster_finding(BATCH[0], BATCH, state)
    assert found and found["together"] == BATCH[:3]


def test_the_alert_names_the_batch_and_offers_its_ban_button():
    state = _state()
    found = J.voice_cluster_finding(BATCH[0], BATCH[:2], state)
    embed = J._voice_embed(found, _Channel(), state)
    assert "12 min" in embed.description, f"the registration spread is the evidence: {embed.description}"
    assert f"<@{BATCH[0]}>" in embed.description
    ids = [c.item.custom_id for c in J._voice_view(found, state).children]
    assert "joincluster:ban:1700000000" in ids, ids


class _Channel:
    id = 555
    mention = "<#555>"
    members = []


def test_a_stale_sighting_is_forgotten_rather_than_kept_forever():
    path = tempfile.mktemp(suffix=".json")
    J.VOICE_SIGHTINGS_FILE = path
    try:
        now = int(time.time())
        J._save_sightings({
            "old:1": {"ids": BATCH, "message_id": 1, "at": now - J.VOICE_SIGHTING_TTL - 60},
            "new:1": {"ids": BATCH, "message_id": 2, "at": now},
        })
        kept = {k: v for k, v in J._load_sightings().items()
                if now - int(v.get("at", 0)) < J.VOICE_SIGHTING_TTL}
        assert list(kept) == ["new:1"], kept
    finally:
        os.path.exists(path) and os.remove(path)


# --- straight from joining into a call ---------------------------------------------------

class _Member:
    def __init__(self, seconds_ago, bot=False, uid=777):
        self.id = uid
        self.bot = bot
        self.mention = f"<@{uid}>"
        self.joined_at = (None if seconds_ago is None else
                          datetime.datetime.now(datetime.timezone.utc)
                          - datetime.timedelta(seconds=seconds_ago))
        self.created_at = datetime.datetime.now(datetime.timezone.utc)


def test_a_brand_new_member_in_a_call_is_inside_the_window():
    assert J._fast_join_seconds(_Member(30)) < J.VOICE_RUSH_SECONDS


def test_somebody_who_has_been_here_a_while_is_not():
    assert J._fast_join_seconds(_Member(4 * 3600)) > J.VOICE_RUSH_SECONDS


def test_a_missing_join_time_is_not_treated_as_instant():
    """joined_at can be absent on an uncached member. Absent evidence is not the tell."""
    assert J._fast_join_seconds(_Member(None)) is None


def test_the_note_offers_no_ban_button():
    """Joining and using voice is not a banning offence, and a button that is there gets
    pressed. Timeout and analyse are the honest options."""
    from lib.core.mod_actions import (
        ModAnalyseButton, ModBanButton, ModIgnoreButton, ModTimeoutButton, VOICE_RUSH,
        action_view,
    )
    view = action_view(VOICE_RUSH, 7,
                       only=(ModTimeoutButton, ModAnalyseButton, ModIgnoreButton))
    assert not any(isinstance(c, ModBanButton) for c in view.children)
    assert len(view.children) == 3


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
