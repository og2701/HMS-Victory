#!/bin/bash

# Configuration
BOT_DIR="/home/ubuntu/HMS-Victory"
BACKUP_DIR="${BOT_DIR}/backups"
DB_FILE="${BOT_DIR}/database.db"
SHM_FILE="${BOT_DIR}/database.db-shm"
WAL_FILE="${BOT_DIR}/database.db-wal"
TIMESTAMP=$(date +"%Y-%m-%d_%H-%M-%S")

echo "======================================"
echo " Starting HMS-Victory Update Sequence "
echo "======================================"

# 1. Stop the bot safely to release SQLite locks
echo "[1/4] Stopping HMS-Victory service..."
sudo systemctl stop hms-victory
sleep 2

# 2. Backup the internal database (with WAL files)
echo "[2/4] Backing up the database..."
mkdir -p "$BACKUP_DIR"

if [ -f "$DB_FILE" ]; then
    cp "$DB_FILE" "${BACKUP_DIR}/database_${TIMESTAMP}.db"
    echo "  -> Backed up database.db"
fi

if [ -f "$SHM_FILE" ]; then
    cp "$SHM_FILE" "${BACKUP_DIR}/database_${TIMESTAMP}.db-shm"
    echo "  -> Backed up database.db-shm"
fi

if [ -f "$WAL_FILE" ]; then
    cp "$WAL_FILE" "${BACKUP_DIR}/database_${TIMESTAMP}.db-wal"
    echo "  -> Backed up database.db-wal"
fi

# 3. Pull the latest code from GitHub
echo "[3/4] Pulling the latest changes from GitHub..."
cd "$BOT_DIR" || exit
git pull

# 4. Restart the bot
echo "[4/4] Restarting HMS-Victory service..."
sudo systemctl start hms-victory
sleep 2

echo "======================================"
echo " Update complete! Showing live logs..."
echo " (Press Ctrl+C to exit logs)"
echo "======================================"

journalctl -f -u hms-victory.service
