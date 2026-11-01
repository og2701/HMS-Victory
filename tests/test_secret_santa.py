"""Secret Santa: the draw loop, the state file, the live draw's DMs and the panel views."""

import asyncio
import os
import random
import sys

import discord
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def ss(monkeypatch, tmp_path):
    import config
    from lib.features import secret_santa as module

    monkeypatch.setattr(config, "SECRET_SANTA_ENABLED", True)
    monkeypatch.setattr(config, "SECRET_SANTA_FILE", str(tmp_path / "secret_santa.json"))
    monkeypatch.setattr(module, "DM_GAP_SECONDS", 0)
    return module


class FakeMember:
    def __init__(self, uid, bot=False):
        self.id = uid
        self.bot = bot
        self.display_name = f"member{uid}"
        self.name = f"user{uid}"


class _Resp:
    status = 403
    reason = "Forbidden"


class FakeUser:
    def __init__(self, uid, client):
        self.id = uid
        self.client = client

    async def send(self, *args, **kwargs):
        self.client.on_send(self.id)
        if self.id in self.client.closed_dms:
            raise discord.Forbidden(_Resp(), "Cannot send messages to this user")
        self.client.sent.append(self.id)


class FakeClient:
    def __init__(self, closed_dms=()):
        self.closed_dms = set(closed_dms)
        self.sent = []
        self.on_send = lambda uid: None

    def get_user(self, uid):
        return FakeUser(uid, self)

    def get_guild(self, gid):
        return None


def _cycle_from(pairs, start):
    seen, cur = [start], pairs[start]
    while cur != start:
        seen.append(cur)
        cur = pairs[cur]
    return seen


def test_draw_is_one_loop_through_everyone(ss):
    rng = random.Random(7)
    for n in range(3, 15):
        ids = list(range(100, 100 + n))
        for _ in range(50):
            pairs = ss.draw_loop(ids, rng)
            assert sorted(pairs) == ids and sorted(pairs.values()) == ids
            assert all(g != r for g, r in pairs.items())
            assert all(pairs[r] != g for g, r in pairs.items())  # nobody has each other
            assert len(_cycle_from(pairs, ids[0])) == n


def test_draw_needs_three_people(ss):
    with pytest.raises(ValueError):
        ss.draw_loop([1, 2])
    with pytest.raises(ValueError):
        ss.draw_loop([1, 1, 1, 2])  # duplicates don't count twice


def test_state_defaults_to_test_mode_with_nobody_in(ss):
    state = ss.load_state()
    assert state["mode"] == ss.MODE_TEST
    assert state["participants"] == {} and state["draw"] is None


def test_add_and_remove_skip_bots_and_repeats(ss):
    assert ss.add_participants([FakeMember(1), FakeMember(2), FakeMember(9, bot=True)]) == [1, 2]
    assert ss.add_participants([FakeMember(1), FakeMember(3)], added_by=42) == [3]
    state = ss.load_state()
    assert ss.participant_ids(state) == [1, 2, 3]
    assert state["participants"]["3"]["added_by"] == 42
    assert ss.remove_participants([2, 77]) == [2]
    assert ss.participant_ids(ss.load_state()) == [1, 3]


def _setup_live(ss, n=5):
    ss.add_participants([FakeMember(i) for i in range(1, n + 1)])
    state = ss.load_state()
    state["mode"] = ss.MODE_LIVE
    ss.save_state(state)


def test_live_draw_refused_in_test_mode(ss):
    ss.add_participants([FakeMember(i) for i in range(1, 5)])
    client = FakeClient()
    with pytest.raises(RuntimeError):
        asyncio.run(ss.run_live_draw(client, by=1))
    assert client.sent == [] and ss.load_state()["draw"] is None


def test_live_draw_saves_pairs_before_any_dm_and_dms_everyone(ss):
    _setup_live(ss)
    client = FakeClient()
    stored_at_first_send = []
    client.on_send = lambda uid: stored_at_first_send.append(bool(ss.load_state()["draw"]))

    sent, missing = asyncio.run(ss.run_live_draw(client, by=1))

    assert sent == 5 and missing == []
    assert stored_at_first_send and all(stored_at_first_send)
    state = ss.load_state()
    assert sorted(client.sent) == [1, 2, 3, 4, 5]
    assert set(state["draw"]["dms"].values()) == {"sent"}
    pairs = {int(g): int(r) for g, r in state["draw"]["pairs"].items()}
    assert len(_cycle_from(pairs, 1)) == 5


def test_second_live_draw_is_refused(ss):
    _setup_live(ss)
    client = FakeClient()
    asyncio.run(ss.run_live_draw(client, by=1))
    first = ss.load_state()["draw"]["pairs"]
    with pytest.raises(RuntimeError):
        asyncio.run(ss.run_live_draw(client, by=1))
    assert ss.load_state()["draw"]["pairs"] == first
    assert len(client.sent) == 5


def test_retry_only_resends_the_dms_that_failed(ss):
    _setup_live(ss)
    client = FakeClient(closed_dms={3})
    sent, missing = asyncio.run(ss.run_live_draw(client, by=1))
    assert sent == 4 and missing == [3]

    client.closed_dms.clear()
    client.sent.clear()
    sent, missing = asyncio.run(ss.send_match_dms(client))
    assert sent == 1 and missing == [] and client.sent == [3]
    assert ss.undelivered(ss.load_state()) == []


def _custom_ids(view):
    out = []
    for c in view.walk_children():
        button = c.item if isinstance(c, discord.ui.DynamicItem) else c
        cid = getattr(button, "custom_id", None)
        if cid:
            out.append((cid, getattr(button, "disabled", False)))
    return out


def test_panel_never_shows_who_has_who_after_a_live_draw(ss):
    _setup_live(ss)
    asyncio.run(ss.run_live_draw(FakeClient(closed_dms={2}), by=1))
    text = ss.panel_text(ss.load_state())
    assert "→" not in text
    assert "No DM yet:** <@2>" in text


def test_panel_buttons_lock_after_the_draw_and_retry_appears_only_when_needed(ss):
    ss.add_participants([FakeMember(i) for i in range(1, 5)])
    ids = dict(_custom_ids(ss.ControlPanelView(ss.load_state())))
    assert "ss:ctl:retry" not in ids
    assert not any(ids.values())

    state = ss.load_state()
    state["mode"] = ss.MODE_LIVE
    ss.save_state(state)
    asyncio.run(ss.run_live_draw(FakeClient(closed_dms={4}), by=1))
    ids = dict(_custom_ids(ss.ControlPanelView(ss.load_state())))
    for locked in ("ss:ctl:mode", "ss:ctl:add", "ss:ctl:remove", "ss:ctl:draw"):
        assert ids[locked] is True
    assert ids["ss:ctl:retry"] is False and ids["ss:ctl:refresh"] is False


def test_only_the_three_coordinators_can_press_the_panel(ss):
    import config
    assert ss.operator_ids() == {config.USERS.OGGERS, config.USERS.CHIN, config.USERS.KAIZO}

    replies = []

    class Response:
        async def send_message(self, text, **kwargs):
            replies.append(text)

    class Interaction:
        def __init__(self, uid):
            self.user = FakeMember(uid)
            self.response = Response()

    pressed = []
    ss.PANEL_ACTIONS["probe"] = lambda interaction: pressed.append(interaction.user.id) or asyncio.sleep(0)
    try:
        asyncio.run(ss.ControlButton("probe").callback(Interaction(12345)))
        assert pressed == [] and "coordinators" in replies[0]
        asyncio.run(ss.ControlButton("probe").callback(Interaction(config.USERS.KAIZO)))
        assert pressed == [config.USERS.KAIZO]
    finally:
        del ss.PANEL_ACTIONS["probe"]


def test_dynamic_items_match_their_own_custom_ids(ss):
    for action in ss.PANEL_ACTIONS:
        assert ss.ControlButton.__discord_ui_compiled_template__.fullmatch(f"ss:ctl:{action}")


def test_test_draw_splits_long_lists_and_ends_with_a_dm_preview(ss):
    ss.add_participants([FakeMember(i) for i in range(1, 131)])
    state = ss.load_state()
    pairs = ss.draw_loop(ss.participant_ids(state))
    views = ss._test_draw_views(FakeClient(), state, pairs)
    assert len(views) == 4  # 60 + 60 + 10 pairing lines, then the preview
    for v in views:
        v.to_components()
        text = "\n".join(getattr(c, "content", "") for c in v.walk_children())
        assert len(text) < 4000
    assert "The DM" in "\n".join(getattr(c, "content", "") for c in views[-1].walk_children())


def test_match_dm_builds_without_a_cached_member(ss):
    ss.MatchDMView(5, None, "member5").to_components()


def test_no_em_dashes_in_user_facing_copy(ss):
    with open(ss.__file__, encoding="utf-8") as f:
        assert "—" not in f.read()
