"""Opening Skyrim in the activity: the sign-in's opening screen and the home screen's tile (lib/activities/server.py)."""
from types import SimpleNamespace

import pytest

import config
from lib.activities import server
from lib.core import file_operations as F
from lib.features.skyrim import engine as E

UID = 4242
WORKSHOP, ELSEWHERE = 111, 222


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for key in ("SKYRIM_PROFILES_FILE", "SKYRIM_DAILY_FILE", "SKYRIM_GRAVEYARD_FILE", "SKYRIM_WORLDBOSS_FILE",
                "PERSISTENT_VIEWS_FILE"):
        monkeypatch.setattr(config, key, str(tmp_path / f"{key}.json"))
    monkeypatch.setattr(F, "PERSISTENT_VIEWS_FILE", str(tmp_path / "PERSISTENT_VIEWS_FILE.json"))
    monkeypatch.setattr(config, "ACTIVITIES_CASINO_CHANNELS", [WORKSHOP])
    yield
    E.drain_log()


def client(name="Tester"):
    member = SimpleNamespace(display_name=name)
    guild = SimpleNamespace(get_member=lambda uid: member)
    return SimpleNamespace(get_guild=lambda gid: guild)


def test_the_opening_screen_is_the_class_pick_then_the_town():
    first = server._opening(client(), UID, "skyrim", WORKSHOP)
    assert first["game"] == "skyrim" and first["skyrim"]["hero"] is None and first["skyrim"]["classPick"]
    E.create_profile(UID, "Tester", "mage")
    again = server._opening(client("Arch-Mage"), UID, "skyrim", WORKSHOP)
    assert again["skyrim"]["hero"]["stone"] == "mage" and again["skyrim"]["hero"]["name"] == "Arch-Mage"


def test_the_home_tile_shows_where_skyrim_is_open(monkeypatch):
    monkeypatch.setattr(config, "SKYRIM_AS_ACTIVITY", False)
    assert server._skyrim_card(UID, ELSEWHERE) is None                 # still being tested: the workshop only
    assert server._skyrim_card(UID, WORKSHOP) == {"level": 0, "delvesLeft": 0, "live": False}
    E.create_profile(UID, "Tester", "thief")
    card = server._skyrim_card(UID, WORKSHOP)
    assert card["level"] == 1 and card["delvesLeft"] > 0 and not card["live"]
    monkeypatch.setattr(config, "SKYRIM_AS_ACTIVITY", True)
    assert server._skyrim_card(UID, ELSEWHERE)["level"] == 1          # handed over: everywhere


def test_the_launch_commands_follow_the_switch():
    launch = config.ACTIVITIES_LAUNCH_COMMANDS
    assert ("skyrim" in launch) == bool(config.SKYRIM_AS_ACTIVITY)
    assert ("test-skyrim" in launch) == (not config.SKYRIM_AS_ACTIVITY)
    assert (launch.get("skyrim") or launch.get("test-skyrim"))[0] == "skyrim"


def test_the_skyrim_command_opens_skyrim_not_home():
    from lib.activities import launcher
    launcher.want(4242, "skyrim")             # what /skyrim does before launching the activity
    assert server._game_for(4242, {}) == "skyrim"
    assert server._game_for(4242, {"game": "skyrim"}) == "skyrim"      # a Play link naming it
    assert server._game_for(4242, {}) == "home"                        # the request is used once
