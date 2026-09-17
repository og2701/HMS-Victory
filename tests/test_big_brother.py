"""Big Brother event: storage layer, tallies, panel text and the persistent components."""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def bb():
    import database

    if database.DatabaseManager._connection is not None:
        database.DatabaseManager._connection.close()
        database.DatabaseManager._connection = None
    original_db_file = database.DB_FILE
    tmpdir = tempfile.mkdtemp()
    database.DB_FILE = os.path.join(tmpdir, "test.db")

    from lib.features import big_brother as module

    module._tables_ready = False
    module.ensure_tables()
    yield module
    # Later test files lean on whatever connection was open before this one (some point at
    # a temp file an earlier test has already deleted), so restore the path and make sure
    # the schema exists there again rather than leaving them an empty database.
    database.DatabaseManager._connection.close()
    database.DatabaseManager._connection = None
    database.DB_FILE = original_db_file
    os.makedirs(os.path.dirname(os.path.abspath(original_db_file)), exist_ok=True)
    database.init_db()
    module._tables_ready = False


def test_housemates_add_evict_and_immunity(bb):
    assert bb.db_add_housemate(1) and bb.db_add_housemate(2) and bb.db_add_housemate(3)
    assert not bb.db_add_housemate(1)
    assert bb.housemates() == [1, 2, 3]
    assert bb.is_housemate(2)

    assert bb.db_toggle_immunity(3) is True
    assert bb.immune_ids() == {3}
    assert bb.db_toggle_immunity(3) is False

    bb.db_set_status(2, bb.STATUS_EVICTED)
    assert bb.housemates() == [1, 3]
    assert bb.housemates(bb.STATUS_EVICTED) == [2]
    assert not bb.is_housemate(2)
    # An evicted housemate can be put back in (host mis-click).
    assert bb.db_add_housemate(2)
    assert bb.housemates() == [1, 3, 2] or set(bb.housemates()) == {1, 2, 3}


def test_nominations_replace_and_tally(bb):
    for u in (1, 2, 3, 4):
        bb.db_add_housemate(u)
    rid = bb.create_round(bb.KIND_NOMINATIONS)
    assert bb.open_round(bb.KIND_NOMINATIONS)["id"] == rid

    bb.record_nominations(rid, 1, [2, 3])
    bb.record_nominations(rid, 2, [3, 1])
    # Re-nominating replaces the earlier pair rather than adding to it.
    bb.record_nominations(rid, 1, [3, 4])
    assert sorted(bb.nominations_for(rid)) == [(1, 3), (1, 4), (2, 1), (2, 3)]
    assert bb.nominators_done(rid) == {1, 2}

    bb.close_round(rid)
    assert bb.open_round(bb.KIND_NOMINATIONS) is None
    assert bb.get_round(rid)["status"] == "closed"


def test_votes_one_per_voter_and_tally(bb):
    vid = bb.create_round(bb.KIND_VOTE, nominees=[1, 3])
    assert bb.get_round(vid)["nominees"] == [1, 3]
    bb.cast_vote(vid, 10, 1)
    bb.cast_vote(vid, 11, 3)
    bb.cast_vote(vid, 10, 3)  # changed their mind
    assert bb.vote_tally(vid) == {3: 2}
    assert bb.vote_count(vid) == 2


def test_state_roundtrip(bb):
    bb.set_state("k", {"a": [1, 2]})
    assert bb.get_state("k") == {"a": [1, 2]}
    assert bb.get_state("missing", "d") == "d"
    bb.set_state("k", None)
    assert bb.get_state("k", "d") == "d"


def test_missions_and_challenges(bb):
    bb.db_add_housemate(2)
    mid = bb.add_mission(2, "say crumpet")
    assert bb.active_mission_for(2)["id"] == mid
    assert [m["id"] for m in bb.active_missions()] == [mid]
    assert bb.resolve_mission(mid, "done")["user_id"] == 2
    assert bb.active_missions() == []
    assert bb.resolve_mission(999, "done") is None

    cid = bb.add_challenge("Q", "2+2?", "Four", None)
    assert bb.open_challenge()["answer"] == "Four"
    assert bb._norm(" four! ") == bb._norm("Four")
    assert bb._norm("4") != bb._norm("Four")
    bb.close_challenge(cid, 3)
    assert bb.open_challenge() is None


def test_quiet_housemates_uses_last_message_or_join(bb, monkeypatch):
    bb.db_add_housemate(1)
    bb.db_add_housemate(2)
    assert bb.quiet_housemates() == []
    # Jump the clock past the quiet window: both are quiet, then 1 speaks.
    real_now = bb._now
    monkeypatch.setattr(bb, "_now", lambda: real_now() + bb.quiet_hours() * 3600 + 60)
    assert set(bb.quiet_housemates()) == {1, 2}
    bb.touch_activity(1)
    assert bb.quiet_housemates() == [2]


def test_panel_text_and_view_have_no_secrets(bb):
    for u in (1, 2, 3):
        bb.db_add_housemate(u)
    rid = bb.create_round(bb.KIND_NOMINATIONS)
    bb.record_nominations(rid, 1, [2, 3])
    text = bb._panel_text(None)
    assert "3 in the house" in text
    assert "1/3 have nominated" in text
    # Counts only: the nominee names never appear on the panel.
    assert "user 2" not in text and "user 3" not in text

    view = bb.BigBrotherControlView(None)
    assert view.timeout is None
    ids = [c.custom_id for row in view.children[0].children
           if hasattr(row, "children") for c in row.children]
    assert len(ids) == 17 and len(set(ids)) == 17
    assert set(ids) == {f"bb:ctl:{a}" for a in bb.PANEL_ACTIONS}
    # Rows stay short so buttons don't wrap mid-row on desktop.
    assert all(len(row.children) <= 3 for row in view.children[0].children if hasattr(row, "children"))


def test_house_panel_buttons_and_gating(bb):
    bb.db_add_housemate(1)
    bb.create_round(bb.KIND_NOMINATIONS)
    text = bb._house_panel_text(None)
    assert "Nominations are open" in text and "1** housemates remain" in text

    view = bb.HousePanelView(None)
    assert view.timeout is None
    ids = [c.custom_id for row in view.children[0].children
           if hasattr(row, "children") for c in row.children]
    assert set(ids) == {f"bb:house:{a}" for a in bb.HOUSE_ACTIONS}
    # The two registries never share a custom_id, so both views can be persistent at once.
    control_ids = {f"bb:ctl:{a}" for a in bb.PANEL_ACTIONS}
    assert not control_ids & set(ids)
    assert bb.HOUSE_PUBLIC_ACTIONS <= set(bb.HOUSE_ACTIONS)


def test_events_and_rundown(bb):
    bb.db_add_housemate(1)
    bb.db_add_housemate(2)
    bb.log_event("housemate_added", target=1)
    rid = bb.create_round(bb.KIND_NOMINATIONS)
    bb.record_nominations(rid, 1, [2])
    bb.log_event("nominated", actor=1, round_id=rid, nominees=[2], changed=False)
    bb.add_diary(2, "I trust nobody", True)
    bb.log_event("diary", actor=2, anonymous=True, text="I trust nobody")
    evs = bb.events()
    assert [e["kind"] for e in evs] == ["housemate_added", "nominated", "diary"]
    assert evs[1]["nominees"] == [2] and evs[1]["actor_id"] == 1

    dump, timeline = bb.build_rundown(None)
    assert {h["user_id"] for h in dump["housemates"]} == {1, 2}
    assert dump["nominations"][0]["nominee"] == 2
    assert dump["diary"][0]["anonymous"] is True
    assert "user 1 nominated user 2" in timeline
    assert "diary (anonymous): I trust nobody" in timeline
    import json
    json.dumps(dump)  # must be serialisable as-is


def test_tokens_immunity_and_swap(bb):
    for u in (1, 2, 3, 4):
        bb.db_add_housemate(u)
    assert bb.tokens_of(1) == 0
    assert not bb.spend_token(1)
    assert bb.grant_token(1) == 1
    assert bb.spend_token(1) and not bb.spend_token(1)

    bb.set_immune(2, True)
    assert bb.immune_ids() == {2}
    assert bb.clear_all_immunity() == [2]
    assert bb.immune_ids() == set()

    vid = bb.create_round(bb.KIND_VOTE, nominees=[1, 3])
    bb.cast_vote(vid, 10, 1)
    bb.cast_vote(vid, 11, 3)
    bb.replace_nominee(vid, 1, 4)
    assert bb.get_round(vid)["nominees"] == [4, 3]
    assert bb.vote_tally(vid) == {3: 1}  # votes for the swapped-out nominee are voided

    sid = bb.add_snug(555, 1, [1, 2])
    assert bb.snugs()[0] == {"id": sid, "thread_id": 555, "opened_by": 1, "members": [1, 2],
                             "created_at": bb.snugs()[0]["created_at"]}
    assert bb.recent_snug_by(1, 60) and not bb.recent_snug_by(2, 60)


def test_vote_button_custom_id(bb):
    btn = bb.VoteButton(7, 42, "Bob")
    assert btn.custom_id == "bb:vote:7:42"
    assert btn.item.label == "Bob"


def test_disabled_flag_blocks_everything(bb, monkeypatch):
    import config
    monkeypatch.setattr(config, "BIG_BROTHER_ENABLED", False)
    assert not bb.enabled()
