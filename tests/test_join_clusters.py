"""Batch-creation clustering: accounts made together that now arrive together."""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from commands.moderation import join_clusters as JC

NOW = 1_760_000_000


def rec(uid, created_at, joined_ago=60, name=None):
    return {"user_id": str(uid), "username": name or f"user{uid}",
            "joined_at": NOW - joined_ago, "account_created_at": created_at}


def test_accounts_created_minutes_apart_are_clustered():
    base = NOW - 120 * 86400          # three months old, like the real ones
    records = [rec(1, base), rec(2, base + 260), rec(3, base + 900), rec(4, base + 1500)]
    clusters = JC.find_clusters(records, now=NOW)
    assert len(clusters) == 1
    assert len(clusters[0]["members"]) == 4
    assert clusters[0]["spread"] == 1500


def test_a_paused_script_is_still_one_batch():
    """Real batch: 01:49, 01:53, 02:03, 02:35 - every step small, 46m end to end.
    Measuring from the first account would drop the last one."""
    base = NOW - 120 * 86400
    records = [rec(1, base), rec(2, base + 263), rec(3, base + 891), rec(4, base + 2768)]
    clusters = JC.find_clusters(records, now=NOW)
    assert len(clusters) == 1
    assert len(clusters[0]["members"]) == 4


def test_a_long_gap_starts_a_new_batch():
    """Two runs hours apart on the same day are separate registrations."""
    base = NOW - 120 * 86400
    records = [rec(1, base), rec(2, base + 120), rec(3, base + 300),
               rec(4, base + 23000), rec(5, base + 23100), rec(6, base + 23300)]
    assert len(JC.find_clusters(records, now=NOW)) == 2


def test_a_scripted_spread_is_marked_as_such():
    base = NOW - 90 * 86400
    tight = JC.find_clusters([rec(1, base), rec(2, base + 60), rec(3, base + 120)], now=NOW)
    assert tight[0]["tight"] is True and tight[0]["max_gap"] == 60
    loose = JC.find_clusters([rec(1, base), rec(2, base + 600), rec(3, base + 1500)], now=NOW)
    assert loose[0]["tight"] is False


def test_unrelated_accounts_are_not_a_cluster():
    """Ordinary joiners have creation dates scattered over years."""
    records = [rec(1, NOW - 900 * 86400), rec(2, NOW - 400 * 86400),
               rec(3, NOW - 30 * 86400), rec(4, NOW - 5 * 86400)]
    assert JC.find_clusters(records, now=NOW) == []


def test_two_accounts_alone_are_never_a_cluster():
    """Two friends signing up together is a real thing and must not trip this."""
    base = NOW - 200 * 86400
    assert JC.find_clusters([rec(1, base), rec(2, base + 30)], now=NOW) == []


def test_old_joins_fall_out_of_the_window():
    base = NOW - 200 * 86400
    stale = JC.JOIN_WINDOW_SECONDS + 3600
    records = [rec(1, base, joined_ago=stale), rec(2, base + 60, joined_ago=stale),
               rec(3, base + 120, joined_ago=stale)]
    assert JC.find_clusters(records, now=NOW) == []


def test_separate_batches_are_reported_separately():
    feb = NOW - 190 * 86400
    apr = NOW - 120 * 86400
    records = [rec(1, feb), rec(2, feb + 100), rec(3, feb + 200),
               rec(4, apr), rec(5, apr + 100), rec(6, apr + 200)]
    clusters = JC.find_clusters(records, now=NOW)
    assert len(clusters) == 2
    assert {len(c["members"]) for c in clusters} == {3}


def test_the_id_list_is_deduped_and_ordered():
    base = NOW - 150 * 86400
    clusters = JC.find_clusters([rec(7, base), rec(8, base + 60), rec(9, base + 90)], now=NOW)
    assert JC.cluster_user_ids(clusters) == ["7", "8", "9"]


def test_the_card_shows_the_ids_and_offers_no_automatic_action():
    base = NOW - 150 * 86400
    clusters = JC.find_clusters([rec(1, base), rec(2, base + 60), rec(3, base + 90)], now=NOW)
    import json
    payload = json.dumps(JC.build_cluster_view(clusters).to_components())
    for uid in ("1", "2", "3"):
        assert f"`{uid}`" in payload
    # The destructive control is present but it is a button, not something already done.
    assert "joincluster:massban" in payload
    assert "not proof" in payload, "the card must caveat the evidence"


def test_a_banned_report_drops_the_ban_button():
    """Once actioned the card must not offer to ban the same people again."""
    base = NOW - 150 * 86400
    clusters = JC.find_clusters([rec(1, base), rec(2, base + 60), rec(3, base + 90)], now=NOW)
    import json
    payload = json.dumps(JC.build_cluster_view(clusters, banned=["1", "2", "3"]).to_components())
    assert "joincluster:massban" not in payload
    assert "banned" in payload


def test_staff_check_rejects_ordinary_members():
    class R:
        def __init__(self, rid): self.id = rid

    class U:
        def __init__(self, roles): self.roles = roles

    assert JC._is_staff(U([R(next(iter(JC.STAFF_ROLE_IDS)))])) is True
    assert JC._is_staff(U([R(1)])) is False
    assert JC._is_staff(U([])) is False


def test_one_member_rejoining_is_never_a_cluster():
    """A leave-and-rejoin wrote a record each time, so one person read as several accounts
    created 0s apart - which is exactly the shape flagged as scripted."""
    base = NOW - 5 * 86400
    same = [rec(1, base, joined_ago=600), rec(1, base, joined_ago=400),
            rec(1, base, joined_ago=200), rec(1, base, joined_ago=60)]
    assert JC.find_clusters(same, now=NOW) == []


def test_duplicates_do_not_inflate_a_real_cluster():
    base = NOW - 120 * 86400
    records = [rec(1, base), rec(1, base, joined_ago=30), rec(1, base, joined_ago=10),
               rec(2, base + 120), rec(3, base + 300), rec(3, base + 300, joined_ago=5)]
    clusters = JC.find_clusters(records, now=NOW)
    assert len(clusters) == 1
    assert len(clusters[0]["members"]) == 3
    # Ordered by join time, not id - that is the order the card lists them in.
    assert sorted(JC.cluster_user_ids(clusters)) == ["1", "2", "3"]


def test_the_latest_join_is_the_one_kept():
    base = NOW - 100 * 86400
    records = [rec(1, base, joined_ago=9000), rec(1, base, joined_ago=60),
               rec(2, base + 60), rec(3, base + 120)]
    clusters = JC.find_clusters(records, now=NOW)
    member = next(m for m in clusters[0]["members"] if m["user_id"] == "1")
    assert member["joined_at"] == NOW - 60
