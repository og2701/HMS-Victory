#!/bin/bash
#
# HMS-Victory Instance Setup Script
# Ubuntu 22.04 / 24.04, x86_64. Idempotent - safe to re-run on a half-built box.
#
# Run it from inside the cloned repo:
#   git clone https://github.com/<owner>/HMS-Victory.git && cd HMS-Victory && ./setup_instance.sh
#
# NOTE ON ARCHITECTURE: this installs google-chrome-stable, which Google ships for amd64
# only. On Graviton/ARM (t4g.*) the apt repo has nothing to give you - use a t3.* instance,
# or switch to `chromium-browser` and point CHROME_PATH at it.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

echo "======================================"
echo "   HMS-Victory Instance Setup         "
echo "======================================"

# 1. Update System
echo "[1/8] Updating system packages..."
sudo apt-get update && sudo apt-get upgrade -y

# 2. Install Dependencies
echo "[2/8] Installing system dependencies (Python, SQLite, Chrome)..."
sudo apt-get install -y python3 python3-pip python3-venv sqlite3 wget gnupg git curl

# Google Chrome for the rendered cards (balance graphs, crossword boards, game art).
# apt-key was removed in Ubuntu 24.04 - the key goes in its own keyring and the source
# line points at it, which is also what apt has wanted since 22.04.
if ! command -v google-chrome-stable &> /dev/null; then
    sudo install -m 0755 -d /etc/apt/keyrings
    wget -qO- https://dl-ssl.google.com/linux/linux_signing_key.pub \
        | sudo gpg --dearmor -o /etc/apt/keyrings/google-chrome.gpg
    sudo chmod a+r /etc/apt/keyrings/google-chrome.gpg
    echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" \
        | sudo tee /etc/apt/sources.list.d/google-chrome.list >/dev/null
    sudo rm -f /etc/apt/sources.list.d/google-chrome.list.save
    sudo apt-get update
    sudo apt-get install -y google-chrome-stable
fi

# 3. Swap. One headless Chrome stays resident for the life of the bot process and spikes
#    hard while rendering, so a 2GB box with no swap can OOM - which kills sshd's ability
#    to fork a login as readily as it kills the bot, and then you can't get in to look.
echo "[3/8] Ensuring swap exists..."
if ! sudo swapon --show | grep -q '/swapfile'; then
    sudo fallocate -l 2G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
    echo "  2G swapfile created and enabled."
else
    echo "  swap already present, leaving it alone."
fi

# 4. Setup Virtual Environment
echo "[4/8] Setting up Python virtual environment..."
python3 -m venv venv
./venv/bin/pip install --upgrade pip
if [ -f "requirements.txt" ]; then
    ./venv/bin/pip install -r requirements.txt
else
    echo "Warning: requirements.txt not found. Skipping pip install."
fi

# 5. Data directories the bot writes into but does not create.
echo "[5/8] Creating data directories..."
mkdir -p data/json balance_snapshots daily_summaries

# 6. Setup Secrets Template
echo "[6/8] Creating .env template if missing..."
if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        cp .env.example .env
        echo "Created .env from .env.example. PLEASE FILL IT IN."
    else
        # systemd reads this directly as an EnvironmentFile: bare KEY=value, no `export`,
        # no shell quoting - a quoted value arrives with its quotes still attached.
        cat <<EOF > .env
DISCORD_TOKEN=your_token_here
OPENAI_TOKEN=your_openai_token_here
GEMINI_TOKEN=your_gemini_token_here
CHROME_PATH=/usr/bin/google-chrome-stable
EOF
        echo "Created basic .env. PLEASE FILL IT IN."
    fi
    chmod 600 .env
fi

# 7. Configure Systemd Service
echo "[7/8] Configuring systemd service..."
TEMPLATE="$HERE/deployment/hms-victory.service.template"
if [ -f "$TEMPLATE" ]; then
    sed "s|{{WORKING_DIR}}|$HERE|g; s|{{USER}}|$USER|g" "$TEMPLATE" \
        | sudo tee /etc/systemd/system/hms-victory.service >/dev/null

    # The bot drains active games on SIGTERM (whole live PvP matches can be in flight),
    # which takes longer than systemd's 90s default patience. Same drop-in update_bot.sh
    # writes, installed up front so the very first restart is graceful too.
    sudo mkdir -p /etc/systemd/system/hms-victory.service.d
    printf '[Service]\nTimeoutStopSec=630\n' \
        | sudo tee /etc/systemd/system/hms-victory.service.d/timeout.conf >/dev/null

    sudo systemctl daemon-reload
    echo "  hms-victory.service installed (not started yet)."
else
    echo "Warning: $TEMPLATE not found. Skipping service setup."
fi

# 8. Final Steps
echo "[8/8] Setup complete!"
echo "--------------------------------------"
echo "Next steps:"
echo "1. Edit '.env' with your tokens:  nano $HERE/.env"
echo "2. Restore data/ , database.db and balance_snapshots/ from your backup."
echo "3. sudo systemctl enable --now hms-victory"
echo "4. journalctl -fu hms-victory      # watch it come up"
echo "5. Use './update_bot.sh' for future updates."
echo "--------------------------------------"
