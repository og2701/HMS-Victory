
import json
import os
import sqlite3
from datetime import datetime

DB_FILE = "database.db"
PAY_LOG_FILE = "pay_log.json"

def migrate():
    if not os.path.exists(PAY_LOG_FILE):
        print("No pay_log.json found, skipping.")
        return

    try:
        with open(PAY_LOG_FILE, "r") as f:
            data = json.load(f)
    except Exception as e:
        print(f"Error reading JSON: {e}")
        return

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    # Ensure table exists (redundant but safe)
    c.execute('''
        CREATE TABLE IF NOT EXISTS pay_transfers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER NOT NULL,
            payer_id TEXT NOT NULL,
            recipient_id TEXT NOT NULL,
            amount INTEGER NOT NULL
        )
    ''')
    
    count = 0
    for entry in data:
        try:
            # Convert ISO timestamp to unix
            dt = datetime.fromisoformat(entry["timestamp"])
            ts = int(dt.timestamp())
            
            # Check if exists to avoid dupes (basic)
            c.execute("SELECT id FROM pay_transfers WHERE timestamp = ? AND payer_id = ? AND recipient_id = ? AND amount = ?",
                      (ts, entry["payer_id"], entry["recipient_id"], entry["amount"]))
            if not c.fetchone():
                c.execute("INSERT INTO pay_transfers (timestamp, payer_id, recipient_id, amount) VALUES (?, ?, ?, ?)",
                          (ts, entry["payer_id"], entry["recipient_id"], entry["amount"]))
                count += 1
        except Exception as e:
            print(f"Error migrating entry {entry}: {e}")

    conn.commit()
    conn.close()
    print(f"Migrated {count} entries.")

if __name__ == "__main__":
    migrate()
