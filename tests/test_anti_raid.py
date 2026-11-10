"""Focused safety tests for anti-raid persistence and mode transitions."""

import asyncio
from datetime import datetime, timezone
import json
import os
import sys
import types
from unittest.mock import AsyncMock

import discord
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from commands.moderation import anti_raid


class _Member:
    def __init__(self, user_id: int, name: str = "New Member"):
        self.id = user_id
        self.display_name = name
        self.created_at = datetime.fromtimestamp(1_000, timezone.utc)


class _Permissions:
    def __init__(self, value: int):
        self.value = value


class _Role:
    def __init__(self, role_id: int, value: int = 0, *, managed: bool = False):
        self.id = role_id
        self.name = f"role-{role_id}"
        self.permissions = _Permissions(value)
        self.managed = managed
        self.edits = []

    async def edit(self, *, permissions, reason):
        self.permissions = permissions
        self.edits.append((permissions.value, reason))


class _Guild:
    def __init__(self, guild_id: int, roles):
        self.id = guild_id
        self.roles = list(roles)


class _JoinGuild:
    def __init__(self, quarantine_role):
        self.quarantine_role = quarantine_role

    def get_role(self, role_id):
        if role_id == anti_raid.QUARANTINE_ROLE_ID:
            return self.quarantine_role
        return None

    def get_channel(self, _channel_id):
        return None


class _JoinMember(_Member):
    def __init__(self, user_id: int, quarantine_role):
        super().__init__(user_id)
        self.guild = _JoinGuild(quarantine_role)
        self.added_roles = []

    async def add_roles(self, role, *, reason=None):
        self.added_roles.append((role, reason))

    def __str__(self):
        return self.display_name


def _isolated_state(monkeypatch, tmp_path):
    monkeypatch.setattr(anti_raid, "_mode_lock", asyncio.Lock())
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


def test_permission_backup_is_versioned_guild_bound_and_restorable(monkeypatch, tmp_path):
    _, _, backup_file = _isolated_state(monkeypatch, tmp_path)
    original = discord.Permissions(embed_links=True, attach_files=True)
    role = _Role(10, value=original.value)
    guild = _Guild(100, [role])

    anti_raid.backup_role_permissions(guild)
    payload = json.loads(backup_file.read_text(encoding="utf-8"))
    role.permissions = _Permissions(0)
    failures = asyncio.run(anti_raid.restore_role_permissions(guild))

    assert payload["version"] == anti_raid.ROLE_PERMISSION_BACKUP_VERSION
    assert payload["guild_id"] == "100"
    assert payload["roles"] == {"10": original.value}
    assert failures == []
    assert role.permissions.value == original.value


def test_empty_permission_backup_keeps_lockdown_active(monkeypatch, tmp_path):
    state_file, _, backup_file = _isolated_state(monkeypatch, tmp_path)
    anti_raid.set_anti_raid_status(True)
    backup_file.write_text("{}", encoding="utf-8")
    role = _Role(10)

    result = asyncio.run(anti_raid.disable_anti_raid(_Guild(100, [role])))

    assert result.active is True
    assert result.changed is False
    assert "no role permissions" in result.failures[0]
    assert role.edits == []
    assert json.loads(state_file.read_text(encoding="utf-8"))["active"] is True


def test_wrong_guild_is_rejected_but_new_roles_do_not_block_restore(monkeypatch, tmp_path):
    _isolated_state(monkeypatch, tmp_path)
    first = _Role(10, value=111)
    backup_guild = _Guild(999, [first])
    anti_raid.backup_role_permissions(backup_guild)

    live = _Guild(100, [first, _Role(20, value=222)])
    prepared, wrong_guild_failures = anti_raid._prepare_role_permission_restore(live)
    assert prepared == []
    assert "different guild" in wrong_guild_failures[0]
    assert first.edits == []

    anti_raid.backup_role_permissions(_Guild(100, [first]))
    prepared, coverage_failures = anti_raid._prepare_role_permission_restore(live)
    assert [role for role, _ in prepared] == [first]
    assert coverage_failures == []
    assert first.edits == []


def test_active_join_reports_quarantine_success_and_missing_role_failure(monkeypatch, tmp_path):
    _isolated_state(monkeypatch, tmp_path)
    anti_raid.set_anti_raid_status(True)
    monkeypatch.setattr(anti_raid, "record_recent_join", lambda _member: None)
    monkeypatch.setattr(anti_raid, "mark_join_quarantined", lambda _user_id: None)
    monkeypatch.setattr(
        anti_raid,
        "_persist_moderation_notice",
        lambda *_args, **_kwargs: None,
    )

    async def no_log(_guild, _message):
        return None

    monkeypatch.setattr(anti_raid, "_log_action", no_log)
    quarantine_role = object()
    successful_member = _JoinMember(42, quarantine_role)
    missing_role_member = _JoinMember(43, None)

    success = asyncio.run(anti_raid.handle_new_member_anti_raid(successful_member))
    failure = asyncio.run(anti_raid.handle_new_member_anti_raid(missing_role_member))

    assert success == anti_raid.AntiRaidJoinOutcome(True, True)
    assert successful_member.added_roles == [
        (quarantine_role, "Anti-raid protection is active")
    ]
    assert failure == anti_raid.AntiRaidJoinOutcome(True, False)
    assert missing_role_member.added_roles == []


def test_member_join_never_grants_normal_role_for_any_active_outcome(monkeypatch):
    class Member:
        id = 99

    async def unexpected_grant(_member, _role):
        raise AssertionError("normal member role must not be granted during anti-raid")

    monkeypatch.setattr(
        anti_raid,
        "grant_normal_member_role_if_safe",
        unexpected_grant,
    )
    for quarantined in (True, False):
        async def active_outcome(_member, quarantined=quarantined):
            return anti_raid.AntiRaidJoinOutcome(True, quarantined)

        monkeypatch.setattr(
            anti_raid,
            "handle_new_member_anti_raid",
            active_outcome,
        )
        result = asyncio.run(anti_raid.handle_new_member_roles(Member(), object()))
        assert result == anti_raid.AntiRaidJoinOutcome(True, quarantined)


def test_member_join_quarantines_if_protection_activates_before_normal_role(monkeypatch, tmp_path):
    _isolated_state(monkeypatch, tmp_path)
    member = _JoinMember(42, object())
    monkeypatch.setattr(anti_raid, "_persist_moderation_notice", lambda *_args: None)
    async def initially_inactive(_member):
        return anti_raid.AntiRaidJoinOutcome(False, False)

    async def protection_activated(_member, _role):
        anti_raid.set_anti_raid_status(True)
        return False

    monkeypatch.setattr(
        anti_raid,
        "handle_new_member_anti_raid",
        initially_inactive,
    )
    monkeypatch.setattr(
        anti_raid,
        "grant_normal_member_role_if_safe",
        protection_activated,
    )

    result = asyncio.run(anti_raid.handle_new_member_roles(member, object()))

    assert result == anti_raid.AntiRaidJoinOutcome(True, True)
    assert len(member.added_roles) == 1


def test_roles_at_or_above_bot_are_never_touched(monkeypatch, tmp_path):
    _isolated_state(monkeypatch, tmp_path)
    below = _Role(1, 7)
    below.position = 1
    bot_top = _Role(2, 7)
    bot_top.position = 5
    above = _Role(3, 7)
    above.position = 9
    guild = _Guild(100, [below, bot_top, above])
    guild.me = type("_Me", (), {"top_role": bot_top})()

    anti_raid.backup_role_permissions(guild)
    with open(anti_raid.PERMISSIONS_BACKUP_FILE, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    assert set(payload["roles"]) == {"1"}

    failures = asyncio.run(anti_raid.disable_role_permissions(guild))
    assert failures == []
    assert below.edits and not bot_top.edits and not above.edits

    failures = asyncio.run(anti_raid.restore_role_permissions(guild))
    assert failures == []
    assert len(below.edits) == 2 and not bot_top.edits and not above.edits


def test_protection_toggle_announcement_names_the_mod():
    class _Channel:
        def __init__(self):
            self.sent = []

        async def send(self, *args, **kwargs):
            self.sent.append({"args": args, **kwargs})

    class _Client:
        def __init__(self):
            self.police = _Channel()

        def get_channel(self, _channel_id):
            return self.police

    class _Actor:
        mention = "<@42>"

    client = _Client()
    asyncio.run(anti_raid._announce_protection_toggle(client, _Actor(), True, 0))
    asyncio.run(anti_raid._announce_protection_toggle(client, _Actor(), False, 3))

    enabled_text = json.dumps(client.police.sent[0]["view"].to_components())
    disabled_text = json.dumps(client.police.sent[1]["view"].to_components())
    assert "<@42>" in enabled_text and "enabled" in enabled_text
    assert "<@42>" in disabled_text and "disabled" in disabled_text
    assert "3 role operation(s) failed" in disabled_text


# ---------------------------------------------------------------------------
# Quarantine-only: hold the joins, leave everyone already here alone.
# ---------------------------------------------------------------------------
class _FakeRole:
    """Roles compare by position, and the preflight relies on that ordering."""

    def __init__(self, rid, position):
        self.id = rid
        self.position = position

    def __ge__(self, other):
        return self.position >= getattr(other, "position", 0)

    def __lt__(self, other):
        return self.position < getattr(other, "position", 0)


class _ModeGuild:
    """Tracks whether permissions were touched, which is the whole point of the mode."""

    def __init__(self, bot_top=10, quarantine_pos=5):
        self.id = 1
        self.restricted = 0
        self.restored = 0
        self._role = _FakeRole(anti_raid.QUARANTINE_ROLE_ID, quarantine_pos)
        self.me = types.SimpleNamespace(
            guild_permissions=types.SimpleNamespace(manage_roles=True),
            top_role=_FakeRole(0, bot_top),
        )

    def get_role(self, rid):
        return self._role if rid == anti_raid.QUARANTINE_ROLE_ID else None


def _mode_env(monkeypatch, tmp_path):
    monkeypatch.setattr(anti_raid, "ANTI_RAID_STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(anti_raid, "ANTI_RAID_FILE", str(tmp_path / "marker"))
    guild = _ModeGuild()

    async def fake_disable(g):
        guild.restricted += 1
        return []

    async def fake_restore(g):
        guild.restored += 1
        return []

    monkeypatch.setattr(anti_raid, "disable_role_permissions", fake_disable)
    monkeypatch.setattr(anti_raid, "restore_role_permissions", fake_restore)
    monkeypatch.setattr(anti_raid, "backup_role_permissions", lambda g: None)
    return guild


def test_quarantine_only_never_touches_role_permissions(monkeypatch, tmp_path):
    guild = _mode_env(monkeypatch, tmp_path)
    result = asyncio.run(anti_raid.enable_anti_raid(guild, mode=anti_raid.MODE_QUARANTINE_ONLY))
    assert result.active and result.mode == anti_raid.MODE_QUARANTINE_ONLY
    assert guild.restricted == 0, "existing members must be left alone"
    assert anti_raid.anti_raid_mode() == anti_raid.MODE_QUARANTINE_ONLY


def test_the_full_lockdown_still_restricts(monkeypatch, tmp_path):
    guild = _mode_env(monkeypatch, tmp_path)
    result = asyncio.run(anti_raid.enable_anti_raid(guild, mode=anti_raid.MODE_FULL))
    assert result.active and guild.restricted == 1


def test_disabling_quarantine_only_does_not_apply_a_stale_backup(monkeypatch, tmp_path):
    """Nothing was taken, so nothing may be restored - a leftover backup from an older
    full lockdown would otherwise be written over live permissions."""
    guild = _mode_env(monkeypatch, tmp_path)
    asyncio.run(anti_raid.enable_anti_raid(guild, mode=anti_raid.MODE_QUARANTINE_ONLY))
    result = asyncio.run(anti_raid.disable_anti_raid(guild))
    assert result.active is False and result.changed
    assert guild.restored == 0


def test_narrowing_from_full_hands_the_permissions_back(monkeypatch, tmp_path):
    guild = _mode_env(monkeypatch, tmp_path)
    asyncio.run(anti_raid.enable_anti_raid(guild, mode=anti_raid.MODE_FULL))
    result = asyncio.run(anti_raid.enable_anti_raid(guild, mode=anti_raid.MODE_QUARANTINE_ONLY))
    assert result.mode == anti_raid.MODE_QUARANTINE_ONLY
    assert guild.restored == 1, "stepping down must restore what the full mode took"


def test_widening_to_full_restricts_after_quarantine_only(monkeypatch, tmp_path):
    guild = _mode_env(monkeypatch, tmp_path)
    asyncio.run(anti_raid.enable_anti_raid(guild, mode=anti_raid.MODE_QUARANTINE_ONLY))
    result = asyncio.run(anti_raid.enable_anti_raid(guild, mode=anti_raid.MODE_FULL))
    assert result.mode == anti_raid.MODE_FULL and guild.restricted == 1


def test_state_written_before_modes_existed_reads_as_the_full_lockdown(monkeypatch, tmp_path):
    """An in-flight deployment must keep restoring the permissions it actually backed up."""
    _mode_env(monkeypatch, tmp_path)
    import json
    with open(anti_raid.ANTI_RAID_STATE_FILE, "w") as fh:
        json.dump({"version": 1, "active": True, "degraded": False,
                   "failures": [], "updated_at": 1}, fh)
    assert anti_raid.anti_raid_mode() == anti_raid.MODE_FULL


def test_quarantine_only_can_be_started_without_going_through_full_lockdown(monkeypatch, tmp_path):
    """Enabling full then narrowing would strip every role and hand it straight back."""
    guild = _mode_env(monkeypatch, tmp_path)
    asyncio.run(anti_raid.enable_anti_raid(guild, mode=anti_raid.MODE_QUARANTINE_ONLY))
    assert guild.restricted == 0 and guild.restored == 0


def test_the_panel_does_not_reprint_the_join_watch_brief():
    """The brief is a screenful of rules; it pushed everything below it off the panel."""
    block = anti_raid._join_watch_block()
    assert "Edit context" in block
    assert len(block) < 500, "the panel should summarise the brief, not carry it"


def test_the_quarantine_block_is_a_count_not_a_roster():
    """The dropdown below already lists every name and is what you act on."""
    members = [types.SimpleNamespace(id=i, display_name=f"m{i}", mention=f"<@{i}>")
               for i in range(20)]
    block = anti_raid._quarantine_block(members, set())
    assert "20 members" in block
    for m in members:
        assert m.mention not in block, "names belong in the dropdown, not the text"
    assert anti_raid._quarantine_block([], set()).endswith("quarantine role.")


def test_the_panel_gate_matches_the_command_gate():
    """Opening /anti-raid and pressing anything on it must be the same set of roles, or
    the panel opens and does nothing for whoever the mismatch excluded."""
    import re
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "lib", "bot", "setup_commands.py")).read()
    m = re.search(r'@command\("anti-raid".*?has_any_role\(i, \[(.*?)\]\)', src, re.S)
    assert m, "could not find the /anti-raid check"
    named = {n.strip().split(".")[-1] for n in m.group(1).split(",") if n.strip()}
    panel = {n for n in ("MINISTER", "CABINET", "BORDER_FORCE", "PCSO",
                         "DEPUTY_MINISTER_OF_COMMUNITY")
             # PCSO is deliberately not on this list; the assertion below pins that.
             if getattr(anti_raid.ROLES, n) in anti_raid.STAFF_ROLE_IDS}
    assert named == panel, f"command allows {named}, panel allows {panel}"


def test_pcso_alone_does_not_open_the_control_centre():
    """Deliberate: the panel arms lockdowns and releases quarantines, so it stops at
    Deputy Minister of Community rather than extending to every PCSO."""
    assert anti_raid.ROLES.DEPUTY_MINISTER_OF_COMMUNITY in anti_raid.STAFF_ROLE_IDS
    assert anti_raid.ROLES.PCSO not in anti_raid.STAFF_ROLE_IDS


@pytest.mark.parametrize("raises", [False, True])
def test_failed_narrowing_keeps_full_mode_and_disable_retries_restore(monkeypatch, tmp_path, raises):
    guild = _mode_env(monkeypatch, tmp_path)
    asyncio.run(anti_raid.enable_anti_raid(guild))
    calls = []

    async def restore(_guild):
        calls.append(True)
        if len(calls) == 1:
            if raises:
                raise RuntimeError("Discord unavailable")
            return ["One role: forbidden"]
        return []

    monkeypatch.setattr(anti_raid, "restore_role_permissions", restore)
    result = asyncio.run(anti_raid.enable_anti_raid(guild, mode=anti_raid.MODE_QUARANTINE_ONLY))
    state = anti_raid.get_anti_raid_state()
    assert result.active and not result.changed and result.failures
    assert result.mode == state["mode"] == anti_raid.MODE_FULL
    assert state["degraded"]

    result = asyncio.run(anti_raid.disable_anti_raid(guild))
    assert not result.active and result.successful
    assert len(calls) == 2


def test_narrowing_storage_failure_does_not_claim_a_mode_change(monkeypatch, tmp_path):
    guild = _mode_env(monkeypatch, tmp_path)
    asyncio.run(anti_raid.enable_anti_raid(guild))
    save = anti_raid.set_anti_raid_status

    def fail_narrowing(active, failures=(), mode=anti_raid.MODE_FULL, **kwargs):
        if mode == anti_raid.MODE_QUARANTINE_ONLY:
            raise OSError("disk full")
        save(active, failures, mode, **kwargs)

    monkeypatch.setattr(anti_raid, "set_anti_raid_status", fail_narrowing)
    result = asyncio.run(anti_raid.enable_anti_raid(guild, mode=anti_raid.MODE_QUARANTINE_ONLY))
    assert result.active and not result.changed and result.failures
    assert anti_raid.anti_raid_mode() == anti_raid.MODE_FULL


def test_restore_preserves_staff_changes_and_flags_lockdown_did_not_change(monkeypatch, tmp_path):
    _isolated_state(monkeypatch, tmp_path)
    role = _Role(10, discord.Permissions(embed_links=True, send_messages=True).value)
    guild = _Guild(1, [role])
    anti_raid.backup_role_permissions(guild)
    asyncio.run(anti_raid.disable_role_permissions(guild))
    # Staff revoke sending, grant role management, and allow attachments (which
    # were already off when lockdown began, so the bot never changed that bit).
    role.permissions.update(send_messages=False, manage_roles=True, attach_files=True)
    assert asyncio.run(anti_raid.restore_role_permissions(guild)) == []
    assert role.permissions.embed_links
    assert role.permissions.manage_roles and role.permissions.attach_files
    assert not role.permissions.send_messages


def test_new_and_deleted_roles_do_not_block_disabling_lockdown(monkeypatch, tmp_path):
    _isolated_state(monkeypatch, tmp_path)
    saved = _Role(10, discord.Permissions(embed_links=True).value)
    deleted = _Role(11, discord.Permissions(attach_files=True).value)
    guild = _Guild(1, [saved, deleted])
    anti_raid.backup_role_permissions(guild)
    asyncio.run(anti_raid.disable_role_permissions(guild))
    new_role = _Role(12, discord.Permissions(send_messages=True, attach_files=True).value)
    guild.roles = [saved, new_role]
    anti_raid.set_anti_raid_status(True)
    result = asyncio.run(anti_raid.disable_anti_raid(guild))
    assert result.successful and not result.active
    assert saved.permissions.embed_links
    assert not new_role.edits
    assert new_role.permissions.value == discord.Permissions(send_messages=True, attach_files=True).value


def test_restriction_retry_backs_up_new_roles_before_editing_them(monkeypatch, tmp_path):
    _, _, backup_file = _isolated_state(monkeypatch, tmp_path)
    original = _Role(10, discord.Permissions(embed_links=True).value)
    guild = _Guild(1, [original])
    anti_raid.backup_role_permissions(guild)
    asyncio.run(anti_raid.disable_role_permissions(guild))
    original_backup = json.loads(backup_file.read_text())["roles"]["10"]
    added = _Role(20, discord.Permissions(attach_files=True, manage_roles=True).value)
    guild.roles.append(added)
    assert asyncio.run(anti_raid.disable_role_permissions(guild)) == []
    assert not added.permissions.attach_files
    assert json.loads(backup_file.read_text())["roles"]["10"] == original_backup
    assert asyncio.run(anti_raid.restore_role_permissions(guild)) == []
    assert original.permissions.embed_links and added.permissions.attach_files
    assert added.permissions.manage_roles


def test_failed_new_role_backup_prevents_any_restriction(monkeypatch, tmp_path):
    _isolated_state(monkeypatch, tmp_path)
    guild = _Guild(1, [_Role(10)])
    anti_raid.backup_role_permissions(guild)
    guild.roles.append(_Role(20, discord.Permissions(attach_files=True).value))

    def no_storage(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(anti_raid, "atomic_write_json", no_storage)
    failures = asyncio.run(anti_raid.disable_role_permissions(guild))
    assert failures and "disk full" in failures[0]
    assert all(not role.edits for role in guild.roles)


@pytest.mark.parametrize("format", ["v2", "integer", "one_bit"])
def test_older_backups_restore_only_controlled_bits(monkeypatch, tmp_path, format):
    _, _, backup_file = _isolated_state(monkeypatch, tmp_path)
    original = discord.Permissions(use_external_apps=True, embed_links=True, send_messages=True)
    roles = {"10": {"use_external_apps": True} if format == "one_bit" else original.value}
    payload = {"version": 2, "guild_id": "1", "created_at": 1, "roles": roles} if format == "v2" else roles
    backup_file.write_text(json.dumps(payload))
    live = discord.Permissions(manage_roles=True)
    role = _Role(10, live.value)
    assert asyncio.run(anti_raid.restore_role_permissions(_Guild(1, [role]))) == []
    assert role.permissions.use_external_apps and role.permissions.manage_roles
    assert not role.permissions.send_messages
    assert role.permissions.embed_links == (format != "one_bit")


@pytest.mark.parametrize("saved", [True, -1, "nonsense", {"use_external_apps": "yes"}])
def test_invalid_backup_entry_prevents_all_restoration(monkeypatch, tmp_path, saved):
    _, _, backup_file = _isolated_state(monkeypatch, tmp_path)
    first, second = _Role(10), _Role(20)
    backup_file.write_text(json.dumps({"10": 1, "20": saved}))
    assert asyncio.run(anti_raid.restore_role_permissions(_Guild(1, [first, second])))
    assert not first.edits and not second.edits


class _EnforcementGuild(_Guild):
    def __init__(self):
        self.quarantine = _Role(anti_raid.QUARANTINE_ROLE_ID)
        self.normal = _Role(anti_raid.ROLES.MEMBER)
        self.quarantine.members = []
        self.normal.members = []
        super().__init__(1, [self.quarantine, self.normal])
        self.members = {}
        self.fetch_member = AsyncMock()

    def get_role(self, rid):
        return next((r for r in self.roles if r.id == rid), None)

    def get_member(self, uid):
        return self.members.get(uid)

    def get_channel(self, _cid):
        return None


class _EnforcementMember(_Member):
    def __init__(self, guild, uid=42, *, quarantined=False):
        super().__init__(uid)
        self.guild = guild
        self.roles = []
        self.joined_at = datetime.fromtimestamp(1_000 + uid, timezone.utc)
        self.calls = []
        self.add_error = None
        self.remove_error = None
        guild.members[uid] = self
        if quarantined:
            self.roles.append(guild.quarantine)
            guild.quarantine.members.append(self)

    async def add_roles(self, role, *, reason):
        self.calls.append(("add", role.id))
        if self.add_error:
            raise self.add_error
        if role not in self.roles:
            self.roles.append(role)
            role.members.append(self)

    async def remove_roles(self, role, *, reason):
        self.calls.append(("remove", role.id))
        if self.remove_error:
            raise self.remove_error
        if role in self.roles:
            self.roles.remove(role)
            role.members.remove(self)


def _enforcement_env(monkeypatch, tmp_path, *, active=True):
    _isolated_state(monkeypatch, tmp_path)
    monkeypatch.setattr(anti_raid, "_persist_moderation_notice", lambda *_args: None)
    monkeypatch.setattr(anti_raid, "_log_action", AsyncMock())
    monkeypatch.setattr(anti_raid, "get_join_watch_state", lambda: {"enabled": False, "context": "Test brief"})
    anti_raid.set_anti_raid_status(active, mode=anti_raid.MODE_QUARANTINE_ONLY)
    return _EnforcementGuild()


def _discord_error(cls=discord.Forbidden):
    response = types.SimpleNamespace(status=403, reason="Forbidden")
    return cls(response, "Missing permissions")


@pytest.mark.parametrize("active", [True, False])
def test_release_completes_onboarding_before_removing_quarantine(monkeypatch, tmp_path, active):
    guild = _enforcement_env(monkeypatch, tmp_path, active=active)
    member = _EnforcementMember(guild, quarantined=True)
    assert asyncio.run(anti_raid.release_quarantined_member(member, "Staff"))
    assert member.calls == [("add", guild.normal.id), ("remove", guild.quarantine.id)]
    assert guild.normal in member.roles and guild.quarantine not in member.roles


def test_release_normal_role_failure_keeps_quarantine(monkeypatch, tmp_path):
    guild = _enforcement_env(monkeypatch, tmp_path)
    member = _EnforcementMember(guild, quarantined=True)
    member.add_error = _discord_error()
    with pytest.raises(discord.Forbidden):
        asyncio.run(anti_raid.release_quarantined_member(member, "Staff"))
    assert member.roles == [guild.quarantine]
    assert member.calls == [("add", guild.normal.id)]


def test_release_missing_normal_role_keeps_quarantine(monkeypatch, tmp_path):
    guild = _enforcement_env(monkeypatch, tmp_path)
    member = _EnforcementMember(guild, quarantined=True)
    guild.roles.remove(guild.normal)
    with pytest.raises(ValueError, match="normal member role is missing"):
        asyncio.run(anti_raid.release_quarantined_member(member, "Staff"))
    assert member.roles == [guild.quarantine] and not member.calls


def test_release_removal_failure_can_be_retried_without_duplicate_onboarding(monkeypatch, tmp_path):
    guild = _enforcement_env(monkeypatch, tmp_path)
    member = _EnforcementMember(guild, quarantined=True)
    member.remove_error = _discord_error()
    with pytest.raises(discord.Forbidden):
        asyncio.run(anti_raid.release_quarantined_member(member, "Staff"))
    assert guild.quarantine in member.roles
    member.remove_error = None
    assert asyncio.run(anti_raid.release_quarantined_member(member, "Staff"))
    assert member.calls.count(("add", guild.normal.id)) == 1
    assert member.roles == [guild.normal]


def test_failed_quarantine_is_durable_and_retry_keeps_quarantine_only_mode(monkeypatch, tmp_path):
    guild = _enforcement_env(monkeypatch, tmp_path)
    member = _EnforcementMember(guild)
    member.add_error = _discord_error()
    outcome = asyncio.run(anti_raid.handle_new_member_anti_raid(member))
    assert outcome == anti_raid.AntiRaidJoinOutcome(True, False)
    # Reading a fresh state file simulates losing all in-memory operational state.
    state = anti_raid.get_anti_raid_state()
    assert state["degraded"] and str(member.id) in state["quarantine_failures"]
    vitals, warnings = anti_raid._vitals_block(guild, [])
    assert "Degraded" in vitals and any("Pending quarantines · 1" in w for w in warnings)
    assert "Not needed" in vitals
    monkeypatch.setattr(anti_raid, "backup_role_permissions", lambda _g: pytest.fail("must not widen mode"))
    monkeypatch.setattr(anti_raid, "disable_role_permissions", AsyncMock(side_effect=AssertionError("must not restrict roles")))
    member.add_error = None
    result = asyncio.run(anti_raid.retry_anti_raid_enforcement(guild))
    assert result.successful and result.mode == anti_raid.MODE_QUARANTINE_ONLY
    assert guild.quarantine in member.roles and guild.normal not in member.roles
    assert not anti_raid.get_anti_raid_state()["degraded"]
    assert not anti_raid.get_anti_raid_state()["quarantine_failures"]


def test_retry_failure_stays_queued_and_does_not_claim_success(monkeypatch, tmp_path):
    guild = _enforcement_env(monkeypatch, tmp_path)
    member = _EnforcementMember(guild)
    member.add_error = _discord_error(discord.HTTPException)
    asyncio.run(anti_raid.handle_new_member_anti_raid(member))
    result = asyncio.run(anti_raid.retry_anti_raid_enforcement(guild))
    assert result.failures and not result.successful
    assert str(member.id) in anti_raid.get_anti_raid_state()["quarantine_failures"]


def test_missing_quarantine_role_queues_join_and_preflight_preserves_mode(monkeypatch, tmp_path):
    guild = _enforcement_env(monkeypatch, tmp_path)
    guild.roles.remove(guild.quarantine)
    member = _EnforcementMember(guild)
    asyncio.run(anti_raid.handle_new_member_anti_raid(member))
    result = asyncio.run(anti_raid.retry_anti_raid_enforcement(guild))
    assert result.mode == anti_raid.anti_raid_mode() == anti_raid.MODE_QUARANTINE_ONLY
    assert "missing" in anti_raid.get_anti_raid_state()["quarantine_failures"][str(member.id)]
    guild.roles.append(guild.quarantine)
    assert asyncio.run(anti_raid.retry_anti_raid_enforcement(guild)).successful


def test_retry_fetches_uncached_member_and_discards_only_confirmed_departures(monkeypatch, tmp_path):
    guild = _enforcement_env(monkeypatch, tmp_path)
    member = _EnforcementMember(guild)
    anti_raid.set_anti_raid_status(True, mode=anti_raid.MODE_QUARANTINE_ONLY,
                                  quarantine_failures={"42": "retry", "43": "retry", "44": "retry"})
    monkeypatch.setattr(guild, "get_member", lambda _uid: None)
    guild.fetch_member.side_effect = [member, _discord_error(discord.NotFound), _discord_error()]
    result = asyncio.run(anti_raid.retry_anti_raid_enforcement(guild))
    assert guild.quarantine in member.roles
    assert result.failures
    assert set(anti_raid.get_anti_raid_state()["quarantine_failures"]) == {"44"}


def test_release_clears_pending_retry_and_later_retry_does_not_requarantine(monkeypatch, tmp_path):
    guild = _enforcement_env(monkeypatch, tmp_path)
    member = _EnforcementMember(guild, quarantined=True)
    anti_raid.set_anti_raid_status(True, mode=anti_raid.MODE_QUARANTINE_ONLY,
                                  quarantine_failures={str(member.id): "unfinished enforcement"})
    assert asyncio.run(anti_raid.release_quarantined_member(member, "Staff"))
    assert asyncio.run(anti_raid.retry_anti_raid_enforcement(guild)).successful
    assert member.roles == [guild.normal]


def test_retry_on_disabled_protection_does_not_enable_it(monkeypatch, tmp_path):
    guild = _enforcement_env(monkeypatch, tmp_path, active=False)
    member = _EnforcementMember(guild)
    anti_raid.set_anti_raid_status(False, quarantine_failures={str(member.id): "retry"})
    result = asyncio.run(anti_raid.retry_anti_raid_enforcement(guild))
    assert not result.active and not member.calls
    assert not anti_raid.is_anti_raid_enabled()


def _interaction():
    return types.SimpleNamespace(
        response=types.SimpleNamespace(edit_message=AsyncMock(), defer=AsyncMock()),
        edit_original_response=AsyncMock(), user=types.SimpleNamespace(id=7),
    )


def _select(view):
    return next(child for child in view.walk_children() if isinstance(child, anti_raid.QuarantineMemberSelect))


def _page_button(view, delta):
    return next(child for child in view.walk_children()
                if isinstance(child, anti_raid.QuarantinePageButton) and child.delta == delta)


def test_quarantine_pagination_reaches_every_member_and_clears_selection(monkeypatch, tmp_path):
    guild = _enforcement_env(monkeypatch, tmp_path)
    for uid in range(60):
        _EnforcementMember(guild, uid, quarantined=True)

    async def scenario():
        view = anti_raid.AntiRaidControlView(guild, 7)
        seen = set()
        assert _page_button(view, -1).disabled
        for page in range(3):
            options = _select(view).options
            assert len(options) == (25 if page < 2 else 10)
            seen.update(int(o.value) for o in options)
            assert all(not o.default for o in options)
            if page < 2:
                view.selected_member_ids = [int(options[0].value)]
                await _page_button(view, 1).callback(_interaction())
                assert not view.selected_member_ids
        assert seen == set(guild.members)
        assert _page_button(view, 1).disabled
        assert "Page 3/3" in json.dumps(view.to_components())
        await _page_button(view, -1).callback(_interaction())
        assert view.quarantine_page == 1

    asyncio.run(scenario())


def test_pagination_clamps_after_members_leave_and_discards_stale_selection(monkeypatch, tmp_path):
    guild = _enforcement_env(monkeypatch, tmp_path)
    for uid in range(26):
        _EnforcementMember(guild, uid, quarantined=True)

    async def scenario():
        view = anti_raid.AntiRaidControlView(guild, 7)
        await _page_button(view, 1).callback(_interaction())
        view.selected_member_ids = [0]
        guild.quarantine.members.remove(guild.members[0])
        view.render(keep_selection=True)
        assert view.quarantine_page == 0
        assert not view.selected_member_ids
        assert len(_select(view).options) == 25
        guild.quarantine.members.clear()
        view.render()
        assert not any(isinstance(c, anti_raid.QuarantineMemberSelect) for c in view.walk_children())

    asyncio.run(scenario())


def test_release_button_on_later_page_grants_membership_and_updates_history(monkeypatch, tmp_path):
    guild = _enforcement_env(monkeypatch, tmp_path)
    for uid in range(26):
        _EnforcementMember(guild, uid, quarantined=True)
    member = guild.members[0]
    anti_raid.record_recent_join(member)
    anti_raid.mark_join_quarantined(member.id)

    async def scenario():
        view = anti_raid.AntiRaidControlView(guild, 7)
        await _page_button(view, 1).callback(_interaction())
        assert int(_select(view).options[0].value) == member.id
        view.selected_member_ids = [member.id]
        view.render(keep_selection=True)
        assert _select(view).options[0].default
        button = next(c for c in view.walk_children() if isinstance(c, anti_raid.ReleaseButton))
        assert not button.disabled
        await button.callback(_interaction())
        assert "Released 1" in view.notice
        assert member.roles == [guild.normal]
        assert view.quarantine_page == 0

    asyncio.run(scenario())
    record = anti_raid._load_recent_joins()[0]
    assert not record["quarantined"] and record["released_by"] == "7"


def test_legacy_one_bit_backup_is_extended_before_restricting_more_flags(monkeypatch, tmp_path):
    _, _, backup_file = _isolated_state(monkeypatch, tmp_path)
    backup_file.write_text(json.dumps({"10": {"use_external_apps": True}}))
    role = _Role(10, discord.Permissions(attach_files=True, send_messages=True).value)
    guild = _Guild(1, [role])
    assert asyncio.run(anti_raid.disable_role_permissions(guild)) == []
    assert not role.permissions.attach_files
    assert asyncio.run(anti_raid.restore_role_permissions(guild)) == []
    assert role.permissions.use_external_apps and role.permissions.attach_files
    assert role.permissions.send_messages


def test_retry_in_full_mode_keeps_original_backup_and_retries_pending_joins(monkeypatch, tmp_path):
    guild = _enforcement_env(monkeypatch, tmp_path, active=False)
    guild.normal.permissions = discord.Permissions(attach_files=True, send_messages=True)
    assert asyncio.run(anti_raid.enable_anti_raid(guild)).successful
    backup = open(anti_raid.PERMISSIONS_BACKUP_FILE).read()
    member = _EnforcementMember(guild)
    member.add_error = _discord_error()
    asyncio.run(anti_raid.handle_new_member_anti_raid(member))
    member.add_error = None
    result = asyncio.run(anti_raid.retry_anti_raid_enforcement(guild))
    assert result.successful and result.mode == anti_raid.MODE_FULL
    assert open(anti_raid.PERMISSIONS_BACKUP_FILE).read() == backup
    assert not guild.normal.permissions.attach_files
    assert guild.quarantine in member.roles
    assert asyncio.run(anti_raid.disable_anti_raid(guild)).successful
    assert guild.normal.permissions.attach_files


def test_cancelled_quarantine_remains_durable_for_retry(monkeypatch, tmp_path):
    guild = _enforcement_env(monkeypatch, tmp_path)
    member = _EnforcementMember(guild)
    member.add_error = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(anti_raid.handle_new_member_anti_raid(member))
    assert anti_raid.get_anti_raid_state()["degraded"]
    assert str(member.id) in anti_raid.get_anti_raid_state()["quarantine_failures"]
    member.add_error = None
    assert asyncio.run(anti_raid.retry_anti_raid_enforcement(guild)).successful
    assert guild.quarantine in member.roles


def test_pending_quarantines_survive_history_pruning_and_mode_changes(monkeypatch, tmp_path):
    _isolated_state(monkeypatch, tmp_path)
    pending = {str(uid): "retry" for uid in range(150)}
    anti_raid.set_anti_raid_status(True, mode=anti_raid.MODE_QUARANTINE_ONLY,
                                  quarantine_failures=pending)
    anti_raid._save_recent_joins([])
    anti_raid.set_anti_raid_status(False)
    anti_raid.set_anti_raid_status(True)
    assert anti_raid.get_anti_raid_state()["quarantine_failures"] == pending


def test_retry_button_retries_pending_joins_without_widening_mode(monkeypatch, tmp_path):
    guild = _enforcement_env(monkeypatch, tmp_path)
    member = _EnforcementMember(guild)
    member.add_error = _discord_error()
    asyncio.run(anti_raid.handle_new_member_anti_raid(member))
    member.add_error = None

    async def scenario():
        view = anti_raid.AntiRaidControlView(guild, 7)
        button = next(c for c in view.walk_children() if isinstance(c, anti_raid.RetryEnforcementButton))
        await button.callback(_interaction())
        assert "successfully" in view.notice
        assert guild.quarantine in member.roles
        assert anti_raid.anti_raid_mode() == anti_raid.MODE_QUARANTINE_ONLY

    asyncio.run(scenario())


def test_release_storage_failure_keeps_quarantine_and_pending_retry(monkeypatch, tmp_path):
    guild = _enforcement_env(monkeypatch, tmp_path)
    member = _EnforcementMember(guild, quarantined=True)
    anti_raid.set_anti_raid_status(True, mode=anti_raid.MODE_QUARANTINE_ONLY,
                                  quarantine_failures={str(member.id): "retry"})

    def no_storage(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(anti_raid, "atomic_write_json", no_storage)
    with pytest.raises(OSError):
        asyncio.run(anti_raid.release_quarantined_member(member, "Staff"))
    assert member.roles == [guild.quarantine] and not member.calls
    assert str(member.id) in anti_raid.get_anti_raid_state()["quarantine_failures"]


def test_concurrent_release_wins_over_stale_pending_retry_snapshot(monkeypatch, tmp_path):
    guild = _enforcement_env(monkeypatch, tmp_path)
    member = _EnforcementMember(guild, quarantined=True)
    anti_raid.set_anti_raid_status(True, mode=anti_raid.MODE_QUARANTINE_ONLY,
                                  quarantine_failures={str(member.id): "retry"})

    async def scenario():
        # Retry's preflight gets the lock first, but release is already waiting
        # by the time retry takes its pending-members snapshot.
        async with anti_raid._mode_lock:
            retry = asyncio.create_task(anti_raid.retry_anti_raid_enforcement(guild))
            await asyncio.sleep(0)
            release = asyncio.create_task(anti_raid.release_quarantined_member(member, "Staff"))
            await asyncio.sleep(0)
        result, released = await asyncio.gather(retry, release)
        assert result.successful and released
        assert member.roles == [guild.normal]
        assert member.calls == [("add", guild.normal.id), ("remove", guild.quarantine.id)]

    asyncio.run(scenario())


def test_concurrent_disable_prevents_pending_quarantine_retry(monkeypatch, tmp_path):
    guild = _enforcement_env(monkeypatch, tmp_path)
    member = _EnforcementMember(guild)
    anti_raid.set_anti_raid_status(True, mode=anti_raid.MODE_QUARANTINE_ONLY,
                                  quarantine_failures={str(member.id): "retry"})

    async def scenario():
        async with anti_raid._mode_lock:
            retry = asyncio.create_task(anti_raid.retry_anti_raid_enforcement(guild))
            await asyncio.sleep(0)
            disable = asyncio.create_task(anti_raid.disable_anti_raid(guild))
            await asyncio.sleep(0)
        result, disabled = await asyncio.gather(retry, disable)
        assert not result.active and not disabled.active
        assert not member.calls

    asyncio.run(scenario())


def test_saved_role_moved_above_bot_blocks_restore_until_hierarchy_is_fixed(monkeypatch, tmp_path):
    _isolated_state(monkeypatch, tmp_path)
    role = _Role(10, discord.Permissions(embed_links=True).value)
    bot_top = _Role(20)
    role.position, bot_top.position = 1, 5
    guild = _Guild(1, [role, bot_top])
    guild.me = types.SimpleNamespace(top_role=bot_top)
    anti_raid.backup_role_permissions(guild)
    assert asyncio.run(anti_raid.disable_role_permissions(guild)) == []
    anti_raid.set_anti_raid_status(True)
    role.position = 6
    result = asyncio.run(anti_raid.disable_anti_raid(guild))
    assert result.active and result.failures and not role.permissions.embed_links
    role.position = 1
    result = asyncio.run(anti_raid.disable_anti_raid(guild))
    assert not result.active and result.successful and role.permissions.embed_links
