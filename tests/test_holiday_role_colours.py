"""Holiday colours for staff, boosters and VIP: which holiday is on when, that every holiday covers the right roles,
and that a run only edits roles whose colours are off, stays out of the way with the flag off, and puts the usual
colours back."""

import asyncio
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OPENAI_TOKEN", "mock-token")

import pytest  # noqa: E402

import config  # noqa: E402
from lib.features import holiday_role_colours as H  # noqa: E402


class Colour:
    def __init__(self, value):
        self.value = value


class Role:
    def __init__(self, role_id, primary, secondary, edits):
        self.id, self.name, self._edits = role_id, str(role_id), edits
        self.colour, self.secondary_colour = Colour(primary), (Colour(secondary) if secondary is not None else None)

    async def edit(self, colour, secondary_colour, reason):
        self._edits.append((self.id, colour.value, secondary_colour and secondary_colour.value, reason))
        self.colour, self.secondary_colour = colour, secondary_colour


class Channel:
    def __init__(self):
        self.sent = []

    async def send(self, text, **_):
        self.sent.append(text)


class Guild:
    def __init__(self):
        self.edits, self.log, self.cabinet = [], Channel(), Channel()
        self.roles = {rid: Role(rid, H._hex(H.NORMAL[k][0]), H._hex(H.NORMAL[k][1]), self.edits)
                      for k, (rid, _) in H.ROLES.items()}

    def get_role(self, rid):
        return self.roles.get(rid)

    def get_channel(self, cid):
        return {config.CHANNELS.BOT_USAGE_LOG: self.log, config.CHANNELS.CABINET: self.cabinet}.get(cid)


class Client:
    def __init__(self, guild):
        self.guild = guild

    def get_guild(self, _):
        return self.guild


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(H, "STATE", str(tmp_path / "holiday_role_colours.json"))
    monkeypatch.setattr(H, "PAUSE", 0)


def run(guild, day, enabled):
    return asyncio.run(H.apply_holiday_role_colours(Client(guild), dt.date(*day), enabled))


def test_flag_ships_off():
    assert config.HOLIDAY_ROLE_COLOURS_ENABLED is False


def test_easter_dates():
    assert H.easter_sunday(2026) == dt.date(2026, 4, 5)
    assert H.easter_sunday(2027) == dt.date(2027, 3, 28)
    assert H.easter_sunday(2038) == dt.date(2038, 4, 25)
    assert H.active_holidays(dt.date(2027, 3, 26)) == ["easter"]   # Good Friday
    assert H.active_holidays(dt.date(2027, 3, 29)) == ["easter"]   # Easter Monday
    assert H.active_holidays(dt.date(2027, 3, 30)) == []


def test_windows():
    on = lambda *d: H.active_holidays(dt.date(*d))  # noqa: E731
    assert on(2026, 10, 23) == [] and on(2026, 10, 24) == on(2026, 11, 2) == ["halloween"]
    assert on(2026, 11, 3) == [] and on(2026, 11, 5) == ["bonfire"]
    assert on(2026, 11, 30) == ["st_andrews"]
    assert on(2026, 12, 17) == [] and on(2026, 12, 18) == on(2026, 12, 27) == ["christmas"] and on(2026, 12, 28) == []
    assert on(2026, 12, 31) == on(2027, 1, 2) == ["new_year"]
    assert on(2027, 1, 3) == []
    assert on(2027, 2, 12) == on(2027, 2, 15) == ["valentines"]
    assert on(2027, 3, 1) == ["st_davids"] and on(2027, 4, 23) == ["st_georges"]
    assert on(2026, 11, 11) == []


def test_every_holiday_covers_known_roles():
    for h in H.HOLIDAYS:
        assert set(h["colours"]) <= set(H.ROLES), h["key"]
        if h["key"] in ("halloween", "christmas", "easter"):
            assert set(h["colours"]) == set(H.ROLES), h["key"]
        else:
            assert set(h["colours"]) == set(H.BOOSTER_VIP), h["key"]
    assert set(H.NORMAL) == set(H.ROLES)


def test_booster_day_wins_inside_easter_week_staff_keep_easter():
    plan = H.plan_for(dt.date(2038, 4, 23))  # St George's inside Easter 2038
    assert plan["booster"][1] == "st_georges" and plan["vip"][1] == "st_georges"
    assert plan["cabinet"][1] == "easter"


def test_flag_off_does_nothing():
    g = Guild()
    assert run(g, (2026, 12, 5), False) is None
    assert g.edits == []


def test_christmas_then_back_to_normal():
    g = Guild()
    holidays, changed, failed = run(g, (2026, 12, 18), True)
    assert holidays == ["christmas"] and changed == len(H.ROLES) and failed == 0
    pcso = g.roles[H.ROLES["pcso"][0]]
    assert pcso.colour.value == 0x38E54D and pcso.secondary_colour.value == 0x21A835
    assert g.roles[H.ROLES["border_force"][0]].colour.value == 0x2563EB  # Moderation stays blue
    assert g.cabinet.sent == ["🎄 Christmas role colours are on"]
    assert run(g, (2026, 12, 19), True)[1] == 0          # same day colours again: no edits
    _, changed, _ = run(g, (2026, 12, 28), True)        # Christmas over: everyone back to normal
    assert changed == len(H.ROLES)
    assert g.roles[H.ROLES["border_force"][0]].secondary_colour is None
    _, changed, _ = run(g, (2026, 12, 31), True)        # New Year: boosters/VIP only
    assert changed == 2 and g.cabinet.sent[-1] == "🎆 New Year role colours are on"
    _, changed, _ = run(g, (2027, 1, 3), True)
    assert changed == 2 and g.cabinet.sent[-1].startswith("🎨")


def test_turning_off_mid_holiday_restores_once():
    g = Guild()
    run(g, (2026, 10, 28), True)
    holidays, changed, _ = run(g, (2026, 10, 29), False)
    assert holidays == [] and changed == len(H.ROLES)
    for k, (rid, _) in H.ROLES.items():
        assert g.roles[rid].colour.value == H._hex(H.NORMAL[k][0])
    assert run(g, (2026, 10, 30), False) is None


def test_solid_holiday_colours_get_a_subtle_second_colour():
    assert H.shine("#FF2E55") == "#FF5C7A"
    plan = H.plan_for(dt.date(2026, 10, 31))
    (p, s), src = plan["border_force"]
    assert src == "halloween" and p == "#2F6BFF" and s == H.shine("#2F6BFF")
    assert all(colours[1] for colours, src in plan.values() if src)
    assert H.plan_for(dt.date(2026, 11, 11))["border_force"] == (H.NORMAL["border_force"], None)  # normal days untouched
