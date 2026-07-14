import discord
import os
import logging
import io
import json
import shutil
import zipfile
import asyncio
import sqlite3
import stat
import tempfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from config import *

logger = logging.getLogger(__name__)
MAX_PART_SIZE = 8 * 1024 * 1024
MAX_DATABASE_BACKUP_BYTES = 512 * 1024 * 1024
MAX_DATABASE_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_JSON_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_JSON_TOTAL_BYTES = 128 * 1024 * 1024
MAX_JSON_FILE_BYTES = 16 * 1024 * 1024
MAX_JSON_FILES = 10_000
ALLOW_EMPTY_DB_BOOTSTRAP_ENV = "ALLOW_EMPTY_DB_BOOTSTRAP"
ECONOMY_TOTAL_SUPPLY = 800_000
ESSENTIAL_DATABASE_TABLES = frozenset({
    "bank",
    "economy_transactions",
    "ukpence",
    "xp",
})
ESSENTIAL_DATABASE_COLUMNS = {
    "bank": frozenset({"id", "balance"}),
    "economy_transactions": frozenset({"id", "timestamp", "log_text"}),
    "ukpence": frozenset({"user_id", "balance"}),
    "xp": frozenset({"user_id", "xp", "last_xp_time"}),
}
ESSENTIAL_PRIMARY_KEYS = {
    "bank": "id",
    "economy_transactions": "id",
    "ukpence": "user_id",
    "xp": "user_id",
}


class DatabaseRecoveryError(RuntimeError):
    """Raised when a missing live database cannot be restored safely."""


class JSONRecoveryError(RuntimeError):
    """Raised when missing JSON state cannot be restored safely."""


def _zip_folder_to_buffer(folder_path) -> io.BytesIO:
    """Zip every file under folder_path into an in-memory buffer. Runs in a worker thread
    (zlib + file I/O release the GIL) so the compression never blocks the event loop."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zipf:
        for root, _, files in os.walk(folder_path):
            for name in files:
                fp = os.path.join(root, name)
                zipf.write(fp, os.path.relpath(fp, start=folder_path))
    buf.seek(0)
    return buf


def _zip_json_dirs_to_buffer(present_dirs) -> io.BytesIO:
    """Zip the .json files under each present dir into an in-memory buffer (worker thread)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zipf:
        for folder in present_dirs:
            for root, _, files in os.walk(folder):
                for f in files:
                    if not f.endswith(".json"):
                        continue
                    full = os.path.join(root, f)
                    zipf.write(full, os.path.relpath(full, start="."))
    buf.seek(0)
    return buf


def _zip_single_file_to_buffer(src_path, arcname) -> io.BytesIO:
    """Zip one file into an in-memory buffer under arcname (worker thread)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zipf:
        zipf.write(src_path, arcname)
    buf.seek(0)
    return buf

def _empty_database_bootstrap_allowed() -> bool:
    return os.getenv(ALLOW_EMPTY_DB_BOOTSTRAP_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _validate_database_candidate(candidate_path: Path) -> None:
    """Reject corrupt, incomplete, or logically invalid HMS databases."""
    if not candidate_path.is_file() or candidate_path.stat().st_size == 0:
        raise DatabaseRecoveryError("Downloaded database backup is empty or missing.")

    try:
        # Read-only mode prevents validation from creating or mutating a database.
        uri = f"{candidate_path.resolve().as_uri()}?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            quick_check = connection.execute("PRAGMA quick_check").fetchall()
            if quick_check != [("ok",)]:
                details = "; ".join(str(row[0]) for row in quick_check)
                raise DatabaseRecoveryError(
                    f"SQLite quick_check rejected the backup: {details or 'unknown error'}"
                )

            present_tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }

            missing_tables = ESSENTIAL_DATABASE_TABLES - present_tables
            if missing_tables:
                raise DatabaseRecoveryError(
                    "Downloaded backup is missing essential tables: "
                    + ", ".join(sorted(missing_tables))
                )

            table_info = {
                table: {
                    row[1]: row
                    for row in connection.execute(
                        f'PRAGMA table_info("{table}")'
                    )
                }
                for table in ESSENTIAL_DATABASE_TABLES
            }
            missing_columns = {
                table: sorted(required - set(table_info[table]))
                for table, required in ESSENTIAL_DATABASE_COLUMNS.items()
                if required - set(table_info[table])
            }
            if missing_columns:
                details = "; ".join(
                    f"{table}: {', '.join(columns)}"
                    for table, columns in sorted(missing_columns.items())
                )
                raise DatabaseRecoveryError(
                    f"Downloaded backup has incompatible essential table schemas ({details})."
                )

            invalid_primary_keys = [
                f"{table}.{column}"
                for table, column in ESSENTIAL_PRIMARY_KEYS.items()
                if not table_info[table][column][5]
            ]
            if invalid_primary_keys:
                raise DatabaseRecoveryError(
                    "Downloaded backup is missing essential primary keys: "
                    + ", ".join(sorted(invalid_primary_keys))
                )

            foreign_key_errors = connection.execute(
                "PRAGMA foreign_key_check"
            ).fetchall()
            if foreign_key_errors:
                raise DatabaseRecoveryError(
                    "Downloaded backup failed SQLite foreign-key validation."
                )

            invalid_balance_count = connection.execute(
                """
                SELECT COUNT(*)
                FROM ukpence
                WHERE user_id IS NULL
                   OR TRIM(CAST(user_id AS TEXT)) = ''
                   OR balance IS NULL
                   OR typeof(balance) NOT IN ('integer', 'real')
                   OR balance < 0
                   OR balance != CAST(balance AS INTEGER)
                """
            ).fetchone()[0]
            if invalid_balance_count:
                raise DatabaseRecoveryError(
                    "Downloaded backup contains invalid UKP account rows."
                )

            account_count, total_balance = connection.execute(
                "SELECT COUNT(*), COALESCE(SUM(balance), 0) FROM ukpence"
            ).fetchone()
            if account_count < 1:
                raise DatabaseRecoveryError(
                    "Downloaded backup violates the closed-economy supply invariant: "
                    "there are no UKP accounts."
                )

            bot_balance_rows = connection.execute(
                "SELECT balance FROM ukpence WHERE CAST(user_id AS TEXT) = ?",
                (str(BOT_ID),),
            ).fetchall()
            if len(bot_balance_rows) != 1:
                raise DatabaseRecoveryError(
                    "Downloaded backup does not contain exactly one bot-bank UKP account."
                )

            bank_rows = connection.execute(
                "SELECT balance FROM bank WHERE id = 1"
            ).fetchall()
            if len(bank_rows) != 1:
                raise DatabaseRecoveryError(
                    "Downloaded backup does not contain the canonical bank row (id = 1)."
                )

            bot_balance = bot_balance_rows[0][0]
            bank_balance = bank_rows[0][0]
            if (
                bank_balance is None
                or type(bank_balance) not in (int, float)
                or bank_balance < 0
                or bank_balance != int(bank_balance)
            ):
                raise DatabaseRecoveryError(
                    "Downloaded backup contains an invalid canonical bank balance."
                )
            if bank_balance != bot_balance:
                raise DatabaseRecoveryError(
                    "Downloaded backup has mismatched bank and bot-account balances."
                )

            non_bot_total = connection.execute(
                "SELECT COALESCE(SUM(balance), 0) FROM ukpence "
                "WHERE CAST(user_id AS TEXT) != ?",
                (str(BOT_ID),),
            ).fetchone()[0]
            expected_reserve = max(ECONOMY_TOTAL_SUPPLY - non_bot_total, 0)
            if bot_balance != expected_reserve:
                raise DatabaseRecoveryError(
                    "Downloaded backup violates the closed-economy supply invariant: "
                    f"expected a {expected_reserve!r} UKP bot reserve for "
                    f"{non_bot_total!r} UKP in user accounts, found {bot_balance!r}."
                )
            if total_balance != non_bot_total + bot_balance:
                raise DatabaseRecoveryError(
                    "Downloaded backup contains inconsistent UKP balance totals."
                )
    except DatabaseRecoveryError:
        raise
    except sqlite3.Error as exc:
        raise DatabaseRecoveryError(
            f"Downloaded backup is not a readable SQLite database: {exc}"
        ) from exc

def _extract_database_candidate(zip_path: Path, candidate_path: Path) -> None:
    """Extract the one supported archive member without trusting ZIP paths."""
    try:
        with zipfile.ZipFile(zip_path, "r") as zip_file:
            members = zip_file.infolist()
            for member in members:
                member_path = PurePosixPath(member.filename)
                unix_mode = member.external_attr >> 16
                if (
                    member_path.is_absolute()
                    or "\\" in member.filename
                    or ".." in member_path.parts
                    or stat.S_ISLNK(unix_mode)
                ):
                    raise DatabaseRecoveryError(
                        f"Unsafe path or link in database backup ZIP: {member.filename!r}"
                    )

            if len(members) != 1 or members[0].is_dir() or members[0].filename != "database.db":
                raise DatabaseRecoveryError(
                    "Database backup ZIP must contain only a root-level database.db file."
                )

            member = members[0]
            if member.flag_bits & 0x1:
                raise DatabaseRecoveryError("Encrypted database backup ZIPs are not supported.")
            if member.file_size > MAX_DATABASE_BACKUP_BYTES:
                raise DatabaseRecoveryError(
                    "Database backup ZIP expands beyond the supported "
                    f"{MAX_DATABASE_BACKUP_BYTES:,}-byte limit."
                )

            with zip_file.open(member, "r") as source, candidate_path.open("xb") as target:
                copied = 0
                while chunk := source.read(1024 * 1024):
                    copied += len(chunk)
                    if copied > MAX_DATABASE_BACKUP_BYTES:
                        raise DatabaseRecoveryError(
                            "Database backup ZIP exceeded the supported uncompressed size limit."
                        )
                    target.write(chunk)
    except DatabaseRecoveryError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise DatabaseRecoveryError(f"Could not safely unpack database backup ZIP: {exc}") from exc


async def _download_validate_and_promote_database(attachment, database_path: Path) -> None:
    """Download into the destination directory, validate, then atomically promote."""
    database_path.parent.mkdir(parents=True, exist_ok=True)
    prefix = f".{database_path.name}.restore-"

    with tempfile.TemporaryDirectory(prefix=prefix, dir=database_path.parent) as temp_dir:
        temp_root = Path(temp_dir)
        is_zip = attachment.filename.lower().endswith(".zip")
        download_path = temp_root / ("download.zip" if is_zip else "download.db")
        await attachment.save(str(download_path))
        download_limit = (
            MAX_DATABASE_ARCHIVE_BYTES if is_zip else MAX_DATABASE_BACKUP_BYTES
        )
        if download_path.stat().st_size > download_limit:
            raise DatabaseRecoveryError(
                "Downloaded database backup exceeds the supported "
                f"{download_limit:,}-byte limit."
            )

        if is_zip:
            candidate_path = temp_root / "candidate.db"
            _extract_database_candidate(download_path, candidate_path)
        else:
            candidate_path = download_path

        _validate_database_candidate(candidate_path)

        # The temporary directory is deliberately on the same filesystem as the
        # destination, so os.replace is an atomic promotion. Until this line the
        # live path remains absent and no partial download can be mistaken for it.
        os.replace(candidate_path, database_path)


def _default_recovery_client_factory():
    intents = discord.Intents.default()
    return discord.Client(intents=intents)


async def restore_database_if_missing(database_path="database.db", *, client_factory=None):
    """Restore a missing database or fail startup without creating an empty one.

    Returns ``True`` after a validated backup is promoted and ``False`` when the
    database already exists or an explicitly authorised first-install bootstrap
    should be handled by the caller's normal ``init_db`` step.
    """
    database_path = Path(database_path)
    if database_path.exists():
        try:
            _validate_database_candidate(database_path)
        except DatabaseRecoveryError:
            logger.critical(
                "Existing database %s failed validation; refusing to initialise or replace it.",
                database_path,
                exc_info=True,
            )
            raise
        return False

    logger.warning("%s not found. Attempting to restore from backup...", database_path)

    # This is intentionally checked before constructing a Discord client: a genuine
    # first install can opt into init_db without any recovery/network side effects.
    if _empty_database_bootstrap_allowed():
        logger.warning(
            "%s is enabled; allowing an empty first-install database bootstrap. "
            "Disable this flag immediately after the first successful boot.",
            ALLOW_EMPTY_DB_BOOTSTRAP_ENV,
        )
        return False

    bot_token = os.getenv("DISCORD_TOKEN")
    if not bot_token:
        raise DatabaseRecoveryError(
            "database.db is missing and DISCORD_TOKEN is unavailable, so no backup "
            f"can be restored. Set {ALLOW_EMPTY_DB_BOOTSTRAP_ENV}=true only for a "
            "genuine first install."
        )

    temp_client = None
    try:
        factory = client_factory or _default_recovery_client_factory
        temp_client = factory()
        await temp_client.login(bot_token)
        archive_channel = await temp_client.fetch_channel(CHANNELS.DATA_BACKUP)

        latest_backup = None
        async for message in archive_channel.history(limit=100):
            for attachment in message.attachments:
                filename = attachment.filename.lower()
                if filename.startswith("database_backup_") and filename.endswith((".db", ".zip")):
                    latest_backup = attachment
                    break
            if latest_backup:
                break

        if latest_backup is None:
            raise DatabaseRecoveryError(
                "No database backup was found in the last 100 data-backup messages."
            )

        logger.info("Found latest database backup: %s", latest_backup.filename)
        await _download_validate_and_promote_database(latest_backup, database_path)
        logger.info("Validated and atomically restored %s.", database_path)
        return True
    except DatabaseRecoveryError:
        logger.critical("Database recovery failed; refusing to boot an empty database.", exc_info=True)
        raise
    except Exception as exc:
        logger.critical("Database recovery failed; refusing to boot an empty database.", exc_info=True)
        raise DatabaseRecoveryError(f"Database backup recovery failed: {exc}") from exc
    finally:
        if temp_client is not None:
            try:
                await temp_client.close()
            except Exception:
                logger.warning("Failed to close temporary database recovery client.", exc_info=True)


async def zip_and_send_folder(client, folder_path, channel_id, zip_filename_prefix):
    if not os.path.exists(folder_path):
        logger.warning(f"Folder '{folder_path}' does not exist.")
        return

    archive_channel = client.get_channel(channel_id)
    if not archive_channel:
        logger.warning(f"Channel ID {channel_id} not found.")
        return

    logger.info(f"Creating in-memory ZIP for {folder_path}...")

    zip_buffer = await asyncio.to_thread(_zip_folder_to_buffer, folder_path)

    file_number = 1
    while True:
        chunk = zip_buffer.read(MAX_PART_SIZE)
        if not chunk:
            break

        part_filename = f"{zip_filename_prefix}_part{file_number}.zip"
        part_buffer = io.BytesIO(chunk)
        part_buffer.seek(0)

        await archive_channel.send(file=discord.File(fp=part_buffer, filename=part_filename))
        logger.info(f"Sent part {file_number}: {part_filename}")

        file_number += 1

    logger.info("Backup complete.")


async def send_json_files(client, folder_path, channel_id):
    if not os.path.exists(folder_path):
        logger.warning(f"Folder '{folder_path}' does not exist.")
        return

    archive_channel = client.get_channel(channel_id)
    if not archive_channel:
        logger.warning(f"Channel ID {channel_id} not found.")
        return

    json_files = [f for f in os.listdir(folder_path) if f.endswith(".json") and os.path.isfile(os.path.join(folder_path, f))]

    if not json_files:
        logger.info("No JSON files found to upload.")
        return

    logger.info(f"Found {len(json_files)} JSON files. Uploading...")

    for file_name in json_files:
        file_path = os.path.join(folder_path, file_name)

        if os.path.getsize(file_path) > 8 * 1024 * 1024:
            logger.warning(f"Skipping {file_name} - File too large for Discord.")
            continue

        with open(file_path, "rb") as file_to_send:
            await archive_channel.send(file=discord.File(file_to_send, filename=file_name))
            logger.info(f"Uploaded {file_name}.")

    logger.info("All JSON files uploaded.")


JSON_BACKUP_PREFIX = "json_backup_"
JSON_BACKUP_DIRS = ("data/json", "daily_summaries", "balance_snapshots")


def _json_root_for_member(member_path: PurePosixPath):
    for root in JSON_BACKUP_DIRS:
        root_parts = PurePosixPath(root).parts
        if member_path.parts[: len(root_parts)] == root_parts:
            return root
    return None


def _allowed_json_directory(member_path: PurePosixPath) -> bool:
    """Allow explicit ZIP directory entries that lead only to approved roots."""
    if not member_path.parts:
        return False
    for root in JSON_BACKUP_DIRS:
        root_parts = PurePosixPath(root).parts
        if (
            member_path.parts[: len(root_parts)] == root_parts
            or root_parts[: len(member_path.parts)] == member_path.parts
        ):
            return True
    return False


def _validate_existing_json_root(base_path: Path, relative_root: str) -> None:
    """Refuse roots whose current shape could escape a staged directory swap."""
    cursor = base_path
    for part in PurePosixPath(relative_root).parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise JSONRecoveryError(
                f"JSON restore target contains a symlink: {cursor}"
            )

    if not cursor.exists():
        return
    if not cursor.is_dir():
        raise JSONRecoveryError(
            f"JSON restore target is not a directory: {cursor}"
        )
    for existing_path in cursor.rglob("*"):
        if existing_path.is_symlink():
            raise JSONRecoveryError(
                f"JSON restore target contains a symlink: {existing_path}"
            )


def _stage_json_restore_archive(
    zip_path: Path,
    base_path: Path,
    staging_root: Path,
):
    """Validate every member and JSON document before any live path is changed."""
    represented_roots = set()
    extracted_members = []
    seen_paths = set()
    declared_total = 0

    try:
        with zipfile.ZipFile(zip_path, "r") as archive:
            for member in archive.infolist():
                raw_name = member.filename
                member_path = PurePosixPath(raw_name)
                unix_mode = member.external_attr >> 16
                file_type = stat.S_IFMT(unix_mode)
                if (
                    not raw_name
                    or member_path.is_absolute()
                    or "\\" in raw_name
                    or ".." in member_path.parts
                    or stat.S_ISLNK(unix_mode)
                ):
                    raise JSONRecoveryError(
                        f"Unsafe path or link in JSON backup ZIP: {raw_name!r}"
                    )
                if member.flag_bits & 0x1:
                    raise JSONRecoveryError(
                        "Encrypted JSON backup ZIPs are not supported."
                    )

                if member.is_dir():
                    if not _allowed_json_directory(member_path):
                        raise JSONRecoveryError(
                            f"Unexpected directory in JSON backup ZIP: {raw_name!r}"
                        )
                    if file_type not in (0, stat.S_IFDIR):
                        raise JSONRecoveryError(
                            f"Unsupported directory type in JSON backup ZIP: {raw_name!r}"
                        )
                    continue

                root = _json_root_for_member(member_path)
                if root is None or member_path.suffix.lower() != ".json":
                    raise JSONRecoveryError(
                        f"Unexpected file in JSON backup ZIP: {raw_name!r}"
                    )
                if file_type not in (0, stat.S_IFREG):
                    raise JSONRecoveryError(
                        f"Unsupported file type in JSON backup ZIP: {raw_name!r}"
                    )
                normalised_name = member_path.as_posix()
                if normalised_name in seen_paths:
                    raise JSONRecoveryError(
                        f"Duplicate file in JSON backup ZIP: {normalised_name!r}"
                    )
                seen_paths.add(normalised_name)
                if len(seen_paths) > MAX_JSON_FILES:
                    raise JSONRecoveryError(
                        f"JSON backup contains more than {MAX_JSON_FILES:,} files."
                    )
                if member.file_size > MAX_JSON_FILE_BYTES:
                    raise JSONRecoveryError(
                        f"JSON backup member {raw_name!r} exceeds the per-file size limit."
                    )
                declared_total += member.file_size
                if declared_total > MAX_JSON_TOTAL_BYTES:
                    raise JSONRecoveryError(
                        "JSON backup expands beyond the supported total size limit."
                    )
                represented_roots.add(root)
                extracted_members.append((member, member_path))

            if not any(root == "data/json" for root in represented_roots):
                raise JSONRecoveryError(
                    "JSON backup does not contain any data/json state files."
                )

            for relative_root in represented_roots:
                _validate_existing_json_root(base_path, relative_root)
                source_root = base_path / relative_root
                staged_root = staging_root / relative_root
                staged_root.parent.mkdir(parents=True, exist_ok=True)
                if source_root.exists():
                    shutil.copytree(source_root, staged_root)
                else:
                    staged_root.mkdir(parents=True)

            actual_total = 0
            for member, member_path in extracted_members:
                target_path = staging_root.joinpath(*member_path.parts)
                target_path.parent.mkdir(parents=True, exist_ok=True)
                if target_path.is_symlink() or not target_path.parent.is_dir():
                    raise JSONRecoveryError(
                        f"Unsafe staged JSON restore path: {member.filename!r}"
                    )

                copied = 0
                with archive.open(member, "r") as source, target_path.open("wb") as target:
                    while chunk := source.read(1024 * 1024):
                        copied += len(chunk)
                        actual_total += len(chunk)
                        if copied > MAX_JSON_FILE_BYTES:
                            raise JSONRecoveryError(
                                f"JSON backup member {member.filename!r} exceeded its size limit."
                            )
                        if actual_total > MAX_JSON_TOTAL_BYTES:
                            raise JSONRecoveryError(
                                "JSON backup exceeded its total uncompressed size limit."
                            )
                        target.write(chunk)

                try:
                    with target_path.open("r", encoding="utf-8") as restored_file:
                        json.load(restored_file)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise JSONRecoveryError(
                        f"JSON backup member {member.filename!r} is not valid UTF-8 JSON: {exc}"
                    ) from exc
    except JSONRecoveryError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise JSONRecoveryError(
            f"Could not safely stage JSON backup ZIP: {exc}"
        ) from exc

    return represented_roots


def _remove_restore_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def _promote_staged_json_roots(
    base_path: Path,
    staging_root: Path,
    rollback_root: Path,
    represented_roots,
) -> None:
    """Swap validated directory trees into place and roll back on any error."""
    operations = []
    try:
        ordered_roots = [root for root in JSON_BACKUP_DIRS if root in represented_roots]
        for index, relative_root in enumerate(ordered_roots):
            target_root = base_path / relative_root
            staged_root = staging_root / relative_root
            previous_root = rollback_root / f"{index}-{relative_root.replace('/', '-') }"
            operation = {
                "target": target_root,
                "previous": previous_root,
                "old_moved": False,
                "new_installed": False,
            }
            operations.append(operation)

            target_root.parent.mkdir(parents=True, exist_ok=True)
            previous_root.parent.mkdir(parents=True, exist_ok=True)
            if target_root.exists():
                os.replace(target_root, previous_root)
                operation["old_moved"] = True
            os.replace(staged_root, target_root)
            operation["new_installed"] = True
    except Exception as exc:
        rollback_errors = []
        for operation in reversed(operations):
            try:
                if operation["new_installed"]:
                    _remove_restore_path(operation["target"])
                if operation["old_moved"] and operation["previous"].exists():
                    operation["target"].parent.mkdir(parents=True, exist_ok=True)
                    os.replace(operation["previous"], operation["target"])
            except Exception as rollback_exc:
                rollback_errors.append(str(rollback_exc))
        details = f" Rollback errors: {'; '.join(rollback_errors)}" if rollback_errors else ""
        raise JSONRecoveryError(
            f"Could not atomically promote restored JSON state: {exc}.{details}"
        ) from exc


async def restore_json_if_missing(base_path=".", *, client_factory=None):
    """Restore missing JSON state through a validated, rollback-safe directory swap."""
    base_path = Path(base_path).resolve()
    base_path.mkdir(parents=True, exist_ok=True)
    json_dir = base_path / "data/json"
    if json_dir.is_symlink() or (json_dir.exists() and not json_dir.is_dir()):
        raise JSONRecoveryError(f"Unsafe existing JSON state path: {json_dir}")
    if json_dir.exists():
        _validate_existing_json_root(base_path, "data/json")
    has_contents = json_dir.is_dir() and any(
        path.is_file() for path in json_dir.rglob("*.json")
    )
    if has_contents:
        return False

    logger.warning("data/json is missing or empty. Attempting to restore from backup...")

    if _empty_database_bootstrap_allowed():
        logger.warning(
            "%s is enabled; allowing empty first-install JSON state. Disable this "
            "flag immediately after the first successful boot.",
            ALLOW_EMPTY_DB_BOOTSTRAP_ENV,
        )
        return False

    bot_token = os.getenv("DISCORD_TOKEN")
    if not bot_token:
        raise JSONRecoveryError(
            "data/json is missing and DISCORD_TOKEN is unavailable, so JSON state "
            "cannot be restored."
        )

    temp_client = None
    try:
        factory = client_factory or _default_recovery_client_factory
        temp_client = factory()
        await temp_client.login(bot_token)
        archive_channel = await temp_client.fetch_channel(CHANNELS.DATA_BACKUP)

        latest = None
        async for message in archive_channel.history(limit=200):
            for attachment in message.attachments:
                filename = attachment.filename.lower()
                if filename.startswith(JSON_BACKUP_PREFIX) and filename.endswith(".zip"):
                    latest = attachment
                    break
            if latest:
                break

        if not latest:
            raise JSONRecoveryError(
                "No JSON backup was found in the last 200 data-backup messages."
            )

        logger.info(f"Found latest JSON backup: {latest.filename}")
        with tempfile.TemporaryDirectory(prefix=".json.restore-", dir=base_path) as temp_dir:
            temp_root = Path(temp_dir)
            zip_path = temp_root / "download.zip"
            staging_root = temp_root / "staged"
            rollback_root = temp_root / "previous"
            staging_root.mkdir()
            rollback_root.mkdir()

            await latest.save(str(zip_path))
            if zip_path.stat().st_size > MAX_JSON_ARCHIVE_BYTES:
                raise JSONRecoveryError(
                    "Downloaded JSON backup exceeds the supported archive size limit."
                )
            represented_roots = _stage_json_restore_archive(
                zip_path, base_path, staging_root
            )
            _promote_staged_json_roots(
                base_path, staging_root, rollback_root, represented_roots
            )
        logger.info("Validated and atomically restored JSON data from backup.")
        return True
    except JSONRecoveryError:
        logger.critical(
            "JSON recovery failed; refusing to boot with missing state.",
            exc_info=True,
        )
        raise
    except Exception as exc:
        logger.critical(
            "JSON recovery failed; refusing to boot with missing state.",
            exc_info=True,
        )
        raise JSONRecoveryError(f"JSON backup recovery failed: {exc}") from exc
    finally:
        if temp_client is not None:
            try:
                await temp_client.close()
            except Exception:
                logger.warning(
                    "Failed to close temporary JSON recovery client.",
                    exc_info=True,
                )


async def backup_json_data(client):
    """Zip all JSON state folders and upload to the data-backup channel."""
    channel = client.get_channel(CHANNELS.DATA_BACKUP)
    if not channel:
        logger.warning(f"Backup channel {CHANNELS.DATA_BACKUP} not found.")
        return

    present_dirs = [d for d in JSON_BACKUP_DIRS if os.path.isdir(d)]
    if not present_dirs:
        logger.info("No JSON directories present to back up.")
        return

    try:
        zip_buffer = await asyncio.to_thread(_zip_json_dirs_to_buffer, present_dirs)
        size = zip_buffer.getbuffer().nbytes
        if size == 0:
            logger.info("JSON backup archive is empty; skipping upload.")
            return
        if size > MAX_PART_SIZE:
            logger.warning(f"JSON backup exceeds {MAX_PART_SIZE} bytes ({size}); upload may fail.")

        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"{JSON_BACKUP_PREFIX}{timestamp}.zip"
        await channel.send(file=discord.File(fp=zip_buffer, filename=filename))
        logger.info(f"JSON backup sent to Discord: {filename} ({size} bytes).")
    except Exception as e:
        logger.error(f"Error during JSON backup: {e}")


async def backup_database(client):
    logger.info("Backing up database to Discord...")
    channel = client.get_channel(CHANNELS.DATA_BACKUP)
    if not channel:
        logger.warning(f"Backup channel {CHANNELS.DATA_BACKUP} not found.")
        return

    if not os.path.exists('database.db'):
        logger.warning("No database file found for backup.")
        return

    # Take a single transactionally-consistent snapshot rather than copying the
    # live .db/-wal/-shm separately (which risked a torn, unrestorable backup).
    from database import DatabaseManager
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    snapshot_path = f"database_snapshot_{timestamp}.db"
    try:
        DatabaseManager.snapshot_to_file(snapshot_path)

        # Store it as 'database.db' so the restore path drops it straight into place.
        # The (slow) compression runs in a worker thread so it can't block the event loop.
        zip_buffer = await asyncio.to_thread(_zip_single_file_to_buffer, snapshot_path, 'database.db')
        filename = f"database_backup_{timestamp}.zip"

        await channel.send(file=discord.File(fp=zip_buffer, filename=filename))
        logger.info("Consistent database snapshot backed up to Discord.")
    except Exception as e:
        logger.error(f"Error during database backup to Discord: {e}")
    finally:
        if os.path.exists(snapshot_path):
            try:
                os.remove(snapshot_path)
            except OSError as e:
                logger.warning(f"Could not remove temporary snapshot {snapshot_path}: {e}")

async def backup_bot(client):
    logger.info("Backing up bot...")
    await send_json_files(
        client=client,
        folder_path="./",
        channel_id=CHANNELS.DATA_BACKUP
    )
