"""Vic's control panel: only Oggers and Hadidas get through, a press reaches the right action
and redraws the panel, every press is logged, and each card renders its live status with the
buttons that match it (and stays a valid Components V2 card)."""

import asyncio
import os
import sys
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pytest  # noqa: E402

import config  # noqa: E402
from config import USERS  # noqa: E402
from lib.bot import control_panel as cp  # noqa: E402

RANDO = 222222222222222222
NOW = 1_794_000_000  # Fri 2026-11-06 21:20 UTC


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _Response:
    def __init__(self):
        self.sent = []
        self._done = False

    async def send_message(self, content=None, **kwargs):
        self.sent.append((content, kwargs))
        self._done = True

    def is_done(self):
        return self._done


def _interaction(user_id):
    return SimpleNamespace(user=SimpleNamespace(id=user_id, __str__=lambda self: "someone"),
                           response=_Response(), client=SimpleNamespace(),
                           followup=SimpleNamespace(send=None))


@pytest.fixture
def wired(monkeypatch):
    """Swap the real actions, the log and the refresh for recorders."""
    calls, logs, refreshed = [], [], []

    async def fake_action(interaction, action):
        calls.append(action)
        await interaction.response.send_message(f"did {action}", ephemeral=True)

    async def fake_log(client, text):
        logs.append(text)

    async def fake_refresh(client, key):
        refreshed.append(key)

    actions = {card: {a: fake_action for a in acts} for card, acts in cp.ACTIONS.items()}
    monkeypatch.setattr(cp, "ACTIONS", actions)
    monkeypatch.setattr(cp, "_log", fake_log)
    monkeypatch.setattr(cp, "refresh_card", fake_refresh)
    return calls, logs, refreshed


# ---------------------------------------------------------------- permissions

def test_only_oggers_and_hadidas_may_use_it():
    assert set(config.CONTROL_PANEL_USER_IDS) == {USERS.OGGERS, USERS.HADIDAS}
    assert cp.may_use(USERS.OGGERS)
    assert cp.may_use(USERS.HADIDAS)
    assert cp.may_use(str(USERS.HADIDAS))
    assert not cp.may_use(RANDO)
    assert not cp.may_use(None)


def test_anyone_else_gets_a_private_no_and_nothing_runs(wired):
    calls, logs, refreshed = wired
    inter = _interaction(RANDO)
    async def go():
        result = await cp.dispatch(inter, "overnight", "on")
        await asyncio.sleep(0)
        return result

    assert _run(go()) == "refused"
    assert calls == [] and refreshed == []
    assert len(logs) == 1 and logs[0].endswith("(refused)")
    content, kwargs = inter.response.sent[0]
    assert content == cp.NOT_FOR_YOU and kwargs["ephemeral"] is True


# ---------------------------------------------------------------- dispatch

@pytest.mark.parametrize("who", [USERS.OGGERS, USERS.HADIDAS])
def test_a_press_runs_its_action_logs_it_and_refreshes_the_card(wired, who):
    calls, logs, refreshed = wired

    async def go():
        result = await cp.dispatch(_interaction(who), "newsfeed", "pause")
        await asyncio.sleep(0)  # let the logging task run
        return result

    assert _run(go()) == "done"
    assert calls == ["pause"]
    assert refreshed == ["newsfeed"]
    assert len(logs) == 1 and "pressed newsfeed › pause on the control panel" in logs[0]
    assert f"(ID {who})" in logs[0]


def test_opening_something_does_not_refresh(wired):
    calls, logs, refreshed = wired
    assert _run(cp.dispatch(_interaction(USERS.OGGERS), "bank", "report")) == "done"
    assert calls == ["report"] and refreshed == []


def test_an_unknown_action_is_stale_and_runs_nothing(wired):
    calls, logs, refreshed = wired
    inter = _interaction(USERS.OGGERS)

    async def go():
        result = await cp.dispatch(inter, "newsfeed", "explode")
        await asyncio.sleep(0)
        return result

    assert _run(go()) == "stale"
    assert calls == [] and logs == []
    assert inter.response.sent[0][0] == cp.STALE
    assert refreshed == ["newsfeed"]


def test_every_action_has_a_handler_and_every_card_a_button_set():
    assert set(cp.ACTIONS) == set(cp.CARD_ORDER)
    for card in cp.CARD_ORDER:
        built = cp._fallback(card)
        for button in built["buttons"]:
            _, c, action = button["custom_id"].split(":")
            assert c == card and action in cp.ACTIONS[card], button["custom_id"]


def test_custom_ids_round_trip_through_the_dynamic_item_template():
    import re
    pattern = re.compile(cp.PanelButton.__discord_ui_compiled_template__.pattern)
    for card, acts in cp.ACTIONS.items():
        for action in acts:
            m = pattern.fullmatch(cp.custom_id(card, action))
            assert m and m["card"] == card and m["action"] == action


def test_overnight_buttons_say_what_they_do(monkeypatch, tmp_path):
    flag = tmp_path / "overnight_mute.txt"
    monkeypatch.setattr(config, "OVERNIGHT_MUTE_FILE", str(flag))
    toggled = []

    async def fake_toggle(interaction):
        toggled.append(True)
        flag.write_text("x")
        await interaction.response.send_message("toggled", ephemeral=True)

    import commands.moderation.overnight_mute as om
    monkeypatch.setattr(om, "toggle_overnight_mute", fake_toggle)
    inter = _interaction(USERS.OGGERS)
    _run(cp._overnight(inter, "off"))  # already off: says so, toggles nothing
    assert toggled == [] and "already off" in inter.response.sent[0][0]
    _run(cp._overnight(_interaction(USERS.OGGERS), "on"))
    assert toggled == [True] and flag.exists()


def test_ukpadd_keeps_the_deputy_pm_check(monkeypatch):
    import lib.economy.bank_commands_ui as ui
    monkeypatch.setattr(ui, "_is_deputy_pm", lambda user: False)
    inter = _interaction(USERS.HADIDAS)
    _run(cp._ukpadd(inter, "open"))
    content, kwargs = inter.response.sent[0]
    assert "Deputy PM" in content and kwargs["ephemeral"] is True


# ---------------------------------------------------------------- status rendering

def _texts(components):
    out = []
    for c in components:
        if c.get("content"):
            out.append(c["content"])
        out.append(_texts(c.get("components", [])))
    return "\n".join(out)


def _buttons(card):
    return [b["custom_id"].split(":")[2] for b in card["buttons"]]


def test_newsfeed_card_follows_the_state():
    running = cp.newsfeed_card({"paused": False, "last_run": NOW - 60, "daily": {"2026-11-06": 3}}, True, False, NOW)
    assert "Running" in running["status"] and "live" in running["status"]
    assert "today 3 posts" in running["status"] and f"<t:{NOW - 60}:R>" in running["status"]
    assert _buttons(running) == ["pause", "run", "dry-run", "status"]
    assert running["accent"] == cp.GREEN

    paused = cp.newsfeed_card({"paused": True}, True, True, NOW)
    assert "Paused" in paused["status"] and "dry run" in paused["status"] and "never" in paused["status"]
    assert _buttons(paused) == ["resume", "run", "live", "status"]
    assert paused["accent"] == cp.ORANGE

    off = cp.newsfeed_card({}, False, False, NOW)
    assert "Off" in off["status"] and off["accent"] == cp.GREY


def test_newsfeed_card_shows_each_outlets_feeds():
    health = {name: {"failures": 0} for name in config.NEWSFEED_FEEDS}
    health["sky_home"] = {"failures": 4, "error": "HTTP 503"}
    card = cp.newsfeed_card({"feed_health": health}, True, False, NOW)
    line = next(l for l in card["status"].split("\n") if "Outlets:" in l)
    assert line.startswith("-# Outlets: BBC ✅ · Sky ⚠️ 1/3 failing")
    assert "Guardian ✅" in line and "Independent ✅" in line
    assert len(card["buttons"]) == 4                         # no extra components


def test_bank_card_shows_the_balance_and_the_throttle():
    calm = cp.bank_card({"balance": 123456, "total_revenue": 50000, "total_tax_collected": 7000},
                        {"reserves": 100000, "pct_of_supply": 12.5, "throttled": False})
    assert "**123,456** UKP" in calm["status"] and "12.5% of supply" in calm["status"]
    assert calm["accent"] == cp.GREEN and _buttons(calm) == ["report"]
    tight = cp.bank_card({"balance": 1}, {"reserves": 1, "pct_of_supply": 0.1, "throttled": True, "multiplier": 0.5})
    assert "Throttle on" in tight["status"] and "50%" in tight["status"] and tight["accent"] == cp.ORANGE


def test_flags_card_counts_restricted_members():
    assert "Nobody is restricted" in cp.flags_card([])["status"]
    rows = [(5, "economy", NOW, "alt"), (6, "pay_only", NOW - 10, None), (7, "economy", NOW - 20, None)]
    card = cp.flags_card(rows, lambda t: t.upper())
    assert "**3** members are restricted" in card["status"]
    assert "2 ECONOMY" in card["status"] and "1 PAY_ONLY" in card["status"]
    assert "<@5>" in card["status"] and card["accent"] == cp.ORANGE
    assert _buttons(card) == ["review"]


def test_overnight_card_offers_the_opposite_switch():
    on = cp.overnight_card(True, NOW)
    assert "**On**" in on["status"] and _buttons(on) == ["off"]
    assert f"<t:{cp.next_uk_time(NOW, cp.MUTE_HOUR)}:R>" in on["status"]
    off = cp.overnight_card(False, NOW)
    assert "**Off**" in off["status"] and _buttons(off) == ["on"]


def test_next_uk_time_is_the_next_one_in_uk_time():
    import datetime as dt
    t = cp.next_uk_time(NOW, 3)
    local = dt.datetime.fromtimestamp(t, cp.UK_TZ)
    assert t > NOW and t - NOW <= 86400 and (local.hour, local.minute) == (3, 0)


def test_handout_and_titles_cards():
    assert "**9,999** UKP available" in cp.ukpadd_card(9999)["status"]
    assert "**4** of 20 titles held" in cp.titles_card(4, 20)["status"]


def _all_cards():
    return [cp._fallback(k) for k in cp.CARD_ORDER]


def test_the_whole_panel_is_one_valid_components_v2_message():
    body = cp.payload(_all_cards(), NOW)
    assert body["flags"] == 1 << 15 and body["allowed_mentions"] == {"parse": []}
    assert len(body["components"]) == 1
    container = body["components"][0]
    assert container["type"] == 17
    rows = [c for c in container["components"] if c["type"] == 1]
    assert len(rows) == len(cp.CARD_ORDER)  # one row of buttons per section
    ids = [b["custom_id"] for r in rows for b in r["components"]]
    assert len(ids) == len(set(ids))
    for r in rows:
        assert 1 <= len(r["components"]) <= 5
        for b in r["components"]:
            assert len(b["custom_id"]) <= 100 and len(b["label"]) <= 80
    seps = [c for c in container["components"] if c["type"] == 14]
    assert len(seps) == len(cp.CARD_ORDER) + 1  # between sections, and above the footer
    text = _texts(body["components"])
    assert text.startswith("## 🎛️ Vic's control panel")
    for title in ("📰 News feed", "🏦 Bank", "🛡️ Flags", "🌙 Visitor overnight mute", "🪙 UKPence handout", "🎖️ Titles"):
        assert f"### {title}" in text
    assert f"<t:{NOW}:R>" in text
    assert cp.count_components(body["components"]) <= cp.MAX_COMPONENTS
    assert cp.text_length(body["components"]) <= cp.MAX_TEXT


def test_the_panel_stays_inside_the_limits_at_its_busiest():
    cards = _all_cards()
    cards[0] = cp.newsfeed_card({"paused": True, "last_run": NOW, "last_error": {"error": "x" * 2000}}, True, True, NOW)
    cards[1] = cp.bank_card({"balance": 10**9, "total_revenue": 10**9, "total_tax_collected": 10**9},
                            {"reserves": 10**9, "pct_of_supply": 99.9, "throttled": True, "multiplier": 0.25})
    cards[2] = cp.flags_card([(123456789012345678, "economy", NOW, "x" * 500)] * 300)
    cards[3] = cp.overnight_card(True, NOW)
    components = cp.render(cards, NOW)
    assert cp.count_components(components) <= cp.MAX_COMPONENTS
    assert cp.text_length(components) <= cp.MAX_TEXT


def test_panel_accent_is_the_worst_section():
    cards = _all_cards()
    assert cp.render(cards, NOW)[0]["accent_color"] == cp.BLUE
    cards[0] = cp.newsfeed_card({"paused": True}, True, False, NOW)
    assert cp.render(cards, NOW)[0]["accent_color"] == cp.ORANGE
    cards[1] = dict(cards[1], accent=cp.RED)
    assert cp.render(cards, NOW)[0]["accent_color"] == cp.RED


def test_gather_survives_a_broken_read(monkeypatch):
    import lib.features.newsfeed as nf

    def boom():
        raise RuntimeError("disk")

    monkeypatch.setattr(nf, "load_state", boom)
    card = cp.gather("newsfeed", NOW)
    assert "Couldn't read the status" in card["status"] and card["accent"] == cp.RED
    assert _buttons(card)  # the actions still work


def test_state_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONTROL_PANEL_STATE_FILE", str(tmp_path / "cp.json"))
    assert cp.load_state() == {"thread_id": None, "message_id": None}
    cp.save_state({"thread_id": "1", "message_id": "2"})
    assert cp.load_state()["message_id"] == "2"


# ---------------------------------------------------------------- ensure / re-post

def _not_found():
    import discord
    return discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), "gone")


class _FakeHttp:
    def __init__(self, deleted=()):
        self.calls, self.deleted, self.next_id = [], {int(d) for d in deleted}, 500
        self.removed = []

    async def request(self, route, json=None):
        self.calls.append(route.method)
        if route.method in ("PATCH", "DELETE") and int(route.url.rsplit("/", 1)[1]) in self.deleted:
            raise _not_found()
        if route.method == "DELETE":
            self.removed.append(route.url.rsplit("/", 1)[1])
            return None
        self.next_id += 1
        return {"id": str(self.next_id)}


def _client(http, thread):
    parent = SimpleNamespace(created=[])

    async def create_thread(name, **kwargs):
        parent.created.append(name)
        return thread

    async def fetch_channel(cid):
        raise _not_found()

    parent.create_thread = create_thread
    channels = {config.CHANNELS.BOT_WORKSHOP: parent}
    return SimpleNamespace(http=http, parent=parent, channels=channels, fetch_channel=fetch_channel,
                           get_channel=lambda cid: channels.get(cid))


@pytest.fixture
def quiet_gather(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONTROL_PANEL_STATE_FILE", str(tmp_path / "cp.json"))
    monkeypatch.setattr(cp, "gather", lambda key, now=None: cp._fallback(key))


def _thread():
    return SimpleNamespace(id=42, parent_id=config.CHANNELS.BOT_WORKSHOP, archived=False)


def test_first_run_creates_the_thread_and_posts_one_message(quiet_gather):
    http = _FakeHttp()
    client = _client(http, _thread())
    _run(cp._sync(client))
    assert client.parent.created == [config.CONTROL_PANEL_THREAD_NAME]
    assert http.calls == ["POST"]
    assert cp.load_state() == {"thread_id": "42", "message_id": "501"}


def _existing(client):
    client.channels[42] = _thread()
    return client


def test_a_redraw_edits_the_message_in_place(quiet_gather):
    cp.save_state({"thread_id": "42", "message_id": "77"})
    http = _FakeHttp()
    _run(cp._sync(_existing(_client(http, _thread()))))
    assert http.calls == ["PATCH"]
    assert cp.load_state()["message_id"] == "77"


def test_a_deleted_panel_is_reposted(quiet_gather):
    cp.save_state({"thread_id": "42", "message_id": "77"})
    http = _FakeHttp(deleted={77})
    client = _existing(_client(http, _thread()))
    _run(cp._sync(client))
    assert client.parent.created == []
    assert http.calls == ["PATCH", "POST"]
    assert cp.load_state()["message_id"] == "501"


def test_old_per_feature_cards_are_deleted_and_one_message_posted(quiet_gather):
    cards = {"header": "1", **{k: str(10 + i) for i, k in enumerate(cp.CARD_ORDER)}}
    cp.save_state({"thread_id": "42", "cards": cards})
    http = _FakeHttp(deleted={cards["flags"]})  # one was already gone: that's fine
    _run(cp._sync(_existing(_client(http, _thread()))))
    assert sorted(http.removed) == sorted(v for k, v in cards.items() if k != "flags")
    assert http.calls.count("DELETE") == len(cards) and http.calls.count("POST") == 1
    state = cp.load_state()
    assert "cards" not in state and state["message_id"] == "501"
    # and the next redraw just edits it
    http2 = _FakeHttp()
    _run(cp._sync(_existing(_client(http2, _thread()))))
    assert http2.calls == ["PATCH"]
