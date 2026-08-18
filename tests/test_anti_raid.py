"""Focused safety tests for anti-raid persistence and mode transitions."""

import asyncio
from datetime import datetime, timezone
import json
import os
import sys
import types

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
    role = _Role(10, value=12345)
    guild = _Guild(100, [role])

    anti_raid.backup_role_permissions(guild)
    payload = json.loads(backup_file.read_text(encoding="utf-8"))
    role.permissions = _Permissions(0)
    failures = asyncio.run(anti_raid.restore_role_permissions(guild))

    assert payload["version"] == anti_raid.ROLE_PERMISSION_BACKUP_VERSION
    assert payload["guild_id"] == "100"
    assert payload["roles"] == {"10": 12345}
    assert failures == []
    assert role.permissions.value == 12345


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


def test_wrong_guild_or_incomplete_backup_never_edits_roles(monkeypatch, tmp_path):
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
    assert prepared == []
    assert "does not cover every live role" in coverage_failures[0]
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


def test_member_join_fails_closed_if_protection_activates_before_normal_role(monkeypatch):
    async def initially_inactive(_member):
        return anti_raid.AntiRaidJoinOutcome(False, False)

    async def protection_activated(_member, _role):
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

    result = asyncio.run(anti_raid.handle_new_member_roles(object(), object()))

    assert result == anti_raid.AntiRaidJoinOutcome(True, False)


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
             if getattr(anti_raid.ROLES, n) in anti_raid.STAFF_ROLE_IDS}
    assert named == panel, f"command allows {named}, panel allows {panel}"


def test_the_most_present_staff_can_reach_the_control_centre():
    """PCSO covers the three staff who are actually in the room most days."""
    assert anti_raid.ROLES.PCSO in anti_raid.STAFF_ROLE_IDS
    assert anti_raid.ROLES.DEPUTY_MINISTER_OF_COMMUNITY in anti_raid.STAFF_ROLE_IDS
