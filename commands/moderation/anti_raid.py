"""Anti-raid lockdown state, join quarantine, and the private staff dashboard."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Iterable

import discord
from discord.interactions import Interaction

from config import CHANNELS, JSON_DATA_DIR, PERMISSIONS_BACKUP_FILE, ROLES
from commands.moderation.join_watch import (
    MAX_CONTEXT_CHARS as JW_MAX_CONTEXT_CHARS,
    MAX_SCANNED_MESSAGES as JW_MAX_MESSAGES,
    TIMEOUT_HOURS as JW_TIMEOUT_HOURS,
    announce_toggle as announce_join_watch_toggle,
    get_join_watch_state,
    set_join_watch_state,
)
from lib.core.file_operations import atomic_write_json, set_file_status

logger = logging.getLogger(__name__)

# Both files live in the persistent data directory so protection and operational
# context survive a process restart regardless of the bot's working directory.
ANTI_RAID_FILE = os.path.join(JSON_DATA_DIR, "anti_raid_active")
ANTI_RAID_STATE_FILE = os.path.join(JSON_DATA_DIR, "anti_raid_state.json")
ANTI_RAID_RECENT_FILE = os.path.join(JSON_DATA_DIR, "anti_raid_recent_joins.json")
QUARANTINE_ROLE_ID = 962009285116710922
ANTI_RAID_LOG_CHANNEL_ID = 1172677237988929646
BATCH_SIZE = 10
MAX_RECENT_JOINS = 100
QUARANTINE_PAGE_SIZE = 25
RECENT_JOIN_TTL_SECONDS = 24 * 60 * 60
ROLE_PERMISSION_BACKUP_VERSION = 3
# Kept in step with the /anti-raid command gate in setup_commands: opening the panel and
# being able to press anything on it have to be the same set, or the people most likely to
# be in the room during a raid get a control centre that does nothing.
STAFF_ROLE_IDS = {ROLES.MINISTER, ROLES.CABINET, ROLES.BORDER_FORCE,
                  ROLES.DEPUTY_MINISTER_OF_COMMUNITY}

# Two ways to be switched on. FULL is the original: quarantine every join AND strip the
# risky permissions from every editable role. QUARANTINE_ONLY does the first half and
# leaves existing members completely alone - the right response to a botnet walking in
# quietly, where the threat is the new accounts and there is no reason to take embeds and
# attachments off the people already here.
MODE_FULL = "full"
MODE_QUARANTINE_ONLY = "quarantine"
VALID_MODES = (MODE_FULL, MODE_QUARANTINE_ONLY)

# Permissions stripped from every editable role while a raid lockdown is active.
# send_messages is deliberately left untouched: quarantine handles new members,
# while established members can still communicate during an incident.
RESTRICTED_RAID_PERMS = {
    "use_external_apps": False,
    "mention_everyone": False,
    "embed_links": False,
    "attach_files": False,
}

_mode_lock = asyncio.Lock()


@dataclass(frozen=True)
class AntiRaidTransition:
    """Result of an idempotent anti-raid mode transition."""

    active: bool
    changed: bool
    failures: tuple[str, ...] = ()
    mode: str = MODE_FULL

    @property
    def successful(self) -> bool:
        return not self.failures


@dataclass(frozen=True)
class AntiRaidJoinOutcome:
    """Join enforcement result used to gate normal onboarding roles."""

    protection_active: bool
    quarantined: bool


def _anti_raid_state_payload(
    active: bool,
    failures: Iterable[str] = (),
    mode: str = MODE_FULL,
    quarantine_failures: dict[str, str] | None = None,
) -> dict[str, Any]:
    clean_failures = [str(failure)[:500] for failure in failures if str(failure).strip()]
    pending = dict(quarantine_failures or {})
    return {
        "version": 1,
        "active": bool(active),
        "mode": mode if mode in VALID_MODES else MODE_FULL,
        "degraded": bool(clean_failures or pending),
        "failures": clean_failures[:20],
        # Unlike the bounded recent-joins display, pending enforcement must survive
        # both a large raid and a restart until retried or explicitly released.
        "quarantine_failures": pending,
        "updated_at": int(time.time()),
    }


def _load_anti_raid_state() -> dict[str, Any]:
    """Load the backed-up state, failing closed if it is corrupt.

    The old extensionless marker is migrated on first read so an already-active
    deployment gains disaster-recovery coverage without rewriting its permission
    backup or changing the live mode.
    """
    if os.path.exists(ANTI_RAID_STATE_FILE):
        try:
            with open(ANTI_RAID_STATE_FILE, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            if not isinstance(payload, dict) or not isinstance(payload.get("active"), bool):
                raise ValueError("state root or active flag is invalid")
            raw_failures = payload.get("failures", [])
            if not isinstance(raw_failures, list):
                raise ValueError("failures is not a list")
            failures = [str(value)[:500] for value in raw_failures[:20]]
            pending = payload.get("quarantine_failures", {})
            if not isinstance(pending, dict) or any(
                not str(uid).isdecimal() or not isinstance(reason, str)
                for uid, reason in pending.items()
            ):
                raise ValueError("quarantine failures are invalid")
            mode = str(payload.get("mode") or MODE_FULL)
            return {
                "version": 1,
                "active": payload["active"],
                # State written before modes existed was always the full lockdown, so an
                # in-flight deployment keeps restoring the permissions it actually backed up.
                "mode": mode if mode in VALID_MODES else MODE_FULL,
                "degraded": bool(payload.get("degraded", False) or failures or pending),
                "failures": failures,
                "quarantine_failures": {str(uid): reason[:500] for uid, reason in pending.items()},
                "updated_at": int(payload.get("updated_at", 0) or 0),
            }
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            logger.error("Anti-raid state is unreadable; failing closed: %s", exc)
            return _anti_raid_state_payload(
                True,
                (f"The persisted anti-raid state is unreadable: {exc}",),
            )

    if os.path.exists(ANTI_RAID_FILE):
        migrated = _anti_raid_state_payload(True)
        try:
            atomic_write_json(ANTI_RAID_STATE_FILE, migrated, indent=2)
        except Exception:
            logger.exception("Could not migrate legacy anti-raid marker to JSON state")
        return migrated

    return _anti_raid_state_payload(False)


def get_anti_raid_state() -> dict[str, Any]:
    return dict(_load_anti_raid_state())


def is_anti_raid_enabled() -> bool:
    return bool(_load_anti_raid_state()["active"])


def anti_raid_mode() -> str:
    """Which lockdown is running. Meaningless when protection is off."""
    return str(_load_anti_raid_state().get("mode") or MODE_FULL)


def set_anti_raid_status(active: bool, failures: Iterable[str] = (),
                         mode: str = MODE_FULL, *,
                         quarantine_failures: dict[str, str] | None = None) -> None:
    """Persist canonical JSON state first; retain the old marker for compatibility."""
    atomic_write_json(
        ANTI_RAID_STATE_FILE,
        _anti_raid_state_payload(
            active, failures, mode,
            _load_anti_raid_state()["quarantine_failures"]
            if quarantine_failures is None else quarantine_failures,
        ),
        indent=2,
    )
    try:
        set_file_status(ANTI_RAID_FILE, active)
    except Exception:
        # The backed-up JSON file is authoritative. A legacy marker failure must
        # not reverse or misreport a successfully persisted transition.
        logger.warning("Could not update legacy anti-raid marker", exc_info=True)


def _safe_name(value: Any, fallback: str = "Unknown member") -> str:
    """Return a compact, single-line snapshot suitable for JSON and embeds."""
    name = " ".join(str(value or "").split())[:100]
    return name or fallback


def _normalise_recent_joins(records: Iterable[Any], now: int | None = None) -> list[dict[str, Any]]:
    """Validate, prune, sort, and bound persisted join records."""
    current = int(time.time() if now is None else now)
    cutoff = current - RECENT_JOIN_TTL_SECONDS
    clean: list[dict[str, Any]] = []
    for raw in records:
        if not isinstance(raw, dict):
            continue
        try:
            user_id = str(int(raw["user_id"]))
            joined_at = int(raw["joined_at"])
        except (KeyError, TypeError, ValueError):
            continue
        if joined_at < cutoff or joined_at > current + 300:
            continue
        try:
            account_created_at = int(raw.get("account_created_at") or joined_at)
        except (TypeError, ValueError):
            account_created_at = joined_at
        record: dict[str, Any] = {
            "user_id": user_id,
            "username": _safe_name(raw.get("username"), user_id),
            "joined_at": joined_at,
            "account_created_at": account_created_at,
            "quarantined": bool(raw.get("quarantined", False)),
        }
        if raw.get("released_at") is not None:
            try:
                record["released_at"] = int(raw["released_at"])
            except (TypeError, ValueError):
                pass
        if raw.get("released_by") is not None:
            record["released_by"] = str(raw["released_by"])
        clean.append(record)
    clean.sort(key=lambda row: row["joined_at"])
    # One row per member: a leave-and-rejoin used to append a second record, which read
    # downstream as two different accounts joining together.
    deduped: dict[str, dict[str, Any]] = {}
    for row in clean:
        prev = deduped.get(row["user_id"])
        if prev is not None:
            row["quarantined"] = row["quarantined"] or prev.get("quarantined", False)
        deduped[row["user_id"]] = row
    latest = sorted(deduped.values(), key=lambda row: row["joined_at"])
    return latest[-MAX_RECENT_JOINS:]


def _load_recent_joins(now: int | None = None) -> list[dict[str, Any]]:
    if not os.path.exists(ANTI_RAID_RECENT_FILE):
        return []
    try:
        with open(ANTI_RAID_RECENT_FILE, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("Could not read anti-raid join history: %s", exc)
        return []
    records = payload.get("records", []) if isinstance(payload, dict) else payload
    return _normalise_recent_joins(records if isinstance(records, list) else [], now)


def _save_recent_joins(records: Iterable[Any], now: int | None = None) -> None:
    atomic_write_json(
        ANTI_RAID_RECENT_FILE,
        {"version": 1, "records": _normalise_recent_joins(records, now)},
        indent=2,
    )


def record_recent_join(member: discord.Member, *, now: int | None = None) -> None:
    """Persist a bounded join snapshot without making any guilt determination."""
    joined_at = int(time.time() if now is None else now)
    created_at = getattr(member, "created_at", None)
    records = _load_recent_joins(joined_at)
    records.append(
        {
            "user_id": str(member.id),
            "username": _safe_name(getattr(member, "display_name", None), str(member.id)),
            "joined_at": joined_at,
            "account_created_at": int(created_at.timestamp()) if created_at else joined_at,
            "quarantined": False,
        }
    )
    _save_recent_joins(records, joined_at)


def mark_join_quarantined(user_id: int, *, now: int | None = None) -> None:
    current = int(time.time() if now is None else now)
    records = _load_recent_joins(current)
    for record in reversed(records):
        if record["user_id"] == str(user_id):
            record["quarantined"] = True
            record.pop("released_at", None)
            record.pop("released_by", None)
            break
    _save_recent_joins(records, current)


def mark_members_released(
    user_ids: Iterable[int], actor_id: int, *, now: int | None = None
) -> None:
    current = int(time.time() if now is None else now)
    wanted = {str(user_id) for user_id in user_ids}
    records = _load_recent_joins(current)
    for record in records:
        if record["user_id"] in wanted and record.get("quarantined"):
            record["quarantined"] = False
            record["released_at"] = current
            record["released_by"] = str(actor_id)
    _save_recent_joins(records, current)


def _join_velocity(records: Iterable[dict[str, Any]], now: int | None = None) -> tuple[int, int]:
    current = int(time.time() if now is None else now)
    timestamps = [int(row.get("joined_at", 0)) for row in records]
    return (
        sum(timestamp >= current - 600 for timestamp in timestamps),
        sum(timestamp >= current - 3600 for timestamp in timestamps),
    )


def _editable_roles(guild: discord.Guild) -> list[discord.Role]:
    """Roles the bot can actually edit: unmanaged and strictly below its top role.

    Roles at or above the bot in the hierarchy 403 on edit, which previously
    left every enable/disable pass permanently degraded.
    """
    roles = [role for role in guild.roles if not getattr(role, "managed", False)]
    me = getattr(guild, "me", None)
    top_role = getattr(me, "top_role", None) if me is not None else None
    if top_role is None:
        return roles
    top_position = getattr(top_role, "position", None)
    if top_position is None:
        return roles
    return [role for role in roles if getattr(role, "position", 0) < top_position]


def backup_role_permissions(guild: discord.Guild) -> None:
    """Store a guild-bound, complete snapshot of every role we will restrict."""
    role_permissions = {
        str(role.id): role.permissions.value
        for role in _editable_roles(guild)
    }
    if not role_permissions:
        raise ValueError("guild has no restorable unmanaged roles")
    atomic_write_json(
        PERMISSIONS_BACKUP_FILE,
        {
            "version": ROLE_PERMISSION_BACKUP_VERSION,
            "guild_id": str(guild.id),
            "created_at": int(time.time()),
            "restricted_permissions": list(RESTRICTED_RAID_PERMS),
            "roles": role_permissions,
        },
        indent=2,
    )


def _read_role_permission_backup(guild: discord.Guild) -> dict[str, Any]:
    """Validate every entry before either restoring or extending a backup."""
    with open(PERMISSIONS_BACKUP_FILE, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("backup root is not an object")

    is_structured = any(
        key in payload for key in ("version", "guild_id", "roles", "created_at")
    )
    if is_structured:
        if payload.get("version") not in (2, ROLE_PERMISSION_BACKUP_VERSION):
            raise ValueError("backup version is unsupported")
        if str(payload.get("guild_id")) != str(guild.id):
            raise ValueError("backup belongs to a different guild")
        if (
            isinstance(payload.get("created_at"), bool)
            or not isinstance(payload.get("created_at"), int)
            or payload["created_at"] < 0
        ):
            raise ValueError("backup timestamp is invalid")
        roles = payload.get("roles")
        fields = (
            payload.get("restricted_permissions")
            if payload["version"] == ROLE_PERMISSION_BACKUP_VERSION
            else list(RESTRICTED_RAID_PERMS)
        )
    else:
        roles = payload
        fields = list(RESTRICTED_RAID_PERMS)
    if not isinstance(roles, dict) or not roles:
        raise ValueError("backup contains no role permissions")
    if not isinstance(fields, list) or not fields or any(
        not isinstance(field, str) or field not in RESTRICTED_RAID_PERMS for field in fields
    ) or set(fields) != set(RESTRICTED_RAID_PERMS):
        raise ValueError("backup restricted permissions are invalid")
    for uid, saved in roles.items():
        if not str(uid).isdecimal():
            raise ValueError("backup role ID is invalid")
        if isinstance(saved, dict):
            if (
                not saved or not set(saved).issubset(RESTRICTED_RAID_PERMS)
                or any(not isinstance(value, bool) for value in saved.values())
                or (not (is_structured and payload["version"] == ROLE_PERMISSION_BACKUP_VERSION)
                    and set(saved) != {"use_external_apps"})
            ):
                raise ValueError("backup permission flags are invalid")
        elif isinstance(saved, bool) or not isinstance(saved, (int, str)) or int(saved) < 0:
            raise ValueError("backup permission integer is invalid")
    return {
        "version": ROLE_PERMISSION_BACKUP_VERSION,
        "guild_id": str(guild.id),
        "created_at": payload["created_at"] if is_structured else int(time.time()),
        "restricted_permissions": fields,
        "roles": roles,
    }


def _extend_role_permission_backup(guild: discord.Guild) -> None:
    """Snapshot new roles before a retry restricts them; never replace old entries."""
    payload = _read_role_permission_backup(guild)
    added = False
    for role in _editable_roles(guild):
        saved = payload["roles"].get(str(role.id))
        if saved is None:
            payload["roles"][str(role.id)] = role.permissions.value
            added = True
        elif isinstance(saved, dict):
            # The oldest lockdown only controlled external apps. Before a new
            # enforcement pass controls more flags, snapshot those flags too.
            current = discord.Permissions(role.permissions.value)
            for field in RESTRICTED_RAID_PERMS:
                if field not in saved:
                    saved[field] = getattr(current, field)
                    added = True
    if added:
        atomic_write_json(PERMISSIONS_BACKUP_FILE, payload, indent=2)


def _prepare_role_permission_restore(
    guild: discord.Guild,
) -> tuple[list[tuple[discord.Role, discord.Permissions]], list[str]]:
    """Merge only lockdown-controlled flags into current roles, including old backups."""
    if not os.path.exists(PERMISSIONS_BACKUP_FILE):
        return [], ["The role-permission backup is missing; lockdown remains enabled."]
    try:
        payload = _read_role_permission_backup(guild)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return [], [f"The role-permission backup could not be validated: {exc}"]

    editable = _editable_roles(guild)
    editable_ids = {role.id for role in editable}
    blocked = [
        f"{role.name}: backed-up role is no longer editable; lockdown remains enabled."
        for role in guild.roles
        if str(role.id) in payload["roles"] and role.id not in editable_ids
    ]
    if blocked:
        return [], blocked
    prepared: list[tuple[discord.Role, discord.Permissions]] = []
    for role in editable:
        # A new role that we never restricted needs no restoration. Deleted roles
        # likewise need no replacement or edit.
        if str(role.id) not in payload["roles"]:
            continue
        saved = payload["roles"][str(role.id)]
        permissions = discord.Permissions(role.permissions.value)
        if isinstance(saved, dict):
            permissions.update(**{field: value for field, value in saved.items() if value})
        else:
            original = discord.Permissions(int(saved))
            permissions.update(**{
                field: True for field in payload["restricted_permissions"] if getattr(original, field)
            })
        prepared.append((role, permissions))
    return prepared, []


async def restore_role_permissions(guild: discord.Guild) -> list[str]:
    """Restore editable roles, returning every failure instead of hiding partial work."""
    prepared, validation_failures = _prepare_role_permission_restore(guild)
    if validation_failures:
        return validation_failures
    failures: list[str] = []
    for offset in range(0, len(prepared), BATCH_SIZE):
        batch = prepared[offset : offset + BATCH_SIZE]
        pending: list[tuple[discord.Role, Any]] = []
        for role, permissions in batch:
            pending.append(
                (
                    role,
                    role.edit(
                        permissions=permissions,
                        reason="Anti-raid lockdown disabled by staff",
                    ),
                )
            )
        if pending:
            results = await asyncio.gather(
                *(operation for _, operation in pending), return_exceptions=True
            )
            for (role, _), result in zip(pending, results):
                if isinstance(result, Exception):
                    failures.append(f"{role.name}: {result}")
                    logger.warning("Failed to restore permissions for %s: %s", role.name, result)
        if offset + BATCH_SIZE < len(prepared):
            await asyncio.sleep(1)
    return failures


async def disable_role_permissions(guild: discord.Guild) -> list[str]:
    """Apply the restricted permission set and report partial failures."""
    try:
        _extend_role_permission_backup(guild)
    except (OSError, ValueError, TypeError) as exc:
        return [f"Could not back up roles before restriction: {exc}"]
    roles = _editable_roles(guild)
    failures: list[str] = []
    for offset in range(0, len(roles), BATCH_SIZE):
        batch = roles[offset : offset + BATCH_SIZE]
        pending: list[tuple[discord.Role, Any]] = []
        for role in batch:
            try:
                permissions = discord.Permissions(role.permissions.value)
                permissions.update(**RESTRICTED_RAID_PERMS)
                pending.append(
                    (
                        role,
                        role.edit(
                            permissions=permissions,
                            reason="Anti-raid lockdown enabled by staff",
                        ),
                    )
                )
            except (TypeError, ValueError) as exc:
                failures.append(f"{role.name}: {exc}")
        if pending:
            results = await asyncio.gather(
                *(operation for _, operation in pending), return_exceptions=True
            )
            for (role, _), result in zip(pending, results):
                if isinstance(result, Exception):
                    failures.append(f"{role.name}: {result}")
                    logger.warning("Failed to restrict permissions for %s: %s", role.name, result)
        if offset + BATCH_SIZE < len(roles):
            await asyncio.sleep(1)
    return failures


async def enable_anti_raid(guild: discord.Guild,
                           mode: str | None = MODE_FULL, *,
                           retry_only: bool = False) -> AntiRaidTransition:
    """Enable, retry, or switch mode without ever replacing an active-mode backup.

    MODE_QUARANTINE_ONLY quarantines joins and touches no role permissions at all, so it
    takes no backup and has nothing to restore on the way out.
    """
    async with _mode_lock:
        state = _load_anti_raid_state()
        current_mode = state.get("mode", MODE_FULL)
        if retry_only and not state["active"]:
            return AntiRaidTransition(active=False, changed=False, mode=current_mode)
        mode = current_mode if mode is None else (mode if mode in VALID_MODES else MODE_FULL)
        if state["active"] and not state["degraded"] and current_mode == mode:
            return AntiRaidTransition(active=True, changed=False, mode=mode)

        # Stepping down from the full lockdown: hand the permissions back before
        # narrowing, or they stay stripped with nothing left recording that they were.
        if state["active"] and current_mode == MODE_FULL and mode == MODE_QUARANTINE_ONLY:
            try:
                failures = await restore_role_permissions(guild)
            except Exception as exc:
                logger.exception("Could not restore permissions while narrowing anti-raid")
                failures = [f"Could not restore permissions: {exc}"]
            # Keep the full-mode recovery path until EVERY restore succeeded.
            next_mode = current_mode if failures else MODE_QUARANTINE_ONLY
            try:
                set_anti_raid_status(True, failures, next_mode)
            except Exception as exc:
                logger.exception("Could not persist anti-raid mode change")
                failures = [*failures, f"Could not persist mode change: {exc}"]
                next_mode = current_mode
            return AntiRaidTransition(
                active=True, changed=next_mode != current_mode,
                mode=next_mode, failures=tuple(failures),
            )
        quarantine_role = guild.get_role(QUARANTINE_ROLE_ID)
        if quarantine_role is None:
            failures = ("Lockdown was not enabled because the quarantine role is missing.",)
            if state["active"]:
                try:
                    set_anti_raid_status(True, failures, current_mode)
                except Exception:
                    logger.exception("Could not persist degraded anti-raid preflight state")
            return AntiRaidTransition(
                active=bool(state["active"]),
                changed=False,
                failures=failures,
                mode=current_mode,
            )
        guild_member = getattr(guild, "me", None)
        if guild_member is not None:
            if not guild_member.guild_permissions.manage_roles:
                failures = ("Lockdown was not enabled because the bot lacks Manage Roles.",)
                if state["active"]:
                    try:
                        set_anti_raid_status(True, failures, current_mode)
                    except Exception:
                        logger.exception("Could not persist degraded anti-raid preflight state")
                return AntiRaidTransition(
                    active=bool(state["active"]),
                    changed=False,
                    failures=failures,
                    mode=current_mode,
                )
            if quarantine_role >= guild_member.top_role:
                failures = ("Lockdown was not enabled because the quarantine role is above the bot.",)
                if state["active"]:
                    try:
                        set_anti_raid_status(True, failures, current_mode)
                    except Exception:
                        logger.exception("Could not persist degraded anti-raid preflight state")
                return AntiRaidTransition(
                    active=bool(state["active"]),
                    changed=False,
                    failures=failures,
                    mode=current_mode,
                )

        changed = not state["active"] or current_mode != mode
        if mode == MODE_QUARANTINE_ONLY:
            # Nothing to back up and nothing to strip: joins get quarantined by
            # handle_new_member_anti_raid, and everyone already here is untouched.
            try:
                set_anti_raid_status(True, (), MODE_QUARANTINE_ONLY)
            except Exception as exc:
                logger.exception("Could not persist quarantine-only anti-raid state")
                return AntiRaidTransition(active=False, changed=False, mode=mode,
                                          failures=(f"Lockdown was not enabled: {exc}",))
            return AntiRaidTransition(active=True, changed=changed, mode=mode)

        if changed:
            try:
                backup_role_permissions(guild)
                # Persist a deliberately degraded active state before slow Discord
                # edits. A crash here still quarantines joins and exposes Retry.
                set_anti_raid_status(
                    True,
                    ("Role restriction enforcement did not finish; retry it from the control centre.",),
                    MODE_FULL,
                )
            except Exception as exc:
                logger.exception("Could not initialise anti-raid lockdown")
                return AntiRaidTransition(
                    active=bool(state["active"]),
                    changed=False,
                    failures=(f"Lockdown was not enabled: {exc}",),
                    mode=current_mode,
                )
        try:
            failures = await disable_role_permissions(guild)
        except Exception as exc:
            logger.exception("Unexpected anti-raid restriction failure")
            failures = [f"Unexpected restriction failure: {exc}"]
        try:
            set_anti_raid_status(True, failures, MODE_FULL)
        except Exception as exc:
            logger.exception("Could not persist final anti-raid enforcement state")
            failures = [*failures, f"Could not persist enforcement state: {exc}"]
        return AntiRaidTransition(active=True, changed=changed, failures=tuple(failures),
                                  mode=MODE_FULL)


async def disable_anti_raid(guild: discord.Guild) -> AntiRaidTransition:
    """Restore first and only clear the active flag after a complete restore."""
    async with _mode_lock:
        state = _load_anti_raid_state()
        if not state["active"]:
            return AntiRaidTransition(active=False, changed=False)
        if state.get("mode") == MODE_QUARANTINE_ONLY:
            # No permissions were ever taken, so there is no backup to apply. Running the
            # restore anyway could push a stale backup from an older full lockdown over
            # whatever the roles look like now.
            set_anti_raid_status(False)
            return AntiRaidTransition(active=False, changed=True, mode=MODE_QUARANTINE_ONLY)
        try:
            failures = await restore_role_permissions(guild)
        except Exception as exc:
            logger.exception("Unexpected anti-raid restore failure")
            failures = [f"Unexpected restore failure: {exc}"]
        if failures:
            try:
                set_anti_raid_status(True, failures)
            except Exception as exc:
                logger.exception("Could not persist failed anti-raid restore state")
                failures = [*failures, f"Could not persist restore failure: {exc}"]
            return AntiRaidTransition(active=True, changed=False, failures=tuple(failures))
        try:
            set_anti_raid_status(False)
        except Exception as exc:
            logger.exception("Could not clear anti-raid active marker")
            return AntiRaidTransition(
                active=True,
                changed=False,
                failures=(f"Permissions restored but the active marker could not be cleared: {exc}",),
            )
        return AntiRaidTransition(active=False, changed=True)


async def send_backup_file(guild: discord.Guild) -> None:
    channel = guild.get_channel(ANTI_RAID_LOG_CHANNEL_ID)
    if channel and os.path.exists(PERMISSIONS_BACKUP_FILE):
        try:
            await channel.send(
                "Backup of role permissions before enabling anti-raid:",
                file=discord.File(PERMISSIONS_BACKUP_FILE),
            )
        except (discord.Forbidden, discord.HTTPException) as exc:
            logger.warning("Could not send anti-raid permission backup: %s", exc)


async def _announce_protection_toggle(
    client: Any, actor: Any, active: bool, failures: int
) -> None:
    """Tell the police station who enabled or disabled anti-raid protection."""
    channel = client.get_channel(CHANNELS.POLICE_STATION)
    if channel is None:
        try:
            channel = await client.fetch_channel(CHANNELS.POLICE_STATION)
        except Exception:
            logger.warning("Could not reach the police station for the anti-raid notice")
            return
    if active:
        text = (
            "## 🔴 Anti-raid protection enabled\n"
            f"{actor.mention} enabled protection - new joins are quarantined "
            "and high-abuse role permissions are restricted."
        )
    else:
        text = (
            "## 🟢 Anti-raid protection disabled\n"
            f"{actor.mention} disabled protection - normal join handling restored."
        )
    if failures:
        text += f"\n-# {failures} role operation(s) failed; see /anti-raid for details."
    view = discord.ui.LayoutView(timeout=None)
    card = discord.ui.Container(accent_colour=0xE74C3C if active else 0x2ECC71)
    card.add_item(discord.ui.TextDisplay(text))
    view.add_item(card)
    try:
        await channel.send(view=view, allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        logger.exception("Could not post the anti-raid toggle notice")


async def _log_action(guild: discord.Guild, message: str) -> None:
    channel = guild.get_channel(ANTI_RAID_LOG_CHANNEL_ID)
    if channel:
        try:
            await channel.send(message, allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden, discord.HTTPException) as exc:
            logger.warning("Could not write anti-raid audit event: %s", exc)


def _persist_moderation_notice(user_id: int, title: str, body: str) -> None:
    """Best-effort durable notice; never store raid evidence or matched content."""
    try:
        from lib.features.inbox import create_notification

        create_notification(
            user_id,
            category="moderation",
            title=title,
            body=body,
        )
    except Exception:
        # Enforcement already succeeded. Keep the Discord path available while
        # surfacing the notification failure to operators.
        logger.exception("Could not persist moderation inbox notice for %s", user_id)


def _failure_summary(failures: Iterable[str]) -> str:
    failures = list(failures)
    if not failures:
        return ""
    shown = "\n".join(f"• {failure}" for failure in failures[:5])
    if len(failures) > 5:
        shown += f"\n• …and {len(failures) - 5} more"
    return shown[:1000]


def _quarantined_members(guild: discord.Guild) -> list[discord.Member]:
    role = guild.get_role(QUARANTINE_ROLE_ID)
    if role is None:
        return []
    return sorted(
        role.members,
        key=lambda member: getattr(member, "joined_at", None) or member.created_at,
        reverse=True,
    )


def _member_option(member: discord.Member) -> discord.SelectOption:
    joined_at = getattr(member, "joined_at", None)
    joined = int(joined_at.timestamp()) if joined_at else 0
    description = f"ID {member.id}"
    if joined:
        description += f" • joined <t:{joined}:R>"
    return discord.SelectOption(
        label=_safe_name(member.display_name, str(member.id)),
        value=str(member.id),
        description=description[:100],
    )


def _panel_theme(active: bool, degraded: bool) -> tuple[int, str]:
    """Accent colour and status line for the current protection state."""
    if active and degraded:
        return 0xF39C12, (
            "🟠 **Protection enabled - degraded**\n"
            "-# Some protection operations need attention; check failures and retry enforcement."
        )
    if active:
        return 0xE74C3C, (
            "🔴 **Protection enabled**\n"
            "-# New members are quarantined according to the mode below."
        )
    return 0x2ECC71, (
        "🟢 **Protection disabled**\n"
        "-# Normal join handling is active."
    )


def _vitals_block(guild: discord.Guild, records: list[dict[str, Any]]) -> tuple[str, list[str]]:
    """One compact status block plus any backup warnings worth their own panel line."""
    mode_state = _load_anti_raid_state()
    active = bool(mode_state["active"])
    degraded = bool(mode_state["degraded"])
    ten_minutes, one_hour = _join_velocity(records)

    needs_backup = active and mode_state["mode"] == MODE_FULL
    _, backup_failures = _prepare_role_permission_restore(guild) if needs_backup else ([], [])
    if not needs_backup:
        backup_ok, backup_text = True, "Not needed"
    elif not os.path.exists(PERMISSIONS_BACKUP_FILE):
        backup_ok, backup_text = False, "Missing"
    elif backup_failures:
        backup_ok, backup_text = False, "Invalid"
    else:
        backup_ok, backup_text = True, "Ready"
    role_ok = guild.get_role(QUARANTINE_ROLE_ID) is not None
    enforcement_ok = not (active and (degraded or not role_ok))

    def check(ok: bool, warn: bool) -> str:
        return "✅" if ok else ("⚠️" if warn else "◻️")

    lines = [
        f"📈 **Join velocity** · **{ten_minutes}** in the last 10 min · **{one_hour}** in the last hour",
        f"{check(backup_ok, active)} **Recovery backup** · {backup_text}",
        f"{check(role_ok, True)} **Quarantine role** · {'Ready' if role_ok else 'Missing'}",
        f"{check(enforcement_ok, True)} **Enforcement** · {'Healthy' if enforcement_ok else 'Degraded'}",
    ]

    warnings: list[str] = []
    if active and backup_failures:
        warnings.append(f"⚠️ **Recovery warning**\n{_failure_summary(backup_failures)[:600]}")
    if active and degraded and mode_state["failures"]:
        warnings.append(f"⚠️ **Enforcement failures**\n{_failure_summary(mode_state['failures'])[:600]}")
    pending = mode_state["quarantine_failures"]
    if pending:
        warnings.append(
            f"⚠️ **Pending quarantines · {len(pending)}**\n"
            f"{_failure_summary(f'{uid}: {reason}' for uid, reason in pending.items())[:600]}"
        )
    return "\n".join(lines), warnings


def _quarantine_block(
    quarantined: list[discord.Member], selected: set[int]
) -> str:
    """Just the count. The dropdown below already lists every name and is the thing you
    act on, so printing them again cost half the panel and said nothing extra."""
    if not quarantined:
        return "### 👥 Quarantine · empty\n-# No members currently hold the quarantine role."
    count = len(quarantined)
    line = (f"### 👥 Quarantine · {count} member{'s' if count != 1 else ''}\n"
            "-# Pick anyone below to release them.")
    if selected:
        line += f" · {len(selected)} selected"
    return line


def _panel_context(state: dict[str, Any]) -> str:
    """The panel shares one 4000-char container with the vitals, quarantine and
    recent-joins blocks, so the context is clipped here rather than shown whole -
    the arm announcement and the edit modal both carry the full wording."""
    context = state["context"]
    if len(context) <= 400:
        return context
    return context[:400].rstrip() + "… (open Edit context to read it all)"


def _join_watch_block() -> str:
    """Say what the watch is doing, not what its brief says.

    The brief is a screenful of rules by design, and printing it here pushed everything
    below it off the panel. "Edit context" shows it in full to anyone who wants it.
    """
    state = get_join_watch_state()
    context = " ".join(str(state.get("context") or "").split())
    first_line = context.split(".")[0][:80] if context else "not set"
    if state["enabled"]:
        return (
            "### 🔎 AI join-watch · Armed\n"
            f"-# Listening to everyone who joins from now on: their first {JW_MAX_MESSAGES} "
            f"messages are AI-screened, and a confident troll verdict gets a "
            f"{JW_TIMEOUT_HOURS}h timeout and a police station report.\n"
            f"-# **Brief:** {first_line}… · open **Edit context** to read or change it."
        )
    return (
        "### 🔎 AI join-watch · Disarmed\n"
        f"-# When armed, members who join are listened to and their first "
        f"{JW_MAX_MESSAGES} messages AI-screened for raid trolling.\n"
        f"-# **Brief:** {first_line}… · open **Edit context** to read or change it."
    )


def _recent_joins_block(records: list[dict[str, Any]]) -> str:
    lines = ["### 🕒 Recent joins"]
    for record in reversed(records[-8:]):
        account_age_days = max(
            0, (record["joined_at"] - record.get("account_created_at", record["joined_at"])) // 86400
        )
        if record.get("released_at"):
            icon = "🔓"
        elif record.get("quarantined"):
            icon = "🛡️"
        else:
            icon = "👋"
        lines.append(
            f"{icon} `{record['username']}` (`{record['user_id']}`) · <t:{record['joined_at']}:R> "
            f"· account {account_age_days}d old"
        )
    if len(lines) == 1:
        lines.append("-# No joins recorded in the last 24 hours.")
    return "\n".join(lines)[:900]


def _is_staff(member: discord.Member) -> bool:
    return any(getattr(role, "id", None) in STAFF_ROLE_IDS for role in getattr(member, "roles", []))


class QuarantineMemberSelect(discord.ui.Select):
    def __init__(self, members: list[discord.Member], selected: set[int] | None = None):
        visible = members[:QUARANTINE_PAGE_SIZE]
        options = [_member_option(member) for member in visible]
        for option in options:
            option.default = int(option.value) in (selected or set())
        super().__init__(
            placeholder="Select quarantined members to release…",
            min_values=1,
            max_values=len(visible),
            options=options,
        )

    async def callback(self, interaction: Interaction) -> None:
        dashboard: AntiRaidControlView = self.view  # type: ignore[assignment]
        dashboard.selected_member_ids = [int(value) for value in self.values]
        dashboard.notice = (
            f"Selected {len(dashboard.selected_member_ids)} member(s). "
            "Use Release selected to remove the quarantine role."
        )
        dashboard.render(keep_selection=True)
        await interaction.response.edit_message(
            view=dashboard,
            allowed_mentions=discord.AllowedMentions.none(),
        )


class QuarantinePageButton(discord.ui.Button):
    def __init__(self, delta: int, *, disabled: bool):
        super().__init__(
            label="Previous" if delta < 0 else "Next",
            style=discord.ButtonStyle.secondary, disabled=disabled,
        )
        self.delta = delta

    async def callback(self, interaction: Interaction) -> None:
        dashboard: AntiRaidControlView = self.view  # type: ignore[assignment]
        dashboard.quarantine_page += self.delta
        dashboard.render()
        await interaction.response.edit_message(
            view=dashboard, allowed_mentions=discord.AllowedMentions.none(),
        )


class AntiRaidModeButton(discord.ui.Button):
    def __init__(self, active: bool):
        super().__init__(
            label="Disable protection" if active else "Enable protection",
            emoji="🔓" if active else "🛑",
            style=discord.ButtonStyle.success if active else discord.ButtonStyle.danger,
        )
        self.target_active = not active

    async def callback(self, interaction: Interaction) -> None:
        dashboard: AntiRaidControlView = self.view  # type: ignore[assignment]
        # Role edits are batched and rate-limited, so this can take a while;
        # show a working panel immediately and lock the controls meanwhile.
        dashboard.notice = (
            "⏳ Enabling protection - backing up and restricting role permissions. "
            "This can take a minute…"
            if self.target_active
            else "⏳ Disabling protection - restoring role permissions. This can take a minute…"
        )
        dashboard.busy = True
        dashboard.render()
        await interaction.response.edit_message(
            view=dashboard,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        try:
            if self.target_active:
                result = await enable_anti_raid(dashboard.guild)
                if result.changed:
                    await send_backup_file(dashboard.guild)
                action = "enabled"
            else:
                result = await disable_anti_raid(dashboard.guild)
                action = "disabled"
        finally:
            dashboard.busy = False

        if result.failures:
            dashboard.notice = (
                f"⚠️ Protection remains {'enabled' if result.active else 'disabled'}; "
                f"{len(result.failures)} operation(s) failed.\n{_failure_summary(result.failures)}"
            )
        elif result.changed:
            dashboard.notice = f"✅ Protection {action} by {interaction.user.display_name}."
        else:
            dashboard.notice = f"No change needed; protection was already {action}."
        await _log_action(
            dashboard.guild,
            f"Anti-raid protection action by {interaction.user} ({interaction.user.id}): "
            f"active={result.active}, failures={len(result.failures)}.",
        )
        dashboard.render()
        await interaction.edit_original_response(
            view=dashboard,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        if result.changed:
            await _announce_protection_toggle(
                interaction.client, interaction.user, result.active, len(result.failures)
            )


class QuarantineOnlyToggleButton(discord.ui.Button):
    """Switch between the full lockdown and quarantine-only.

    Useful when the threat is arriving rather than already inside: a botnet joining
    quietly needs its joins held, and there is no reason to take embeds and attachments
    off the members who were here first.
    """

    def __init__(self, active: bool, mode: str):
        self.target_mode = MODE_FULL if mode == MODE_QUARANTINE_ONLY else MODE_QUARANTINE_ONLY
        narrowing = self.target_mode == MODE_QUARANTINE_ONLY
        super().__init__(
            label="Quarantine joins only" if narrowing else "Also restrict roles",
            emoji="🔒" if narrowing else "🛡️",
            style=discord.ButtonStyle.secondary,
            disabled=not active,
        )

    async def callback(self, interaction: Interaction) -> None:
        dashboard: AntiRaidControlView = self.view  # type: ignore[assignment]
        narrowing = self.target_mode == MODE_QUARANTINE_ONLY
        dashboard.notice = (
            "⏳ Narrowing to quarantine-only - handing role permissions back…"
            if narrowing else
            "⏳ Widening to the full lockdown - backing up and restricting roles…"
        )
        dashboard.busy = True
        dashboard.render()
        await interaction.response.edit_message(
            view=dashboard, allowed_mentions=discord.AllowedMentions.none())
        try:
            result = await enable_anti_raid(dashboard.guild, mode=self.target_mode)
            if result.changed and self.target_mode == MODE_FULL:
                await send_backup_file(dashboard.guild)
        finally:
            dashboard.busy = False
        if result.failures:
            dashboard.notice = (
                f"⚠️ Mode is now {result.mode}; {len(result.failures)} operation(s) failed.\n"
                f"{_failure_summary(result.failures)}")
        elif result.changed:
            dashboard.notice = (
                f"✅ Quarantine-only: joins are held, existing members untouched. "
                f"Set by {interaction.user.display_name}."
                if narrowing else
                f"✅ Full lockdown: joins held and role permissions restricted. "
                f"Set by {interaction.user.display_name}.")
        else:
            dashboard.notice = "No change needed."
        await _log_action(
            dashboard.guild,
            f"Anti-raid mode set to {result.mode} by {interaction.user} ({interaction.user.id})",
        )
        dashboard.render()
        await interaction.edit_original_response(
            view=dashboard, allowed_mentions=discord.AllowedMentions.none())


class EnableQuarantineOnlyButton(discord.ui.Button):
    """Start protection in quarantine-only, without passing through the full lockdown.

    Enabling full and then narrowing would strip every role's permissions and hand them
    straight back - a lot of API churn and a window where members lose embeds for no
    reason - so this is its own entry point rather than a two-step.
    """

    def __init__(self):
        super().__init__(
            label="Enable quarantine only",
            emoji="🔒",
            style=discord.ButtonStyle.primary,
        )

    async def callback(self, interaction: Interaction) -> None:
        dashboard: AntiRaidControlView = self.view  # type: ignore[assignment]
        dashboard.notice = "⏳ Enabling quarantine-only protection…"
        dashboard.busy = True
        dashboard.render()
        await interaction.response.edit_message(
            view=dashboard, allowed_mentions=discord.AllowedMentions.none())
        try:
            result = await enable_anti_raid(dashboard.guild, mode=MODE_QUARANTINE_ONLY)
        finally:
            dashboard.busy = False
        if result.failures:
            dashboard.notice = (
                f"⚠️ {len(result.failures)} operation(s) failed.\n"
                f"{_failure_summary(result.failures)}")
        else:
            dashboard.notice = (
                f"✅ Quarantine-only protection enabled by {interaction.user.display_name}. "
                "New joins are held; nobody already here is affected.")
        await _log_action(
            dashboard.guild,
            f"Anti-raid enabled (quarantine-only) by {interaction.user} ({interaction.user.id})",
        )
        dashboard.render()
        await interaction.edit_original_response(
            view=dashboard, allowed_mentions=discord.AllowedMentions.none())


class JoinWatchToggleButton(discord.ui.Button):
    def __init__(self, armed: bool):
        super().__init__(
            label="Disarm join-watch" if armed else "Arm join-watch",
            emoji="🔎",
            style=discord.ButtonStyle.success if armed else discord.ButtonStyle.danger,
        )
        self.target_armed = not armed

    async def callback(self, interaction: Interaction) -> None:
        dashboard: AntiRaidControlView = self.view  # type: ignore[assignment]
        if not _is_staff(interaction.user):
            await interaction.response.send_message(
                "Only staff can operate join-watch.", ephemeral=True
            )
            return
        state = set_join_watch_state(self.target_armed)
        dashboard.notice = (
            "🔎 Join-watch armed; new joiners' first messages are now AI-screened."
            if state["enabled"]
            else "💤 Join-watch disarmed."
        )
        dashboard.render()
        await interaction.response.edit_message(
            view=dashboard,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        await announce_join_watch_toggle(interaction.client, interaction.user, state["enabled"])
        await _log_action(
            dashboard.guild,
            f"Join-watch {'armed' if state['enabled'] else 'disarmed'} by "
            f"{interaction.user} ({interaction.user.id}).",
        )


class JoinWatchContextModal(discord.ui.Modal):
    def __init__(self, dashboard: "AntiRaidControlView"):
        super().__init__(title="Join-watch incident context")
        self.dashboard = dashboard
        self.context_input = discord.ui.TextInput(
            label="Why is screening on right now?",
            style=discord.TextStyle.paragraph,
            default=get_join_watch_state()["context"],
            placeholder="Leave empty to clear it and screen without an incident context.",
            max_length=JW_MAX_CONTEXT_CHARS,
            required=False,
        )
        self.add_item(self.context_input)

    async def on_submit(self, interaction: Interaction) -> None:
        # Editing the wording is not the same as deciding to screen people, and arming from
        # here surprised staff who were only tidying the text. The button next to it arms.
        # An empty box clears the context rather than reinstating the default paragraph.
        was_armed = get_join_watch_state()["enabled"]
        new_context = (self.context_input.value or "").strip()
        set_join_watch_state(was_armed, new_context)
        armed_note = "screening is armed" if was_armed else "screening is still off"
        self.dashboard.notice = (
            f"🔎 Join-watch context updated; {armed_note}."
            if new_context
            else f"🔎 Join-watch context cleared; {armed_note}."
        )
        self.dashboard.render()
        await interaction.response.edit_message(
            view=self.dashboard,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        await _log_action(
            self.dashboard.guild,
            f"Join-watch context updated by {interaction.user} ({interaction.user.id}).",
        )


class JoinWatchContextButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Edit context", emoji="📝", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: Interaction) -> None:
        dashboard: AntiRaidControlView = self.view  # type: ignore[assignment]
        if not _is_staff(interaction.user):
            await interaction.response.send_message(
                "Only staff can operate join-watch.", ephemeral=True
            )
            return
        await interaction.response.send_modal(JoinWatchContextModal(dashboard))


class RefreshButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: Interaction) -> None:
        dashboard: AntiRaidControlView = self.view  # type: ignore[assignment]
        dashboard.notice = "Dashboard refreshed."
        dashboard.render()
        await interaction.response.edit_message(
            view=dashboard,
            allowed_mentions=discord.AllowedMentions.none(),
        )


class RetryEnforcementButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Retry enforcement",
            emoji="🔁",
            style=discord.ButtonStyle.primary,
        )

    async def callback(self, interaction: Interaction) -> None:
        dashboard: AntiRaidControlView = self.view  # type: ignore[assignment]
        dashboard.notice = "⏳ Retrying enforcement for the current mode and pending quarantines…"
        dashboard.busy = True
        dashboard.render()
        await interaction.response.edit_message(
            view=dashboard,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        try:
            result = await retry_anti_raid_enforcement(dashboard.guild)
        finally:
            dashboard.busy = False
        if result.failures:
            dashboard.notice = (
                f"⚠️ Enforcement retry still has {len(result.failures)} failure(s).\n"
                f"{_failure_summary(result.failures)}"
            )
        elif result.active:
            dashboard.notice = "✅ Anti-raid enforcement retry completed successfully."
        else:
            dashboard.notice = "Protection is disabled; no enforcement was applied."
        await _log_action(
            dashboard.guild,
            f"Anti-raid enforcement retry by {interaction.user} ({interaction.user.id}): "
            f"failures={len(result.failures)}.",
        )
        dashboard.render()
        await interaction.edit_original_response(
            view=dashboard,
            allowed_mentions=discord.AllowedMentions.none(),
        )


class ReleaseButton(discord.ui.Button):
    def __init__(self, *, disabled: bool = True):
        super().__init__(
            label="Release selected",
            emoji="🔓",
            style=discord.ButtonStyle.danger,
            disabled=disabled,
        )

    async def callback(self, interaction: Interaction) -> None:
        dashboard: AntiRaidControlView = self.view  # type: ignore[assignment]
        selected = list(dashboard.selected_member_ids)
        if not selected:
            await interaction.response.send_message(
                "Select at least one quarantined member first.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        quarantine_role = dashboard.guild.get_role(QUARANTINE_ROLE_ID)
        if quarantine_role is None:
            dashboard.notice = "❌ The quarantine role no longer exists; nobody was changed."
            dashboard.render()
            await interaction.edit_original_response(view=dashboard)
            return

        released: list[int] = []
        failed: list[str] = []
        for member_id in selected:
            member = dashboard.guild.get_member(member_id)
            if member is None:
                failed.append(f"{member_id}: member is no longer in the guild")
                continue
            try:
                if not await release_quarantined_member(member, interaction.user):
                    continue
                released.append(member_id)
                _persist_moderation_notice(
                    member_id,
                    "Quarantine released",
                    "A staff member released your anti-raid quarantine and granted normal membership.",
                )
            except (discord.Forbidden, discord.HTTPException, ValueError, OSError) as exc:
                failed.append(f"{member.display_name}: {exc}")
        if released:
            try:
                mark_members_released(released, interaction.user.id)
            except Exception:
                logger.exception("Could not update anti-raid release history")
        dashboard.notice = f"✅ Released {len(released)} member(s)."
        if failed:
            dashboard.notice += f"\n⚠️ {len(failed)} failed.\n{_failure_summary(failed)}"
        await _log_action(
            dashboard.guild,
            f"Anti-raid quarantine release by {interaction.user} ({interaction.user.id}): "
            f"released={released}, failures={len(failed)}.",
        )
        dashboard.render()
        await interaction.edit_original_response(
            view=dashboard,
            allowed_mentions=discord.AllowedMentions.none(),
        )


class AntiRaidControlView(discord.ui.LayoutView):
    """Owner-checked, staff-only incident dashboard; never bans members.

    Rendered entirely with Components V2: one accent-coloured container whose
    content is rebuilt from persisted state on every action.
    """

    def __init__(self, guild: discord.Guild, author_id: int):
        super().__init__(timeout=300)
        self.guild = guild
        self.author_id = author_id
        self.selected_member_ids: list[int] = []
        self.quarantine_page = 0
        self.notice: str | None = None
        self.message: discord.Message | None = None
        self.busy = False
        self.render()

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Only the staff member who opened this control centre can use it.",
                ephemeral=True,
            )
            return False
        if not _is_staff(interaction.user):
            await interaction.response.send_message(
                "You no longer have permission to operate anti-raid controls.",
                ephemeral=True,
            )
            return False
        return True

    def render(self, *, keep_selection: bool = False) -> None:
        """Rebuild the whole panel from persisted state and current selection."""
        if not keep_selection:
            self.selected_member_ids = []
        self.clear_items()

        mode_state = _load_anti_raid_state()
        active = bool(mode_state["active"])
        degraded = bool(mode_state["degraded"])
        records = _load_recent_joins()
        quarantined = _quarantined_members(self.guild)
        page_count = max(1, (len(quarantined) + QUARANTINE_PAGE_SIZE - 1) // QUARANTINE_PAGE_SIZE)
        self.quarantine_page = max(0, min(self.quarantine_page, page_count - 1))
        start = self.quarantine_page * QUARANTINE_PAGE_SIZE
        visible = quarantined[start : start + QUARANTINE_PAGE_SIZE]
        visible_ids = {member.id for member in visible}
        self.selected_member_ids = [uid for uid in self.selected_member_ids if uid in visible_ids]
        selected = set(self.selected_member_ids)

        accent, status = _panel_theme(active, degraded)
        if active:
            status += (
                "\n🔒 **Quarantine only** - new joins are held; existing members untouched."
                if mode_state.get("mode") == MODE_QUARANTINE_ONLY
                else "\n🛡️ **Full lockdown** - joins held and role permissions restricted."
            )
        panel = discord.ui.Container(accent_colour=accent)

        # Both ways to switch protection on sit together at the top. A Section accessory
        # holds a single component, so the header is plain text and the two buttons share
        # a row beneath it rather than one being stranded down with Refresh.
        panel.add_item(discord.ui.TextDisplay(f"## 🛡️ Anti-Raid Control Centre\n{status}"))
        mode_controls: list[discord.ui.Item] = [AntiRaidModeButton(active)]
        if active:
            mode_controls.append(
                QuarantineOnlyToggleButton(active, mode_state.get("mode", MODE_FULL)))
        else:
            mode_controls.append(EnableQuarantineOnlyButton())
        panel.add_item(discord.ui.ActionRow(*mode_controls))
        panel.add_item(discord.ui.Separator())

        vitals, warnings = _vitals_block(self.guild, records)
        panel.add_item(discord.ui.TextDisplay(vitals))
        for warning in warnings:
            panel.add_item(discord.ui.TextDisplay(warning))
        panel.add_item(discord.ui.Separator())

        panel.add_item(discord.ui.TextDisplay(_join_watch_block()))
        panel.add_item(
            discord.ui.ActionRow(
                JoinWatchToggleButton(get_join_watch_state()["enabled"]),
                JoinWatchContextButton(),
            )
        )
        panel.add_item(discord.ui.Separator())

        panel.add_item(discord.ui.TextDisplay(_quarantine_block(quarantined, selected)))
        if quarantined:
            if page_count > 1:
                panel.add_item(discord.ui.TextDisplay(
                    f"-# Page {self.quarantine_page + 1}/{page_count} · "
                    f"showing {start + 1}–{start + len(visible)} of {len(quarantined)}"
                ))
            panel.add_item(discord.ui.ActionRow(QuarantineMemberSelect(visible, selected)))
            release_controls: list[discord.ui.Item] = [ReleaseButton(disabled=not selected)]
            if page_count > 1:
                release_controls.extend([
                    QuarantinePageButton(-1, disabled=self.quarantine_page == 0),
                    QuarantinePageButton(1, disabled=self.quarantine_page == page_count - 1),
                ])
            panel.add_item(discord.ui.ActionRow(*release_controls))
        panel.add_item(discord.ui.Separator())

        panel.add_item(discord.ui.TextDisplay(_recent_joins_block(records)))
        if self.notice:
            panel.add_item(discord.ui.Separator())
            panel.add_item(discord.ui.TextDisplay(f"📋 **Last action**\n{self.notice[:400]}"))

        controls: list[discord.ui.Item] = [RefreshButton()]
        if active and degraded:
            controls.append(RetryEnforcementButton())
        panel.add_item(discord.ui.ActionRow(*controls))
        panel.add_item(
            discord.ui.TextDisplay(
                "-# Private staff view · account age is context only · no automatic bans"
            )
        )
        self.add_item(panel)
        if self.busy:
            for child in self.walk_children():
                if hasattr(child, "disabled"):
                    child.disabled = True

    async def on_timeout(self) -> None:
        for child in self.walk_children():
            if hasattr(child, "disabled"):
                child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


async def open_anti_raid_control(interaction: Interaction) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            "The anti-raid control centre is only available inside the server.",
            ephemeral=True,
        )
        return
    view = AntiRaidControlView(interaction.guild, interaction.user.id)
    await interaction.response.send_message(
        view=view,
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )
    try:
        view.message = await interaction.original_response()
    except discord.HTTPException:
        pass


def _client_of(member: discord.Member) -> Any:
    """The bot client behind a member object, without threading it through every caller."""
    return getattr(getattr(member, "guild", None), "_state", None) and member.guild._state._get_client()


async def handle_new_member_anti_raid(member: discord.Member) -> AntiRaidJoinOutcome:
    """Record and quarantine a join, returning a fail-closed onboarding gate."""
    try:
        record_recent_join(member)
    except Exception:
        # History is operational context, not a prerequisite for enforcement.
        logger.exception("Could not persist recent anti-raid join for %s", member.id)
    # Silent joiners never reach join_watch, which only screens members who speak, so the
    # batch-creation check runs off the join itself. Reporting only; it bans nothing.
    try:
        from commands.moderation.join_clusters import evaluate_joins
        asyncio.create_task(evaluate_joins(_client_of(member), _load_recent_joins()))
    except Exception:
        logger.debug("join-cluster evaluation could not be scheduled", exc_info=True)
    if not is_anti_raid_enabled():
        return AntiRaidJoinOutcome(protection_active=False, quarantined=False)

    async with _mode_lock:
        return await _quarantine_member_locked(member)


def _set_pending_quarantine(user_id: int, reason: str | None) -> None:
    """Called under the mode lock so retries, releases, and mode changes cannot race."""
    state = _load_anti_raid_state()
    pending = dict(state["quarantine_failures"])
    if reason is None:
        pending.pop(str(user_id), None)
    else:
        pending[str(user_id)] = reason[:500]
    set_anti_raid_status(
        state["active"], state["failures"], state["mode"], quarantine_failures=pending,
    )


async def _quarantine_member_locked(member: discord.Member) -> AntiRaidJoinOutcome:
    """Enforce a join or retry, keeping a durable entry until enforcement succeeds."""
    if not is_anti_raid_enabled():
        return AntiRaidJoinOutcome(False, False)
    try:
        _set_pending_quarantine(member.id, "Quarantine enforcement did not finish; retry required.")
    except Exception:
        logger.exception("Could not persist pending quarantine for %s", member.id)
    quarantine_role = member.guild.get_role(QUARANTINE_ROLE_ID)
    failure = "quarantine role is missing" if quarantine_role is None else None
    if quarantine_role is not None:
        try:
            if quarantine_role not in getattr(member, "roles", []):
                await member.add_roles(quarantine_role, reason="Anti-raid protection is active")
        except (discord.Forbidden, discord.HTTPException) as exc:
            failure = str(exc)
    if failure is not None:
        logger.error("Could not quarantine joining member %s: %s", member.id, failure)
        try:
            _set_pending_quarantine(member.id, failure)
        except Exception:
            logger.exception("Could not persist quarantine failure for %s", member.id)
        await _log_action(
            member.guild, f"⚠️ Anti-raid failed to quarantine {member} ({member.id}): {failure}",
        )
        return AntiRaidJoinOutcome(True, False)

    try:
        _set_pending_quarantine(member.id, None)
        mark_join_quarantined(member.id)
    except Exception:
        logger.exception("Could not update anti-raid quarantine state for %s", member.id)
    _persist_moderation_notice(
        member.id, "Anti-raid quarantine applied",
        "You were placed in the temporary quarantine role because anti-raid protection was active when you joined.",
    )
    await _log_action(
        member.guild, f"🛡️ Anti-raid quarantined {member} ({member.id}) on join or enforcement retry.",
    )
    return AntiRaidJoinOutcome(True, True)


async def retry_anti_raid_enforcement(guild: discord.Guild) -> AntiRaidTransition:
    """Retry the current mode and pending joins, including after a process restart."""
    result = await enable_anti_raid(guild, mode=None, retry_only=True)
    if not result.active:
        return result
    for uid in list(_load_anti_raid_state()["quarantine_failures"]):
        async with _mode_lock:
            state = _load_anti_raid_state()
            # Another panel may have disabled protection or released this member
            # since we took the snapshot. Never undo that staff action.
            if not state["active"]:
                break
            if uid not in state["quarantine_failures"]:
                continue
            member = guild.get_member(int(uid))
            if member is None:
                try:
                    member = await guild.fetch_member(int(uid))
                except discord.NotFound:
                    _set_pending_quarantine(int(uid), None)
                    continue
                except (discord.Forbidden, discord.HTTPException) as exc:
                    _set_pending_quarantine(int(uid), f"Could not fetch member: {exc}")
                    continue
            await _quarantine_member_locked(member)
    state = _load_anti_raid_state()
    failures = [*state["failures"], *(
        f"{uid}: {reason}" for uid, reason in state["quarantine_failures"].items()
    )]
    return AntiRaidTransition(
        active=state["active"], changed=result.changed, mode=state["mode"],
        failures=tuple(failures),
    )


async def release_quarantined_member(member: discord.Member, actor: Any) -> bool:
    """Complete onboarding before removing the hold, even while protection is active."""
    async with _mode_lock:
        quarantine_role = member.guild.get_role(QUARANTINE_ROLE_ID)
        if quarantine_role is None:
            raise ValueError("The quarantine role no longer exists")
        if quarantine_role not in member.roles:
            return False
        normal_role = member.guild.get_role(ROLES.MEMBER)
        if normal_role is None:
            raise ValueError("The normal member role is missing; quarantine was kept")
        reason = f"Released from anti-raid quarantine by {actor}"
        # Clearing a pending retry first prevents it re-quarantining a released
        # member. If storage fails, leave the quarantine in place for staff.
        _set_pending_quarantine(member.id, None)
        if normal_role not in member.roles:
            await member.add_roles(normal_role, reason=reason)
        await member.remove_roles(quarantine_role, reason=reason)
        return True


async def grant_normal_member_role_if_safe(
    member: discord.Member,
    role: discord.Role | None,
) -> bool:
    """Grant normal access only while protection is atomically confirmed inactive."""
    async with _mode_lock:
        if is_anti_raid_enabled():
            return False
        if role is not None:
            await member.add_roles(role, reason="Normal member onboarding")
        return True


async def handle_new_member_roles(
    member: discord.Member,
    normal_role: discord.Role | None,
) -> AntiRaidJoinOutcome:
    """Apply exactly one safe role path: quarantine or normal membership, never both."""
    outcome = await handle_new_member_anti_raid(member)
    if outcome.protection_active:
        return outcome
    if not await grant_normal_member_role_if_safe(member, normal_role):
        # Protection activated between checks. Actually enforce the hold rather
        # than silently leaving a member without either role or a retry entry.
        async with _mode_lock:
            if is_anti_raid_enabled():
                return await _quarantine_member_locked(member)
            # Protection may have been disabled again while waiting for the lock.
            if normal_role is not None:
                await member.add_roles(normal_role, reason="Normal member onboarding")
            return AntiRaidJoinOutcome(False, False)
    return outcome
