#!/bin/bash

INSTALL_DIR="/usr/local/sbin"
SCRIPT_NAME="rem-ukpence"
TARGET_PATH="$INSTALL_DIR/$SCRIPT_NAME"

do_work() {
    local db_path="$1"
    local user_id="$2"
    local amount="$3"
    local log_path="$4"

    if [ ! -f "$db_path" ]; then
        echo "Error: Database not found at $db_path"
        exit 1
    fi

    local current_balance=$(sqlite3 "$db_path" "SELECT balance FROM ukpence WHERE user_id = '$user_id';")

    if [ -z "$current_balance" ]; then
        echo "Error: User $user_id does not have a UKPence record."
        exit 1
    fi

    if [ "$current_balance" -lt "$amount" ]; then
        echo "Note: User $user_id only has $current_balance UKPence. Removing all."
        amount=$current_balance
    fi

    sqlite3 "$db_path" <<EOF
UPDATE ukpence SET balance = balance - $amount WHERE user_id = '$user_id';
EOF

    if [ $? -eq 0 ]; then
        if [ -n "$log_path" ] && [ "$log_path" != "REPLACE_ME_WITH_LOG_PATH" ]; then
            echo "$(date '+%Y-%m-%d %H:%M:%S') | REM | User: $user_id | Amount: $amount | By: $(logname 2>/dev/null || whoami)" >> "$log_path" 2>/dev/null
        fi
        local new_balance=$(sqlite3 "$db_path" "SELECT balance FROM ukpence WHERE user_id = '$user_id';")
        echo "Successfully removed $amount UKPence from user $user_id."
        echo "New balance: $new_balance UKPence"
    else
        echo "Error: Failed to update database."
        exit 1
    fi
}

if [[ "$(readlink -f "$0")" == "$TARGET_PATH" ]]; then
    DB_PATH="REPLACE_ME_WITH_ACTUAL_PATH"
    LOG_PATH="REPLACE_ME_WITH_LOG_PATH"
    
    if [ $# -lt 2 ]; then
        echo "Usage: $SCRIPT_NAME <user_id> <amount>"
        exit 1
    fi
    do_work "$DB_PATH" "$1" "$2" "$LOG_PATH"
    exit 0
fi

echo "--- HMS Victory Utility Setup ---"
PROJECT_ROOT="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
ACTUAL_DB_PATH="$PROJECT_ROOT/database.db"
ACTUAL_LOG_PATH="$PROJECT_ROOT/ukpence_scripts.log"

echo "Detected Project Root: $PROJECT_ROOT"
echo "Installing $SCRIPT_NAME to $INSTALL_DIR..."

TMP_SCRIPT="/tmp/$SCRIPT_NAME"
cp "$0" "$TMP_SCRIPT"

# Replace Database Path
ESCAPED_DB_PATH=$(echo "$ACTUAL_DB_PATH" | sed 's/\//\\\//g')
sed -i "s/REPLACE_ME_WITH_ACTUAL_PATH/$ESCAPED_DB_PATH/g" "$TMP_SCRIPT" 2>/dev/null || \
sed -i "" "s/REPLACE_ME_WITH_ACTUAL_PATH/$ESCAPED_DB_PATH/g" "$TMP_SCRIPT"

# Replace Log Path
ESCAPED_LOG_PATH=$(echo "$ACTUAL_LOG_PATH" | sed 's/\//\\\//g')
sed -i "s/REPLACE_ME_WITH_LOG_PATH/$ESCAPED_LOG_PATH/g" "$TMP_SCRIPT" 2>/dev/null || \
sed -i "" "s/REPLACE_ME_WITH_LOG_PATH/$ESCAPED_LOG_PATH/g" "$TMP_SCRIPT"

sudo mkdir -p "$INSTALL_DIR"
sudo touch "$ACTUAL_LOG_PATH"
sudo chmod 666 "$ACTUAL_LOG_PATH"
sudo mv "$TMP_SCRIPT" "$TARGET_PATH"
sudo chmod +x "$TARGET_PATH"

echo "Successfully installed $SCRIPT_NAME!"
echo "Note: Database targeted at $ACTUAL_DB_PATH"
echo "Note: Logs will be written to $ACTUAL_LOG_PATH"
echo "You can now use '$SCRIPT_NAME <user_id> <amount>' from any terminal."
echo "----------------------------------"
