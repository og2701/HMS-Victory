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
    assert len(ids) == 23 and len(set(ids)) == 23
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
    assert bb.snugs()[0] == {"id": sid, "thread_id": 555, "opened_by": 1, "members": [1, 2],
                             "created_at": bb.snugs()[0]["created_at"]}
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


def test_moles_read_the_other_team_only(bb, monkeypatch):
    import asyncio
    import types
    from lib.features import big_brother_teams as teams

    for u in (1, 2, 3):
        bb.db_add_housemate(u)
    teams.set_team(1, "A"); teams.set_team(2, "B"); teams.set_team(3, "B")
    assert teams.toggle_mole(1) is True and teams.moles() == [1]
    bb.set_state(teams.STATE_ROOMS, {"A": 100, "B": 200})
    assert teams.team_for_room(200) == "B" and teams.team_for_room(999) is None

    sent = []
    async def fake_dm(client, user_id, content=None, embed=None, *, echo=True, ack=None):
        sent.append((user_id, embed.description))
        return True
    monkeypatch.setattr(bb, "dm_user", fake_dm)
    monkeypatch.setattr(teams, "RELAY_DELAY", 0.01)

    def msg(channel_id, author, text):
        return types.SimpleNamespace(
            channel=types.SimpleNamespace(id=channel_id), guild=None, attachments=[],
            author=types.SimpleNamespace(id=author), content=text)

    async def run():
        # Team B's room: the mole is on A, so it reaches them.
        await teams.relay_room_message(None, msg(200, 2, "we'll throw the challenge"))
        await teams.relay_room_message(None, msg(200, 3, "agreed"))
        await asyncio.sleep(0.1)
        # Team A's room: the mole is on A themselves, so nothing is relayed to them.
        await teams.relay_room_message(None, msg(100, 1, "nothing to see"))
        await asyncio.sleep(0.1)
    asyncio.run(run())

    assert len(sent) == 1
    uid, body = sent[0]
    assert uid == 1 and "throw the challenge" in body and "agreed" in body
    assert teams.export()["moles"] == [1]
