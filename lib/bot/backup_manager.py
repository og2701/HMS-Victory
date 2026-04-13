import discord
import os
import logging
import io
import zipfile
from datetime import datetime
from config import *
from database import init_db

logger = logging.getLogger(__name__)
MAX_PART_SIZE = 8 * 1024 * 1024

async def restore_database_if_missing():
    if not os.path.exists('database.db'):
        logger.warning("database.db not found. Attempting to restore from backup...")
        
        # We need a temporary client to fetch the backup
        intents = discord.Intents.default()
        temp_client = discord.Client(intents=intents)
        
        bot_token = os.getenv("DISCORD_TOKEN")
        if not bot_token:
            logger.error("Bot token not found in environment variables. Cannot restore database. Creating a new one.")
            init_db()
            return
            
        try:
            # Login the temporary client
            await temp_client.login(bot_token)
            
            archive_channel = await temp_client.fetch_channel(CHANNELS.DATA_BACKUP)
            
            latest_backup = None
            async for message in archive_channel.history(limit=100):
                if message.attachments:
                    for attachment in message.attachments:
                        if attachment.filename.startswith('database_backup_') and (attachment.filename.endswith('.db') or attachment.filename.endswith('.zip')):
                            latest_backup = attachment
                            break
                if latest_backup:
                    break
            
            if latest_backup:
                logger.info(f"Found latest database backup: {latest_backup.filename}")
                if latest_backup.filename.endswith('.zip'):
                    zip_path = 'temp_backup.zip'
                    await latest_backup.save(zip_path)
                    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                        zip_ref.extractall('.')
                    os.remove(zip_path)
                    logger.info("Successfully extracted database from ZIP backup.")
                else:
                    await latest_backup.save('database.db')
                    logger.info("Successfully restored database.db from backup.")
            else:
                logger.warning("No database backup found in the last 100 messages. Creating a new empty database.")
                init_db()

        except Exception as e:
            logger.error(f"Failed during database restore: {e}. Creating a new database.")
            init_db()
        finally:
            await temp_client.close()


async def zip_and_send_folder(client, folder_path, channel_id, zip_filename_prefix):
    if not os.path.exists(folder_path):
        logger.warning(f"Folder '{folder_path}' does not exist.")
        return

    archive_channel = client.get_channel(channel_id)
    if not archive_channel:
        logger.warning(f"Channel ID {channel_id} not found.")
        return

    logger.info(f"Creating in-memory ZIP for {folder_path}...")

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
        for root, _, files in os.walk(folder_path):
            for file_in_folder in files:
                file_path = os.path.join(root, file_in_folder)
                archive_name = os.path.relpath(file_path, start=folder_path)
                zipf.write(file_path, archive_name)

    zip_buffer.seek(0)

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
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
            for folder in present_dirs:
                for root, _, files in os.walk(folder):
                    for f in files:
                        if not f.endswith(".json"):
                            continue
                        full = os.path.join(root, f)
                        zipf.write(full, os.path.relpath(full, start="."))

        zip_buffer.seek(0)
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

    db_files = ['database.db', 'database.db-shm', 'database.db-wal']
    existing_files = [f for f in db_files if os.path.exists(f)]

    if not existing_files:
        logger.warning("No database files found for backup.")
        return

    try:
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
            for file in existing_files:
                zipf.write(file, file)
        
        zip_buffer.seek(0)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"database_backup_{timestamp}.zip"
        
        await channel.send(file=discord.File(fp=zip_buffer, filename=filename))
        logger.info(f"Database backup ({', '.join(existing_files)}) sent to Discord.")
    except Exception as e:
        logger.error(f"Error during database backup to Discord: {e}")

async def backup_bot(client):
    logger.info("Backing up bot...")
    await send_json_files(
        client=client,
        folder_path="./",
        channel_id=CHANNELS.DATA_BACKUP
    )
