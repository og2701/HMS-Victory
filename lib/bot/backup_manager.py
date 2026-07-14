import discord
import os
import logging
import io
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
ALLOW_EMPTY_DB_BOOTSTRAP_ENV = "ALLOW_EMPTY_DB_BOOTSTRAP"
ESSENTIAL_DATABASE_TABLES = frozenset({
    "bank",
    "economy_transactions",
    "ukpence",
    "xp",
})


class DatabaseRecoveryError(RuntimeError):
    """Raised when a missing live database cannot be restored safely."""


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
    """Reject corrupt/non-HMS SQLite files before they can become the live DB."""
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
    except DatabaseRecoveryError:
        raise
    except sqlite3.Error as exc:
        raise DatabaseRecoveryError(
            f"Downloaded backup is not a readable SQLite database: {exc}"
        ) from exc

    missing_tables = ESSENTIAL_DATABASE_TABLES - present_tables
    if missing_tables:
        raise DatabaseRecoveryError(
            "Downloaded backup is missing essential tables: "
            + ", ".join(sorted(missing_tables))
        )


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
JSON_BACKUP_DIRS = ["data/json", "daily_summaries", "balance_snapshots"]


async def restore_json_if_missing():
    """If data/json is missing/empty on startup, restore the latest JSON backup from Discord."""
    json_dir = "data/json"
    has_contents = os.path.isdir(json_dir) and any(
        f.endswith(".json") for f in os.listdir(json_dir)
    )
    if has_contents:
        return

    logger.warning("data/json is missing or empty. Attempting to restore from backup...")

    intents = discord.Intents.default()
    temp_client = discord.Client(intents=intents)

    bot_token = os.getenv("DISCORD_TOKEN")
    if not bot_token:
        logger.error("Bot token not found. Cannot restore JSON backup.")
        return

    try:
        await temp_client.login(bot_token)
        archive_channel = await temp_client.fetch_channel(CHANNELS.DATA_BACKUP)

        latest = None
        async for message in archive_channel.history(limit=200):
            for attachment in message.attachments:
                if attachment.filename.startswith(JSON_BACKUP_PREFIX) and attachment.filename.endswith(".zip"):
                    latest = attachment
                    break
            if latest:
                break

        if not latest:
            logger.warning("No JSON backup found in last 200 messages.")
            return

        logger.info(f"Found latest JSON backup: {latest.filename}")
        zip_path = "temp_json_backup.zip"
        await latest.save(zip_path)
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(".")
            logger.info("Successfully restored JSON data from backup.")
        finally:
            if os.path.exists(zip_path):
                os.remove(zip_path)
    except Exception as e:
        logger.error(f"Failed during JSON restore: {e}")
    finally:
        await temp_client.close()


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
