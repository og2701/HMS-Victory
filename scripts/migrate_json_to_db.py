#!/usr/bin/env python3
"""
Migrate shut_counts.json and warden_targets.json into the SQLite database.

Run once on the VM before restarting the bot:
    python3 scripts/migrate_json_to_db.py
"""

import sys
import os
import json

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import DatabaseManager, init_db

JSON_DATA_DIR = os.path.join("data", "json")
SHUT_COUNTS_FILE = os.path.join(JSON_DATA_DIR, "shut_counts.json")
WARDEN_TARGETS_FILE = os.path.join(JSON_DATA_DIR, "warden_targets.json")


def load_json(path):
    if not os.path.exists(path):
        print(f"  ⏭️  {path} not found, skipping.")
        return None
    with open(path, "r") as f:
        return json.load(f)


def migrate_shut_counts():
    print("📦 Migrating shut_counts.json ...")
    data = load_json(SHUT_COUNTS_FILE)
    if data is None:
        return

    conn = DatabaseManager.get_connection()
    c = conn.cursor()
    migrated = 0
    for user_id, count in data.items():
        c.execute(
            "INSERT INTO shut_counts (user_id, count) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET count = count + ?",
            (str(user_id), int(count), int(count))
        )
        migrated += 1
    conn.commit()
    print(f"  ✅ Migrated {migrated} shut count entries.")


def migrate_warden_targets():
    print("📦 Migrating warden_targets.json ...")
    data = load_json(WARDEN_TARGETS_FILE)
    if data is None:
        return

    conn = DatabaseManager.get_connection()
    c = conn.cursor()
    migrated = 0
    for user_id, victim_ids in data.items():
        for victim_id in victim_ids:
            c.execute(
                "INSERT OR IGNORE INTO warden_targets (user_id, victim_id) VALUES (?, ?)",
                (str(user_id), str(victim_id))
            )
            migrated += 1
    conn.commit()
    print(f"  ✅ Migrated {migrated} warden target entries.")


def main():
    print("=" * 50)
    print("HMS Victory: JSON → SQLite Migration")
    print("=" * 50)
    print()

    # Ensure tables exist
    init_db()
    print()

    migrate_shut_counts()
    migrate_warden_targets()

    print()
    print("🎉 Migration complete!")
    print()

    # Verify
    sc = DatabaseManager.fetch_one("SELECT COUNT(*) FROM shut_counts")
    wt = DatabaseManager.fetch_one("SELECT COUNT(*) FROM warden_targets")
    print(f"  shut_counts rows:    {sc[0]}")
    print(f"  warden_targets rows: {wt[0]}")
    print()
    print("You can now safely rename/archive the old JSON files:")
    print(f"  mv {SHUT_COUNTS_FILE} {SHUT_COUNTS_FILE}.bak")
    print(f"  mv {WARDEN_TARGETS_FILE} {WARDEN_TARGETS_FILE}.bak")


if __name__ == "__main__":
    main()
