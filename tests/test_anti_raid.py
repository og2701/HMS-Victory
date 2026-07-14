"""Focused safety tests for anti-raid persistence and mode transitions."""

import asyncio
from datetime import datetime, timezone
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from commands.moderation import anti_raid


class _Member:
    def __init__(self, user_id: int, name: str = "New Member"):
        self.id = user_id
        self.display_name = name
        self.created_at = datetime.fromtimestamp(1_000, timezone.utc)


def _isolated_state(monkeypatch, tmp_path):
    active = tmp_path / "anti_raid_state.json"
    legacy = tmp_path / "anti_raid_active"
    recent = tmp_path / "recent.json"
    backup = tmp_path / "permissions.json"
    monkeypatch.setattr(anti_raid, "ANTI_RAID_FILE", str(legacy))
    monkeypatch.setattr(anti_raid, "ANTI_RAID_STATE_FILE", str(active))
    monkeypatch.setattr(anti_raid, "ANTI_RAID_RECENT_FILE", str(recent))
    monkeypatch.setattr(anti_raid, "PERMISSIONS_BACKUP_FILE", str(backup))
    return active, recent, backup


def test_recent_join_history_is_bounded_and_pruned(monkeypatch, tmp_path):
    _isolated_state(monkeypatch, tmp_path)
    now = 100_000
    records = [
        {
            "user_id": str(index),
            "username": f"member-{index}",
            "joined_at": now - 200 + index,
            "account_created_at": 1_000,
            "quarantined": False,
        }
        for index in range(105)
    ]
    records.insert(
        0,
        {
            "user_id": "9999",
            "username": "too old",
            "joined_at": now - anti_raid.RECENT_JOIN_TTL_SECONDS - 1,
        },
    )
    records.append(
        {
            "user_id": "not-an-id",
            "username": "malformed",
            "joined_at": now,
            "account_created_at": "also-invalid",
        }
    )

    anti_raid._save_recent_joins(records, now)
    loaded = anti_raid._load_recent_joins(now)

    assert len(loaded) == anti_raid.MAX_RECENT_JOINS
    assert loaded[0]["user_id"] == "5"
    assert loaded[-1]["user_id"] == "104"
    assert all(record["user_id"] != "9999" for record in loaded)


def test_record_quarantine_and_release_lifecycle(monkeypatch, tmp_path):
    _isolated_state(monkeypatch, tmp_path)
    member = _Member(42, "  A\nNew   Member  ")

    anti_raid.record_recent_join(member, now=10_000)
    anti_raid.mark_join_quarantined(member.id, now=10_001)
    record = anti_raid._load_recent_joins(10_001)[0]
    assert record["username"] == "A New Member"
    assert record["quarantined"] is True

    anti_raid.mark_members_released([member.id], actor_id=7, now=10_002)
    record = anti_raid._load_recent_joins(10_002)[0]
    assert record["quarantined"] is False
    assert record["released_at"] == 10_002
    assert record["released_by"] == "7"


def test_join_velocity_uses_ten_minute_and_hour_windows():
    now = 10_000
    records = [
        {"joined_at": now - 30},
        {"joined_at": now - 599},
        {"joined_at": now - 601},
        {"joined_at": now - 3_599},
        {"joined_at": now - 3_601},
    ]
    assert anti_raid._join_velocity(records, now) == (2, 4)


def test_enable_is_idempotent_and_does_not_replace_backup(monkeypatch, tmp_path):
    active, _, _ = _isolated_state(monkeypatch, tmp_path)
    anti_raid.set_anti_raid_status(True)

    def unexpected_backup(_guild):
        raise AssertionError("an already-active transition must not rewrite the backup")

    monkeypatch.setattr(anti_raid, "backup_role_permissions", unexpected_backup)
    result = asyncio.run(anti_raid.enable_anti_raid(object()))

    assert result.active is True
    assert result.changed is False
    assert result.successful is True


def test_enable_keeps_join_protection_on_partial_role_failure(monkeypatch, tmp_path):
    active, _, _ = _isolated_state(monkeypatch, tmp_path)
    monkeypatch.setattr(anti_raid, "backup_role_permissions", lambda _guild: None)

    class Guild:
        me = None

        def get_role(self, _role_id):
            return object()

    async def partial_failure(_guild):
        return ["Senior role: forbidden"]

    monkeypatch.setattr(anti_raid, "disable_role_permissions", partial_failure)
    result = asyncio.run(anti_raid.enable_anti_raid(Guild()))

    assert active.exists()
    assert result.active is True
    assert result.changed is True
    assert result.failures == ("Senior role: forbidden",)
    assert anti_raid.get_anti_raid_state()["degraded"] is True

    def unexpected_backup(_guild):
        raise AssertionError("retry must preserve the original permission backup")

    async def complete_retry(_guild):
        return []

    monkeypatch.setattr(anti_raid, "backup_role_permissions", unexpected_backup)
    monkeypatch.setattr(anti_raid, "disable_role_permissions", complete_retry)
    retry = asyncio.run(anti_raid.enable_anti_raid(Guild()))

    assert retry.active is True
    assert retry.changed is False
    assert retry.successful is True
    assert anti_raid.get_anti_raid_state()["degraded"] is False


def test_enable_refuses_to_activate_without_quarantine_role(monkeypatch, tmp_path):
    active, _, _ = _isolated_state(monkeypatch, tmp_path)

    class Guild:
        def get_role(self, _role_id):
            return None

    result = asyncio.run(anti_raid.enable_anti_raid(Guild()))

    assert not active.exists()
    assert result.active is False
    assert result.changed is False
    assert "quarantine role is missing" in result.failures[0]


def test_disable_fails_closed_when_restore_is_partial(monkeypatch, tmp_path):
    active, _, _ = _isolated_state(monkeypatch, tmp_path)
    anti_raid.set_anti_raid_status(True)

    async def partial_restore(_guild):
        return ["Moderator: forbidden"]

    monkeypatch.setattr(anti_raid, "restore_role_permissions", partial_restore)
    result = asyncio.run(anti_raid.disable_anti_raid(object()))

    assert active.exists()
    assert result.active is True
    assert result.changed is False
    assert result.failures == ("Moderator: forbidden",)
    state = anti_raid.get_anti_raid_state()
    assert state["active"] is True
    assert state["degraded"] is True


def test_disable_clears_marker_only_after_complete_restore(monkeypatch, tmp_path):
    active, _, _ = _isolated_state(monkeypatch, tmp_path)
    anti_raid.set_anti_raid_status(True)

    async def complete_restore(_guild):
        return []

    monkeypatch.setattr(anti_raid, "restore_role_permissions", complete_restore)
    result = asyncio.run(anti_raid.disable_anti_raid(object()))

    assert active.exists()
    assert anti_raid.get_anti_raid_state()["active"] is False
    assert result.active is False
    assert result.changed is True
    assert result.successful is True


def test_legacy_active_marker_is_migrated_to_backed_up_json(monkeypatch, tmp_path):
    state_file, _, _ = _isolated_state(monkeypatch, tmp_path)
    with open(anti_raid.ANTI_RAID_FILE, "w", encoding="utf-8"):
        pass

    assert anti_raid.is_anti_raid_enabled() is True
    assert state_file.suffix == ".json"
    assert state_file.exists()
    assert anti_raid.get_anti_raid_state()["active"] is True


def test_corrupt_state_fails_closed_and_surfaces_degraded_mode(monkeypatch, tmp_path):
    state_file, _, _ = _isolated_state(monkeypatch, tmp_path)
    state_file.write_text("{not-json", encoding="utf-8")

    state = anti_raid.get_anti_raid_state()

    assert state["active"] is True
    assert state["degraded"] is True
    assert "unreadable" in state["failures"][0]
