#!/bin/bash

# Configuration
BOT_DIR="/home/ubuntu/HMS-Victory"
DB_FILE="${BOT_DIR}/database.db"
SHM_FILE="${BOT_DIR}/database.db-shm"
WAL_FILE="${BOT_DIR}/database.db-wal"

echo "======================================"
echo " Starting HMS-Victory Update Sequence "
echo "======================================"

# 0. Ensure systemd waits for the in-bot graceful drain (up to ~10 min for active games -
#    including whole live Connect 4 / Battleship PvP matches - to finish) before sending SIGKILL.
#    Idempotent drop-in; safe to run every time.
echo "[0/3] Ensuring graceful-stop timeout (630s)..."
sudo mkdir -p /etc/systemd/system/hms-victory.service.d
printf '[Service]\nTimeoutStopSec=630\n' | sudo tee /etc/systemd/system/hms-victory.service.d/timeout.conf >/dev/null
sudo systemctl daemon-reload

# 1. Stop the bot. systemctl now blocks while the bot drains active games (maintenance mode
#    blocks new ones), then checkpoints the DB and exits cleanly within the 150s window.
echo "[1/3] Stopping HMS-Victory service (waits for active games, incl. live PvP matches, to finish)..."
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
echo " Update complete!"
echo "======================================"

# If running interactively in a terminal, follow live logs.
# Otherwise (automated deployment / non-interactive SSH), print status and exit cleanly.
if [ -t 0 ] && [ -t 1 ]; then
    echo " Showing live logs (Press Ctrl+C to exit)..."
    journalctl -f -u hms-victory.service
else
    echo " Bot service status:"
    sudo systemctl is-active hms-victory
    journalctl -n 20 --no-pager -u hms-victory.service
fi
