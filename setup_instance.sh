#!/bin/bash

# HMS-Victory Instance Setup Script
# Works on Ubuntu 20.04/22.04+

echo "======================================"
echo "   HMS-Victory Instance Setup         "
echo "======================================"

# 1. Update System
echo "[1/6] Updating system packages..."
sudo apt-get update && sudo apt-get upgrade -y

# 2. Install Dependencies
echo "[2/6] Installing system dependencies (Python, SQLite, Chrome)..."
sudo apt-get install -y python3 python3-pip python3-venv sqlite3 wget gnupg

# Install Google Chrome for economy stats
if ! command -v google-chrome-stable &> /dev/null; then
    wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | sudo apt-key add -
    echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" | sudo tee /etc/apt/sources.list.d/google-chrome.list
    sudo apt-get update
    sudo apt-get install -y google-chrome-stable
fi

# 3. Setup Virtual Environment
echo "[3/6] Setting up Python virtual environment..."
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt
else
    echo "Warning: requirements.txt not found. Skipping pip install."
fi

# 4. Setup Secrets Template
echo "[4/6] Creating .env template if missing..."
if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        cp .env.example .env
        echo "Created .env from .env.example. PLEASE FILL IT IN."
    else
        cat <<EOF > .env
DISCORD_TOKEN=your_token_here
OPENAI_TOKEN=your_openai_token_here
CHROME_PATH=/usr/bin/google-chrome-stable
EOF
        echo "Created basic .env. PLEASE FILL IT IN."
    fi
fi

# 5. Configure Systemd Service
echo "[5/6] Configuring systemd service..."
SERVICE_FILE="hms-victory.service"
if [ -f "hms-victory.service.template" ]; then
    # Replace placeholders in template
    # Assuming user is 'ubuntu' and path is current directory
    CURRENT_DIR=$(pwd)
    sed "s|{{WORKING_DIR}}|$CURRENT_DIR|g; s|{{USER}}|$USER|g" hms-victory.service.template > $SERVICE_FILE
    
    sudo cp $SERVICE_FILE /etc/systemd/system/
    sudo systemctl daemon-reload
    echo "Systemd service $SERVICE_FILE installed but not started yet."
else
    echo "Warning: hms-victory.service.template not found. Skipping service setup."
fi

# 6. Final Steps
echo "[6/6] Setup complete!"
echo "--------------------------------------"
echo "Next steps:"
echo "1. Edit the '.env' file with your tokens."
echo "2. Run 'sudo systemctl enable hms-victory'"
echo "3. Run 'sudo systemctl start hms-victory'"
echo "4. Use './update_bot.sh' for future updates."
echo "--------------------------------------"
