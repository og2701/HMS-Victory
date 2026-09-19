"""Big Brother event: storage layer, tallies, panel text and the persistent components."""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def shop(bb):
    from lib.features import big_brother_shop as module
    module._tables_ready = False
    module.ensure_tables()
    return module


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
    from lib.features import big_brother_shop as shop_module
    from lib.features import big_brother_teams as teams_module

    module._tables_ready = False
    shop_module._tables_ready = False
    teams_module._tables_ready = False
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
    assert len(ids) == 24 and len(set(ids)) == 24
    assert set(ids) == {f"bb:ctl:{a}" for a in bb.PANEL_ACTIONS}
    # Rows stay short so buttons don't wrap mid-row on desktop.
    assert all(len(row.children) <= 3 for row in view.children[0].children if hasattr(row, "children"))


def test_house_panel_locked_until_game_starts(bb, monkeypatch):
    import config
    monkeypatch.setattr(config, "BIG_BROTHER_HOUSE_PANEL_UNLOCKED", False)
    bb.db_add_housemate(1)
    assert not bb.game_started()
    assert "doors aren't open" in bb._house_panel_text(None)
    view = bb.HousePanelView(None)
    buttons = [c for row in view.children[0].children if hasattr(row, "children") for c in row.children]
    assert buttons and all(b.disabled for b in buttons)
    bb.set_state(bb.STATE_GAME_STARTED_AT, 123)
    assert bb.game_started_at() == 123
    view = bb.HousePanelView(None)
    buttons = [c for row in view.children[0].children if hasattr(row, "children") for c in row.children]
    assert all(not b.disabled for b in buttons)
    # The test override unlocks the panel without starting the game.
    bb.set_state(bb.STATE_GAME_STARTED_AT, None)
    monkeypatch.setattr(config, "BIG_BROTHER_HOUSE_PANEL_UNLOCKED", True)
    assert not bb.game_started() and bb.house_unlocked()


def test_house_panel_buttons_and_gating(bb):
    bb.db_add_housemate(1)
    bb.set_state(bb.STATE_GAME_STARTED_AT, 1)
    bb.create_round(bb.KIND_NOMINATIONS)
    text = bb._house_panel_text(None)
    assert "Nominations are open" in text and "1** housemates remain" in text

    view = bb.HousePanelView(None)
    assert view.timeout is None
    ids = [c.custom_id for row in view.children[0].children
           if hasattr(row, "children") for c in row.children]
    assert set(ids) == {f"bb:house:{a}" for a in bb.HOUSE_ACTIONS}

    # Nominate only exists on the panel while a nominations round is open.
    bb.close_round(bb.open_round(bb.KIND_NOMINATIONS)["id"])
    closed_ids = [c.custom_id for row in bb.HousePanelView(None).children[0].children
                  if hasattr(row, "children") for c in row.children]
    assert "bb:house:nominate" not in closed_ids and "bb:house:immunity" in closed_ids
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
    sn = bb.snugs()[0]
    assert (sn["id"], sn["thread_id"], sn["opened_by"], sn["members"], sn["closed_at"]) == (sid, 555, 1, [1, 2], None)
    assert bb.recent_snug_by(1, 60) and not bb.recent_snug_by(2, 60)


def test_echo_body_covers_content_and_embed(bb):
    e = bb.bb_embed("Secret mission", "say crumpet")
    body = bb._echo_body("hello", e)
    assert "hello" in body and "Secret mission" in body and "say crumpet" in body
    assert bb._echo_body(None, None) == "*(no text)*"
    assert len(bb._echo_body("x" * 5000, None)) <= 3800


def test_grids_render_state(bb):
    import discord
    ids = [1, 2, 3]
    grid = bb._ToggleGrid(None, ids, {1: True, 2: False}, on_toggle=None)
    styles = [b.style for b in grid.children]
    assert styles == [discord.ButtonStyle.success, discord.ButtonStyle.danger, discord.ButtonStyle.danger]

    counts = bb._CountGrid(None, ids, {2: 3}, on_press=None)
    assert [b.label for b in counts.children] == ["user 1 · 0", "user 2 · 3", "user 3 · 0"]

    gs = bb._GridSet()
    pick = bb._PickGrid(None, ids, None, gs, marked=[3])
    assert pick.children[2].style == discord.ButtonStyle.primary
    # Over the per-message button cap the grid is paged across several messages.
    many = list(range(1, 60))
    pages = bb._chunks(many)
    assert [len(p) for p in pages] == [25, 25, 9]
    assert bb._chunks([]) == [[]]


def test_house_panel_signature_tracks_visible_changes(bb):
    bb.db_add_housemate(1)
    bb.db_add_housemate(2)
    bb.set_state(bb.STATE_GAME_STARTED_AT, 1)
    base = bb._house_panel_signature(None)
    assert bb._house_panel_signature(None) == base  # stable between calls
    rid = bb.create_round(bb.KIND_NOMINATIONS)
    opened = bb._house_panel_signature(None)
    assert opened != base
    bb.record_nominations(rid, 1, [2])  # a housemate's own press changes nothing visible
    assert bb._house_panel_signature(None) == opened
    bb.add_mission(2, "m")
    assert bb._house_panel_signature(None) != opened
    assert "1** secret mission in play" in bb._house_panel_text(None)


def test_acks_are_single_use_and_owned(bb):
    aid = bb.create_ack(7, "mission #1 brief")
    assert [a["id"] for a in bb.pending_acks()] == [aid]
    assert bb.mark_ack(aid, 8) is None          # someone else's button
    assert bb.mark_ack(aid, 7) == "mission #1 brief"
    assert bb.mark_ack(aid, 7) is None          # second press does nothing
    assert bb.pending_acks() == []
    btn = bb.AckButton(aid, 7)
    assert btn.custom_id == f"bb:ack:{aid}:7" and not btn.item.disabled
    assert bb.AckButton(aid, 7, done=True).item.disabled


def test_multi_pick_grid_state(bb):
    import discord
    state = bb._MultiState([1, 2, 3], [2], 2, 2, on_done=None)
    page = bb._MultiPickGrid(None, [1, 2, 3], state, "Nominate")
    names = page.children[:3]
    assert names[1].style == discord.ButtonStyle.primary and names[1].label.startswith("✓")
    done = page.children[3]
    assert done.label == "Nominate (1)" and done.disabled  # below the minimum
    state.selected.add(3)
    page._build()
    assert page.children[3].label == "Nominate (2)" and not page.children[3].disabled
    assert state.chosen() == [2, 3]
    # 24 names per page leaves room for the Done button under the 25-button cap.
    assert bb.MULTI_PAGE == 24


def test_vote_button_custom_id(bb):
    btn = bb.VoteButton(7, 42, "Bob")
    assert btn.custom_id == "bb:vote:7:42"
    assert btn.item.label == "Bob"


def test_disabled_flag_blocks_everything(bb, monkeypatch):
    import config
    monkeypatch.setattr(config, "BIG_BROTHER_ENABLED", False)
    assert not bb.enabled()


CATALOGUE = """Meat & Fish
Whole chicken — £7.00
Sausages — £3.00

Fruit & Veg
Maris Piper potatoes — £2.50
Carrots — £1.00
Lemon - 0.50
"""


def test_shop_catalogue_parse_and_store(shop):
    entries = shop.parse_catalogue(CATALOGUE)
    assert entries == [("Meat & Fish", "Whole chicken", 700), ("Meat & Fish", "Sausages", 300),
                       ("Fruit & Veg", "Maris Piper potatoes", 250), ("Fruit & Veg", "Carrots", 100),
                       ("Fruit & Veg", "Lemon", 50)]
    shop.set_catalogue(entries, replace=True)
    assert shop.categories() == ["Meat & Fish", "Fruit & Veg"]
    # Re-adding the same item updates its price instead of duplicating it.
    shop.set_catalogue([("Meat & Fish", "sausages", 350)], replace=False)
    assert len(shop.catalogue()) == 5
    assert next(i for i in shop.catalogue() if i["name"] == "Sausages")["price"] == 350
    assert shop.parse_money("£75.50") == 7550 and shop.parse_money("100") == 10000 and shop.parse_money("x") is None
    assert shop.pounds(1050) == "£10.50"


def test_shop_seed_file_parses(shop):
    with open(shop.SEED_FILE, encoding="utf-8") as f:
        entries = shop.parse_catalogue(f.read())
    assert len(entries) == 114
    assert {c for c, _, _ in entries} == {"Meat & Fish", "Fruit & Veg", "Misc", "Breakfast & Tea",
                                          "Sweets", "Crisps & Snacks", "Drinks & Temptations", "Spices"}
    assert ("Drinks & Temptations", "4-pack Stella", 550) in entries   # hyphen in the name survives
    assert shop.parse_catalogue("Misc. .\nSalt — £0.80")[0][0] == "Misc"


def test_shop_buy_judge_and_close(shop, bb):
    shop.set_catalogue(shop.parse_catalogue(CATALOGUE), replace=True)
    ids = {i["name"]: i["id"] for i in shop.catalogue()}
    tid = shop.create_task("Roast dinner", 1000, ["Whole chicken", "potatoes", "Carrots"], None)
    task = shop.get_task(tid)
    assert shop.current_task()["id"] == tid and shop.remaining(task) == 1000

    ok, _, item, left = shop.buy(tid, ids["Whole chicken"], 1)
    assert ok and item["name"] == "Whole chicken" and left == 300
    ok, reason, _, _ = shop.buy(tid, ids["Whole chicken"], 2)
    assert not ok and "already" in reason          # one of each
    assert shop.buy(tid, ids["Lemon"], 3)[0]        # a temptation: 250 left
    ok, reason, _, _ = shop.buy(tid, ids["Sausages"], 2)
    assert not ok and "Not enough" in reason        # 300 > 250
    assert shop.buy(tid, ids["Maris Piper potatoes"], 2)[0]
    assert shop.remaining(shop.get_task(tid)) == 0
    assert shop.nothing_affordable(shop.get_task(tid))

    passed, missing, extras = shop.judge(shop.get_task(tid))
    assert not passed and missing == ["Carrots"] and extras == ["Lemon"]
    on_list, extra_rows, _ = shop.split_basket(shop.get_task(tid))
    assert [p["name"] for p in on_list] == ["Whole chicken", "Maris Piper potatoes"]
    assert [p["name"] for p in extra_rows] == ["Lemon"]
    embed = shop.shop_embed(shop.get_task(tid), None)
    names = [f.name for f in embed.fields]
    assert any(n.startswith("✅ On the list (2)") for n in names) and any(n.startswith("🍬 Extras (1)") for n in names)
    shop.close_task_db(tid, "done")
    assert shop.current_task() is None
    ok, reason, _, _ = shop.buy(tid, ids["Carrots"], 1)
    assert not ok and "closed" in reason
    dump = shop.export()
    assert len(dump["tasks"][0]["purchases"]) == 3


def test_modals_serialise_within_discord_rules(bb, shop):
    """Discord rejects modal labels over 45 chars, and a TextInput inside a Label must not
    carry its own label. Check the wire form of every Big Brother modal."""
    def walk(components):
        for c in components:
            yield c
            inner = c.get("component")
            if inner:
                yield from walk([inner])
            yield from walk(c.get("components", []))

    for modal in (shop._OpenShopModal(None), bb._AnnounceModal(None),
                  bb._TextModal("t", [("k", "Label", True, 10, False)], None)):
        payload = modal.to_dict()
        for comp in walk(payload["components"]):
            if comp.get("type") == 18:      # Label wrapper
                assert 1 <= len(comp["label"]) <= 45, comp["label"]
                # discord.py serialises a wrapped TextInput's label as null; a real string is what Discord rejects.
                assert not comp["component"].get("label"), (type(modal).__name__, comp)
            elif comp.get("type") == 4 and comp.get("label"):   # bare TextInput with its own label
                assert 1 <= len(comp["label"]) <= 45, comp["label"]


def test_timed_close_runs_to_completion(shop, bb):
    """The timer task calls close_shop from inside itself; the close must not cancel its own
    coroutine, or the announcement and host DM after the DB write never happen."""
    import asyncio
    import types
    import discord

    class FakeClient:
        def get_guild(self, _): return None
        def get_channel(self, _): return None
        async def fetch_channel(self, _):
            raise discord.NotFound(types.SimpleNamespace(status=404, reason="nf"), "nf")
        def get_user(self, _): return None
        async def fetch_user(self, _):
            raise discord.NotFound(types.SimpleNamespace(status=404, reason="nf"), "nf")

    shop.set_catalogue(shop.parse_catalogue(CATALOGUE), replace=True)
    tid = shop.create_task("Roast", 1000, ["Carrots"], bb._now())  # closes now

    async def run():
        shop.schedule_close(FakeClient(), shop.get_task(tid))
        await asyncio.wait_for(shop._close_tasks[tid], timeout=5)
    asyncio.run(run())

    assert shop.get_task(tid)["status"] == "closed"
    # The steps after the DB write ran: the closing event is logged last in close_shop.
    assert [e["kind"] for e in bb.events()][-1] == "shop_closed"


def test_catalogue_capture_window(shop, bb, monkeypatch):
    assert shop.pending_capture(5) is None
    shop.start_capture(5, replace=False)
    assert shop.pending_capture(5)["replace"] is False
    assert shop.pending_capture(6) is None            # someone else's message is ignored
    real_now = bb._now
    monkeypatch.setattr(bb, "_now", lambda: real_now() + shop.CAPTURE_WINDOW + 1)
    assert shop.pending_capture(5) is None            # expired


def test_bare_item_lines_and_price_bands(shop):
    text = "Sweets:\nHaribo\n- Jelly Babies\n\nBottle of rum\n"
    assert shop.bare_item_lines(text) == [("Sweets", "Haribo"), ("Sweets", "Jelly Babies"), ("Sweets", "Bottle of rum")]
    assert shop.bare_item_lines("Haribo — £2.00\nJelly Babies") == []   # priced lines use the normal parser
    assert shop.bare_item_lines("") == []
    # Every band round-trips through the money formatter and parser Jev's answers go through.
    for pence in shop.PRICE_BANDS:
        assert shop.parse_money(shop.pounds(pence)) == pence


def test_jev_pricing_without_key_skips_cleanly(shop, monkeypatch):
    import asyncio
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    entries, failures = asyncio.run(shop.jev_sort_and_price([(None, "Haribo")]))
    assert entries == [] and failures == 1


def test_teams_cycle_and_rooms_state(bb):
    from lib.features import big_brother_teams as teams
    for u in (1, 2, 3):
        bb.db_add_housemate(u)
    assert teams.team_of(1) is None
    assert teams.cycle_team(1) == "A" and teams.cycle_team(1) == "B" and teams.cycle_team(1) is None
    teams.set_team(1, "A"); teams.set_team(2, "B"); teams.set_team(3, "A")
    assert teams.teams() == {"A": [1, 3], "B": [2]}
    bb.db_set_status(3, bb.STATUS_EVICTED)
    assert teams.teams() == {"A": [1], "B": [2]}          # evicted housemates drop out of their team
    assert teams.room_ids() == {} and not teams.is_room(5)
    bb.set_state(teams.STATE_ROOMS, {"A": 5, "B": 6})
    assert teams.room_ids() == {"A": 5, "B": 6} and teams.is_room(6)
    summary = teams._summary(None)
    assert "Team A** (1)" in summary and "<#5>" in summary
    page = teams._TeamsPage(None, [1, 2])
    assert [b.label for b in page.children[:2]] == ["A · user 1", "B · user 2"]
    teams.clear_teams()
    assert teams.teams() == {"A": [], "B": []}


def test_unread_line_is_one_line_per_message(bb):
    """A multi-line DM must not smear the panel across six lines."""
    for u in (1, 2, 3):
        bb.db_add_housemate(u)
        bb.create_ack(u, "Hello Housemates....\n\nThe house opens shortly\n\n- BB")
    bb.create_ack(1, "mission #4 brief")
    labels = {a["label"] for a in bb.pending_acks()}
    assert all("\n" not in lb for lb in labels)
    assert len(labels) == 2                      # the bulk DM groups into one
    text = bb._panel_text(None)
    assert "**Not yet read:** 4 across 2 message(s)" in text
    assert text.count("Hello Housemates") == 1


def test_teams_pages_share_actions_and_summary(bb):
    from lib.features import big_brother_teams as teams
    for u in range(1, 27):
        bb.db_add_housemate(u)
    ins = bb.housemates()
    pages = [ins[i:i + teams.GRID_PAGE] for i in range(0, len(ins), teams.GRID_PAGE)]
    views = [teams._TeamsPage(None, p, index=i, pages=len(pages), actions=(i == 0))
             for i, p in enumerate(pages)]

    def labels(v):
        return {getattr(c, "label", "") for c in v.children}
    # Open / Close / Clear / Moles-free actions sit on page one only.
    assert {"Open rooms", "Close rooms", "Clear teams"} <= labels(views[0])
    assert not {"Open rooms", "Close rooms", "Clear teams"} & labels(views[1])
    assert len(views[1].children) == len(pages[1])          # names only

    # Every page carries the live summary, so a press on either one shows the truth.
    for i in (0, 1):
        assert "Team A" in teams._page_content(None, i, 2)
        assert f"page {i + 1} of 2" in teams._page_content(None, i, 2)
    # A long unassigned list collapses to a count rather than naming everyone.
    assert "Not on a team yet: 26" in teams._summary(None)


def test_destructive_team_buttons_are_not_one_press(bb):
    """Close rooms and Clear teams both delete something that cannot be undone in Discord,
    so neither may act straight from the grid button."""
    import inspect
    from lib.features import big_brother_teams as teams
    src = inspect.getsource(teams._TeamsPage._build)
    for handler in ("async def _close", "async def _clear"):
        body = src[src.index(handler):]
        body = body[:body.index("\n\n        async def")] if "\n\n        async def" in body else body
        assert "_Confirm" in body, handler


def test_rundown_records_each_snug_and_how_long_it_ran(bb):
    import database
    bb.db_add_housemate(1)
    bb.db_add_housemate(2)
    sid = bb.add_snug(777, 1, [1, 2])
    bb.log_event("snug_opened", actor=1, snug_id=sid, thread_id=777, members=[1, 2])
    base = bb._now()
    for i, (who, text) in enumerate([(1, "who worked in a chip shop"), (2, "kaizo did"), (1, "ta")]):
        database.DatabaseManager.execute(
            "INSERT INTO bb_messages (message_id, user_id, content, at, attachments, reply_to, thread_id) "
            "VALUES (?, ?, ?, ?, 0, NULL, ?)", (str(900 + i), str(who), text, base + i * 300, "777"))

    dump, timeline = bb.build_rundown(None)
    snug = dump["snugs"][0]
    assert snug["member_names"] == ["user 1", "user 2"]
    assert snug["messages"] == 3 and snug["active_minutes"] == 10
    assert [m["content"] for m in snug["transcript"]] == ["who worked in a chip shop", "kaizo did", "ta"]
    assert "## Snugs" in timeline
    assert "3 messages over 10 min" in timeline


def test_rundown_separates_house_team_rooms_and_snugs(bb):
    import database
    bb.db_add_housemate(1)
    sid = bb.add_snug(777, 1, [1])
    bb.log_event("snug_opened", actor=1, snug_id=sid, thread_id=777, members=[1])
    bb.log_event("team_rooms_opened", teams={"A": [1]}, rooms={"A": 500, "B": 501})
    base = bb._now()
    for i, cid in enumerate([bb.house_channel_id(), 500, 500, 501, 777]):
        database.DatabaseManager.execute(
            "INSERT INTO bb_messages (message_id, user_id, content, at, attachments, reply_to, thread_id) "
            "VALUES (?, '1', 'x', ?, 0, NULL, ?)", (str(800 + i), base + i, str(cid)))

    dump, timeline = bb.build_rundown(None)
    by_label = {c["label"]: c["messages"] for c in dump["channels"]}
    assert by_label["Team A room"] == 2 and by_label["Team B room"] == 1
    assert by_label["the house"] == 1 and by_label["snug: user 1"] == 1
    assert "## Where the talking happened" in timeline
    assert "**Team A room** — 2 messages from 1 people" in timeline


def test_idle_snugs_are_closed_by_the_bot(bb, monkeypatch):
    """Discord only archives; the bot locks, so a member cannot reopen by posting."""
    import asyncio
    import database

    bb.db_add_housemate(1)
    quiet = bb.add_snug(111, 1, [1])
    busy = bb.add_snug(222, 1, [1])
    old = bb._now() - (bb.SNUG_ARCHIVE_MINUTES + 5) * 60
    database.DatabaseManager.execute(
        "INSERT INTO bb_messages (message_id, user_id, content, at, attachments, reply_to, thread_id) "
        "VALUES ('1', '1', 'old', ?, 0, NULL, '111')", (old,))
    database.DatabaseManager.execute(
        "INSERT INTO bb_messages (message_id, user_id, content, at, attachments, reply_to, thread_id) "
        "VALUES ('2', '1', 'recent', ?, 0, NULL, '222')", (bb._now(),))
    assert bb.snug_last_activity(111) == old

    class FakeThread(discord.Thread if False else object):
        def __init__(self): self.archived, self.locked, self.sent = False, False, []
        async def send(self, text): self.sent.append(text)
        async def edit(self, **kw): self.__dict__.update({k: v for k, v in kw.items() if k in ("archived", "locked")})

    threads = {111: FakeThread(), 222: FakeThread()}
    monkeypatch.setattr(bb.discord, "Thread", FakeThread)

    class FakeClient:
        def get_channel(self, cid): return threads.get(cid)
    assert asyncio.run(bb.close_idle_snugs(FakeClient())) == 1
    assert threads[111].locked and threads[111].archived and threads[111].sent
    assert not threads[222].locked          # still busy, left alone

    closed = {s["id"]: s["closed_at"] for s in bb.snugs()}
    assert closed[quiet] and not closed[busy]
    assert [e["kind"] for e in bb.events()][-1] == "snug_closed"
    assert asyncio.run(bb.close_idle_snugs(FakeClient())) == 0   # not closed twice


def test_vote_reposts_in_the_house_or_its_own_thread(bb, monkeypatch):
    """The board is moved to the bottom as chat buries it, and bumped inside its thread to
    keep the thread in everyone's sidebar. A vote in some other channel is left alone."""
    import asyncio
    calls = []

    class FakeMsg:
        id = 999
        async def delete(self): calls.append("deleted old")

    class FakeChannel:
        def __init__(self, cid, parent_id=None):
            self.id, self.parent_id = cid, parent_id
        async def send(self, **kw):
            calls.append("posted")
            return FakeMsg()
        async def fetch_message(self, mid):
            calls.append(f"fetched {mid}")
            return FakeMsg()

    channels = {}

    async def fake_channel(client, cid):
        return channels.get(cid)
    monkeypatch.setattr(bb, "_channel", fake_channel)
    monkeypatch.setattr(bb, "_guild", lambda client: None)
    monkeypatch.setattr(bb, "_vote_embed", lambda *a: None)
    monkeypatch.setattr(bb, "_vote_view", lambda *a: None)

    vid = bb.create_round(bb.KIND_VOTE, nominees=[1, 2])
    channels[12345] = FakeChannel(12345)                       # somewhere else entirely
    bb.set_round_message(vid, 12345, 555)
    asyncio.run(bb.repost_vote_message(None))
    assert calls == []                                          # left alone

    channels[bb.house_channel_id()] = FakeChannel(bb.house_channel_id())
    bb.set_round_message(vid, bb.house_channel_id(), 555)
    asyncio.run(bb.repost_vote_message(None))
    assert "posted" in calls and "deleted old" in calls
    assert bb.get_round(vid)["message_id"] == 999               # the round follows the new message

    calls.clear()
    channels[777] = FakeChannel(777, parent_id=bb.house_channel_id())   # the vote's own thread
    bb.set_round_message(vid, 777, 555)
    asyncio.run(bb.repost_vote_message(None))
    assert "posted" in calls and "deleted old" in calls

    calls.clear()
    asyncio.run(bb.repost_vote_message(None))
    assert calls == []                                          # bumped a moment ago, left alone
    bb.set_state(bb.STATE_VOTE_BUMPED_AT, bb._now() - bb.VOTE_THREAD_BUMP_SECONDS - 1)
    asyncio.run(bb.repost_vote_message(None))
    assert "posted" in calls                                    # due again


def test_a_running_vote_moves_into_a_thread_on_deploy(bb, monkeypatch):
    """The board gets a room of its own: housemates pulled in, chat locked, house told once."""
    import asyncio
    events = []

    class FakeMsg:
        id = 4242
        async def delete(self): events.append("old board deleted")

    class FakeThread:
        id = 777
        def __init__(self): self.added, self.locked = [], False
        async def send(self, **kw):
            events.append("board posted in the thread")
            return FakeMsg()
        async def add_user(self, obj): self.added.append(obj.id)
        async def edit(self, **kw): self.locked = kw.get("locked", self.locked)

    class FakeHouse:
        id = bb.house_channel_id()
        async def fetch_message(self, mid): return FakeMsg()

    thread, house = FakeThread(), FakeHouse()

    async def fake_house_channel(client): return house
    async def fake_open_thread(ch, rid): return thread
    async def fake_send(channel, content=None, **kw):
        events.append("house told where it went")
        return FakeMsg()
    monkeypatch.setattr(bb, "house_channel", fake_house_channel)
    monkeypatch.setattr(bb, "open_vote_thread", fake_open_thread)
    monkeypatch.setattr(bb, "bb_send", fake_send)
    monkeypatch.setattr(bb, "_guild", lambda client: None)
    monkeypatch.setattr(bb, "_vote_embed", lambda *a: None)
    monkeypatch.setattr(bb, "_vote_view", lambda *a: None)

    for u in (1, 2, 3):
        bb.db_add_housemate(u)
    vid = bb.create_round(bb.KIND_VOTE, nominees=[1, 2])
    bb.cast_vote(vid, 10, 1)
    bb.set_round_message(vid, bb.house_channel_id(), 555)

    assert asyncio.run(bb.migrate_vote_to_thread(None)) == 777
    assert events == ["board posted in the thread", "old board deleted", "house told where it went"]
    assert sorted(thread.added) == [1, 2, 3] and thread.locked
    rnd = bb.get_round(vid)
    assert rnd["channel_id"] == 777 and rnd["message_id"] == 4242
    assert bb.vote_count(vid) == 1                      # votes are keyed to the round, so they survive
    assert [e["kind"] for e in bb.events()][-1] == "vote_moved_to_thread"

    events.clear()
    assert asyncio.run(bb.migrate_vote_to_thread(None)) is None   # already in its thread
    assert events == []


def test_the_vote_thread_is_public_locked_and_slow_to_archive(bb):
    import inspect
    assert bb.vote_in_thread()                          # on by default, config can turn it off
    src = inspect.getsource(bb.open_vote_thread)
    assert "public_thread" in src                       # the whole server votes, not just the house
    assert "VOTE_THREAD_AUTO_ARCHIVE" in src and bb.VOTE_THREAD_AUTO_ARCHIVE == 10080
    fill = inspect.getsource(bb.fill_vote_thread)
    assert "add_user" in fill and "locked=True" in fill
    # Opening a vote sets the thread up before telling the house about it.
    start = inspect.getsource(bb.start_vote)
    assert start.index("fill_vote_thread") < start.index("house_channel(client)")
    # Closing it shuts the thread behind the board.
    assert "archived=True" in inspect.getsource(bb.close_vote)


def test_nomination_and_vote_reasons_are_kept(bb):
    for u in (1, 2, 3):
        bb.db_add_housemate(u)
    rid = bb.create_round(bb.KIND_NOMINATIONS)
    bb.record_nominations(rid, 1, [2, 3], {2: "never speaks", 3: "threw the challenge"})
    assert bb.nomination_reasons(rid) == {(1, 2): "never speaks", (1, 3): "threw the challenge"}
    # Re-nominating replaces the reasons along with the picks.
    bb.record_nominations(rid, 1, [2], {2: "changed my mind"})
    assert bb.nomination_reasons(rid) == {(1, 2): "changed my mind"}

    vid = bb.create_round(bb.KIND_VOTE, nominees=[2, 3])
    bb.cast_vote(vid, 10, 2, "dead weight")
    bb.cast_vote(vid, 11, 3, "")            # skipped the box
    assert bb.vote_reasons(vid) == [(10, 2, "dead weight")]
    assert bb.vote_count(vid) == 2

    dump, _ = bb.build_rundown(None)
    assert dump["nominations"][0]["reason"] == "changed my mind"
    assert [v["reason"] for v in dump["votes"]] == ["dead weight", None]


def test_vote_embed_shows_the_total_but_not_the_split(bb):
    vid = bb.create_round(bb.KIND_VOTE, nominees=[1, 2])
    fields = {f.name: f.value for f in bb._vote_embed([1, 2], None, vid).fields}
    assert fields["Votes cast"] == "none yet"
    bb.cast_vote(vid, 10, 1)
    bb.cast_vote(vid, 11, 1)
    e = bb._vote_embed([1, 2], None, vid)
    fields = {f.name: f.value for f in e.fields}
    assert fields["Votes cast"] == "**2**"
    # The running total must never give away who is ahead.
    assert "user 1" not in str(fields) and "1 vote" not in (e.description or "")
    assert not bb._vote_embed([1, 2], None).fields      # omitted when no round is given


def test_opening_nominations_is_not_one_press(bb):
    """It silences the house and pings everyone, so it explains itself and asks first."""
    import inspect
    src = inspect.getsource(bb._act_open_noms)
    assert "_Confirm" in src
    assert "Open nominations?" in src
    # The explanation has to cover the consequences the host cannot undo with one press.
    for point in ("Housemate role is pinged", "say why", "by DM", "Close nominations"):
        assert point in src, point


def test_vote_moves_down_twice_as_often_as_the_panel(bb):
    """At 5 messages only the vote moves; at 20 both do and the counters restart."""
    assert bb.VOTE_REPOST_EVERY == 5 and bb.HOUSE_PANEL_REPOST_EVERY == 20
    fires = []
    for n in range(1, 21):
        bb.set_state(bb.STATE_HOUSE_MSGS_SINCE_PANEL, n)
        if n >= bb.HOUSE_PANEL_REPOST_EVERY:
            fires.append((n, "both"))
            bb.set_state(bb.STATE_HOUSE_MSGS_SINCE_PANEL, 0)   # what repost_house_panel does
            bb.set_state(bb.STATE_VOTE_REPOST_AT, 0)
        elif bb.msgs_since_vote_repost() >= bb.VOTE_REPOST_EVERY:
            fires.append((n, "vote"))
            bb.set_state(bb.STATE_VOTE_REPOST_AT, n)           # what repost_vote_message does
    assert fires == [(5, "vote"), (10, "vote"), (15, "vote"), (20, "both")]


def test_a_restart_catches_the_vote_up(bb):
    """Chat that piled up while the bot was down still counts, so the vote moves on boot."""
    import inspect
    bb.set_state(bb.STATE_HOUSE_MSGS_SINCE_PANEL, 3)
    bb.set_state(bb.STATE_VOTE_REPOST_AT, 0)
    assert bb.msgs_since_vote_repost() == 3          # under the threshold, nothing to do
    bb.set_state(bb.STATE_HOUSE_MSGS_SINCE_PANEL, 9)
    assert bb.msgs_since_vote_repost() >= bb.VOTE_REPOST_EVERY
    src = inspect.getsource(bb.ensure_panels)
    assert "msgs_since_vote_repost() >= VOTE_REPOST_EVERY" in src and "repost_vote_message(client)" in src
    # Moving it resets the gap, whether or not there was a vote to move.
    assert "set_state(STATE_VOTE_REPOST_AT" in inspect.getsource(bb.repost_vote_message)
    # A panel repost zeroes the house counter, which must not read as a negative gap.
    bb.set_state(bb.STATE_HOUSE_MSGS_SINCE_PANEL, 0)
    bb.set_state(bb.STATE_VOTE_REPOST_AT, 9)
    assert bb.msgs_since_vote_repost() == 0


def test_standings_break_down_who_voted_for_who(bb):
    """The host's DM shows the split and every voter with their reason; nobody else sees it."""
    import inspect
    for u in (1, 2):
        bb.db_add_housemate(u)
    vid = bb.create_round(bb.KIND_VOTE, nominees=[1, 2])
    bb.cast_vote(vid, 10, 1, "never speaks")
    bb.cast_vote(vid, 11, 1)
    bb.cast_vote(vid, 12, 2, "threw the challenge")
    assert bb.vote_breakdown(vid) == [(10, 1, "never speaks"), (11, 1, None), (12, 2, "threw the challenge")]
    assert bb.latest_round(bb.KIND_VOTE)["id"] == vid

    text = "\n".join(bb.standings_lines(bb.get_round(vid), None))
    assert "**2** (67%) - <@1> (user 1)" in text and "**1** (33%) - <@2> (user 2)" in text
    assert text.index("<@1>") < text.index("<@2>")          # ahead first
    assert "user 10 - never speaks" in text and "user 12 - threw the challenge" in text
    assert "user 11" in text                                 # a vote with no reason still shows

    # It goes to whoever pressed it, privately, and is not echoed to the host.
    src = inspect.getsource(bb._act_standings)
    assert "dm_user(interaction.client, interaction.user.id, echo=False" in src
    assert "notify_host" not in src and "ephemeral=True" in src
    assert bb.PANEL_ACTIONS["standings"] is bb._act_standings


def test_only_housemates_vote(bb):
    """Rem isn't playing, so his vote doesn't count and the button tells him so."""
    import inspect
    bb.db_add_housemate(1)
    bb.db_add_housemate(2)
    vid = bb.create_round(bb.KIND_VOTE, nominees=[1, 2])
    bb.cast_vote(vid, 1, 2, "obvious")
    bb.cast_vote(vid, 99, 1, "just passing through")      # not in the house
    assert bb.vote_count(vid) == 2
    assert bb.drop_outsider_votes(vid) == [99]
    assert bb.vote_count(vid) == 1 and bb.vote_of(vid, 99) is None
    assert bb.drop_outsider_votes(vid) == []               # nothing left to drop
    assert [e["kind"] for e in bb.events()][-1] == "outsider_votes_dropped"

    for button in (bb.VoteButton, bb.MyVoteButton):
        assert "is_housemate(interaction.user.id)" in inspect.getsource(button.callback), button.__name__
    # and the board itself no longer invites the whole server
    assert "the house decides" in (bb._vote_embed([1], None).description or "")
    assert "Housemates only" in (bb._vote_embed([1], None).description or "")
    # a vote already running is cleaned up on the next deploy
    assert "drop_outsider_votes" in inspect.getsource(bb.ensure_panels)


def test_you_can_check_your_own_vote(bb):
    """The vote message carries a private reminder button; it only ever shows your own vote."""
    import inspect
    vid = bb.create_round(bb.KIND_VOTE, nominees=[1, 2])
    bb.cast_vote(vid, 10, 2, "chaotic")
    assert bb.vote_of(vid, 10) == (2, "chaotic")
    assert bb.vote_of(vid, 11) is None                      # hasn't voted
    bb.cast_vote(vid, 10, 1)                                 # changed their mind, reason dropped
    assert bb.vote_of(vid, 10) == (1, None)

    ids = [i.custom_id for i in bb._vote_view(vid, [1, 2], None).children]
    assert ids == [f"bb:vote:{vid}:1", f"bb:vote:{vid}:2", f"bb:myvote:{vid}"]
    src = inspect.getsource(bb.MyVoteButton.callback)
    # Every reply it can make is private, and it never reaches for anyone else's vote.
    assert src.count("ephemeral=True") == src.count("send_message(") and "vote_breakdown" not in src


def test_standings_split_across_embeds_when_long(bb):
    lines = [f"-# · user {i} - " + "x" * 60 for i in range(200)]
    blocks = bb._paragraphs(lines)
    assert len(blocks) > 1 and all(len(b) <= 3500 for b in blocks)
    assert "".join(b.replace("\n", "") for b in blocks).count("user 199") == 1


def test_panel_repost_brings_the_vote_down_with_it(bb):
    """Whatever moves the panel - chat, or a phase change - must leave the vote underneath."""
    import inspect
    src = inspect.getsource(bb.repost_house_panel)
    assert "await repost_vote_message(client)" in src
    # and the chat path no longer schedules the vote separately at the panel threshold
    hook = inspect.getsource(bb.on_house_message)
    assert hook.count("repost_vote_message") == 1


def test_a_vote_updates_both_the_message_and_the_host_panel(bb):
    import inspect
    src = inspect.getsource(bb.refresh_vote_count)
    assert "msg.edit(embed=" in src and "await refresh_panel(client)" in src
    assert bb.VOTE_REFRESH_DELAY <= 1.0

    # Casting a vote must not change what the house panel says, or every vote would drag
    # the panel and the vote itself to the bottom of the channel.
    bb.db_add_housemate(1)
    bb.set_state(bb.STATE_GAME_STARTED_AT, 1)
    vid = bb.create_round(bb.KIND_VOTE, nominees=[1, 2])
    bb.set_round_message(vid, bb.house_channel_id(), 42)
    before = bb._house_panel_signature(None)
    bb.cast_vote(vid, 10, 1)
    assert bb._house_panel_signature(None) == before
    # but the host's panel does show the new count
    assert "1 votes cast" in bb._panel_text(None)


def test_house_panel_hides_a_shop_the_housemates_cannot_reach(bb, shop, monkeypatch):
    import config
    bb.db_add_housemate(1)
    bb.set_state(bb.STATE_GAME_STARTED_AT, 1)
    shop.set_catalogue([("Misc", "Salt", 80)], replace=True)
    tid = shop.create_task("Roast", 10000, [], None)
    shop.set_task_message(tid, bb.house_channel_id(), 4242)

    monkeypatch.setattr(config, "BIG_BROTHER_SHOP_CHANNEL", None)
    assert shop.shop_is_in_the_house()
    text = bb._house_panel_text(None)
    assert "The shop is open" in text and "/4242" in text      # links straight to it

    # Being tested in the control channel: the house is told nothing, and the panel text is
    # unchanged, so it does not re-post into the house either.
    monkeypatch.setattr(config, "BIG_BROTHER_SHOP_CHANNEL", bb.control_channel_id())
    assert not shop.shop_is_in_the_house()
    assert "The shop is open" not in bb._house_panel_text(None)
