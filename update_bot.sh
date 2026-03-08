#!/bin/bash

# Configuration
BOT_DIR="/home/ubuntu/HMS-Victory"
DB_FILE="${BOT_DIR}/database.db"
SHM_FILE="${BOT_DIR}/database.db-shm"
WAL_FILE="${BOT_DIR}/database.db-wal"

echo "======================================"
echo " Starting HMS-Victory Update Sequence "
echo "======================================"

# 1. Stop the bot safely to release SQLite locks
echo "[1/4] Stopping HMS-Victory service..."
sudo systemctl stop hms-victory
sleep 2

# 2. Pull the latest code from GitHub
echo "[2/3] Pulling the latest changes from GitHub..."
cd "$BOT_DIR" || exit
git pull

# 3. Restart the bot
echo "[3/3] Restarting HMS-Victory service..."
sudo systemctl start hms-victory
sleep 2

echo "======================================"
echo " Update complete! Showing live logs..."
echo " (Press Ctrl+C to exit logs)"
echo "======================================"

journalctl -f -u hms-victory.service
