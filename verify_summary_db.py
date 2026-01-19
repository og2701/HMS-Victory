import sys
import os
import json
import sqlite3
from datetime import datetime
import pytz

# Add the project root to sys.path
sys.path.append(os.getcwd())

from database import init_db, DatabaseManager
from lib.features.summary import load_summary_data, update_summary_data, initialize_summary_data

def test_summary_migration():
    print("Starting verification...")
    
    # 1. Initialize DB
    init_db()
    
    # 2. Test initialization
    initialize_summary_data(force_init=True)
    uk_timezone = pytz.timezone("Europe/London")
    today = datetime.now(uk_timezone).strftime("%Y-%m-%d")
    
    db_data = DatabaseManager.fetch_one("SELECT data FROM daily_summaries WHERE date = ?", (today,))
    if db_data:
        print(f"✅ Successfully initialized summary for {today} in database.")
    else:
        print(f"❌ Failed to initialize summary for {today} in database.")
        return

    # 3. Test update
    update_summary_data("total_messages")
    data = load_summary_data(today)
    if data.get("total_messages") == 1:
        print("✅ Successfully updated summary data in database.")
    else:
        print(f"❌ Failed to update summary data. Expected 1, got {data.get('total_messages')}")
        return

    # 4. Test migration from JSON
    test_date = "2000-01-01"
    os.makedirs("daily_summaries", exist_ok=True)
    test_json_path = f"daily_summaries/daily_summary_{test_date}.json"
    test_data = {"total_messages": 99, "total_members": 10}
    
    with open(test_json_path, "w") as f:
        json.dump(test_data, f)
    
    print(f"Created dummy JSON for {test_date}. Attempting migration...")
    
    migrated_data = load_summary_data(test_date)
    if migrated_data.get("total_messages") == 99:
        print("✅ Successfully migrated data from JSON to database.")
    else:
        print(f"❌ Migration failed. Expected 99, got {migrated_data.get('total_messages')}")
        return
        
    db_check = DatabaseManager.fetch_one("SELECT data FROM daily_summaries WHERE date = ?", (test_date,))
    if db_check:
        print("✅ Verified data exists in database after migration.")
    else:
        print("❌ Data not found in database after migration call.")
        return

    print("\nAll tests passed successfully!")

if __name__ == "__main__":
    test_summary_migration()
