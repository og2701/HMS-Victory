"""Seasonal level-role colours: which season a date is, that the palettes line up with the roles, and that a run
only edits roles whose colour is off, stays out of the way with the flag off, and puts the originals back."""

import asyncio
import datetime as dt
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OPENAI_TOKEN", "mock-token")

import pytest  # noqa: E402

import config  # noqa: E402
from lib.core.constants import CHAT_LEVEL_ROLE_THRESHOLDS  # noqa: E402
from lib.features import seasonal_rank_colours as S  # noqa: E402


def hexint(code):
    return int(code.lstrip("#"), 16)


class Colour:
    def __init__(self, value):
        self.value = value


class Role:
    def __init__(self, role_id, value, edits):
        self.id, self.name, self.colour, self._edits = role_id, str(role_id), Colour(value), edits

    async def edit(self, colour, reason):
        self._edits.append((self.id, colour.value, reason))
        self.colour = Colour(colour.value)


class Channel:
    def __init__(self):
        self.sent = []

    async def send(self, text, **_):
        self.sent.append(text)


class Guild:
    def __init__(self, palette):
        self.edits, self.log, self.cabinet = [], Channel(), Channel()
        self.roles = {rid: Role(rid, hexint(c), self.edits) for (_, rid), c in zip(CHAT_LEVEL_ROLE_THRESHOLDS, S.PALETTES[palette])}

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
    monkeypatch.setattr(S, "STATE", str(tmp_path / "seasonal_rank_colours.json"))
    monkeypatch.setattr(S, "PAUSE", 0)


def run(guild, day, enabled):
    return asyncio.run(S.apply_seasonal_rank_colours(Client(guild), dt.date(*day), enabled))


def test_flag_on_but_waits_for_go_live(monkeypatch):
    assert config.SEASONAL_RANK_COLOURS_ENABLED is True
    from lib.features import holiday_role_colours as H
    monkeypatch.setattr(H, "go_live_reached", lambda now=None: False)
    g = Guild("original")
    assert asyncio.run(S.apply_seasonal_rank_colours(Client(g), dt.date(2026, 12, 20))) is None and g.edits == []


def test_seasons_go_by_month():
    assert S.season_for(dt.date(2026, 12, 1)) == "winter"
    assert S.season_for(dt.date(2027, 2, 28)) == "winter"
    assert S.season_for(dt.date(2027, 3, 1)) == "spring"
    assert S.season_for(dt.date(2027, 5, 31)) == "spring"
    assert S.season_for(dt.date(2027, 6, 1)) == "summer"
    assert S.season_for(dt.date(2027, 8, 31)) == "summer"
    assert S.season_for(dt.date(2027, 9, 1)) == "autumn"
    assert S.season_for(dt.date(2027, 11, 30)) == "autumn"


def test_palettes_have_a_valid_colour_for_every_level_role():
    assert len(CHAT_LEVEL_ROLE_THRESHOLDS) == 20
    assert set(S.PALETTES) == {"spring", "summer", "autumn", "winter", "original"}
    for codes in S.PALETTES.values():
        assert len(codes) == 20 and all(re.fullmatch(r"#[0-9A-F]{6}", c) for c in codes)
    # Hadidas's ends, Serf first and Royal Duke last
    assert (S.PALETTES["winter"][0], S.PALETTES["winter"][-1]) == ("#143258", "#F5FCFF")
    assert (S.PALETTES["original"][0], S.PALETTES["original"][-1]) == ("#6D001A", "#FF8400")


def test_a_season_change_edits_only_the_roles_that_differ_then_nothing():
    guild = Guild("original")
    rid = CHAT_LEVEL_ROLE_THRESHOLDS[5][1]
    guild.roles[rid].colour = Colour(hexint(S.PALETTES["winter"][5]))  # already right
    assert run(guild, (2026, 12, 1), True) == ("winter", 19, 0)
    assert rid not in [e[0] for e in guild.edits]
    assert all(r.colour.value == hexint(c) for r, c in zip(guild.roles.values(), S.PALETTES["winter"]))
    assert guild.log.sent == ["🎨 Rank roles: winter colours applied (19 changed)"]
    assert run(guild, (2026, 12, 15), True) == ("winter", 0, 0)
    assert len(guild.edits) == 19 and len(guild.log.sent) == 1


def test_flag_off_and_never_on_does_nothing():
    guild = Guild("original")
    assert run(guild, (2027, 6, 1), False) is None
    assert guild.edits == [] and not os.path.exists(S.STATE)


def test_flag_off_after_a_season_restores_the_originals_once():
    guild = Guild("original")
    run(guild, (2027, 3, 1), True)
    assert run(guild, (2027, 4, 2), False) == ("original", 20, 0)
    assert all(r.colour.value == hexint(c) for r, c in zip(guild.roles.values(), S.PALETTES["original"]))
    assert guild.log.sent[-1] == "🎨 Rank roles: original colours restored (20 changed)"
    edits = len(guild.edits)
    assert run(guild, (2027, 4, 3), False) is None
    assert len(guild.edits) == edits


def test_a_failed_restore_is_tried_again():
    guild = Guild("summer")
    S.atomic_write_json(S.STATE, {"applied": "summer"})
    del guild.roles[CHAT_LEVEL_ROLE_THRESHOLDS[0][1]]
    assert run(guild, (2027, 7, 1), False) == ("original", 19, 1)
    assert S.wanted(dt.date(2027, 7, 2), False) == "original"


def test_cabinet_hears_about_a_switch_but_not_a_boot_check_that_changes_nothing():
    guild = Guild("original")
    run(guild, (2027, 9, 1), True)
    assert guild.cabinet.sent == ["🍂 Autumn rank colours are on"]
    run(guild, (2027, 9, 1), True)  # the boot check after a restart
    assert guild.cabinet.sent == ["🍂 Autumn rank colours are on"]
    run(guild, (2027, 12, 1), True)
    run(guild, (2027, 12, 2), False)
    assert guild.cabinet.sent[1:] == ["❄️ Winter rank colours are on", "🎨 Rank colours are back to the usual ones"]


def test_flag_off_and_never_on_says_nothing():
    guild = Guild("original")
    run(guild, (2027, 1, 1), False)
    assert guild.cabinet.sent == [] and guild.log.sent == []
