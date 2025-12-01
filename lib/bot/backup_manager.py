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
                        if attachment.filename.startswith('database_backup_') and attachment.filename.endswith('.db'):
                            latest_backup = attachment
                            break
                if latest_backup:
                    break
            
            if latest_backup:
                logger.info(f"Found latest database backup: {latest_backup.filename}")
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


async def backup_database(client):
    logger.info("Backing up database...")
    channel = client.get_channel(CHANNELS.DATA_BACKUP)
    if channel:
        if os.path.exists('database.db'):
            with open('database.db', 'rb') as f:
                await channel.send(file=discord.File(f, f'database_backup_{datetime.now().strftime("%Y-%m-%d_%H-%M-%S")}.db'))
            logger.info("Database backup sent to Discord.")
        else:
            logger.warning("database.db not found, skipping backup.")

async def backup_bot(client):
    logger.info("Backing up bot...")
    await send_json_files(
        client=client,
        folder_path="./",
        channel_id=CHANNELS.DATA_BACKUP
    )
