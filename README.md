# 🚢 HMS-Victory

A high-performance Discord bot for economy, social interactions, and server management.

## 🛠️ Instance Setup (New Server)

To set up the bot on a fresh Ubuntu instance, follow these steps:

1.  **Clone the Repository:**
    ```bash
    git clone https://github.com/og2701/HMS-Victory.git
    cd HMS-Victory
    ```

2.  **Run the Setup Script:**
    This script installs system dependencies (Python, Chrome, SQLite), creates a virtual environment, and installs a systemd service.
    ```bash
    ./setup_instance.sh
    ```

3.  **Configure Environment Variables:**
    Edit the generated `.env` file with your tokens:
    ```bash
    nano .env
    ```
    *Fill in `DISCORD_TOKEN` and `OPENAI_TOKEN`.*

4.  **Start the Bot:**
    ```bash
    sudo systemctl enable hms-victory
    sudo systemctl start hms-victory
    ```

---

## 🔄 Updates & Maintenance

### Updating the Bot
To pull the latest code from GitHub and restart the bot safely:
```bash
./update_bot.sh
```
*Note: This script safely stops the service, pulls the latest code, and restarts it.*

### Viewing Logs
To see the bot's live output:
```bash
journalctl -f -u hms-victory.service
```

---

## 💾 Backups & Disaster Recovery

### How Backups Work
- **Remote Backups:** Every **5 minutes**, the bot bundles the SQLite database (`.db`, `-wal`, `-shm`) into a ZIP file and sends it to the `#data-backup` channel.
- **Data Persistence:** JSON data (predictions, streaks, etc.) is stored in `data/json/` and backed up alongside the database.

### Restoring the Database
If you move to a new instance or lose your local database:
1.  Ensure your `DISCORD_TOKEN` is set in `.env`.
2.  Start the bot normally (`sudo systemctl start hms-victory`).
3.  The bot will detect the missing `database.db`, automatically fetch the latest ZIP from the Discord backup channel, and extract it back into place.

---

## 📂 Project Structure

- `lib/`: Core bot logic and features.
- `commands/`: Discord slash commands.
- `data/json/`: Persistent JSON data (ignored by Git).
- `scripts/`: Maintenance and migration scripts.
- `deployment/`: Systemd service templates and legacy start scripts.
- `update_bot.sh`: The primary tool for server maintenance.
