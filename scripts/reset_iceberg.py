import os
import sys

# Add parent directory to sys.path to import config
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import DatabaseManager

def reset_iceberg():
    try:
        count = DatabaseManager.execute("DELETE FROM iceberg")
        # Reset autoincrement
        DatabaseManager.execute("DELETE FROM sqlite_sequence WHERE name='iceberg'")
        print(f"✅ Successfully reset the iceberg. Removed {count} entries.")
    except Exception as e:
        print(f"❌ Failed to reset iceberg data: {e}")

if __name__ == "__main__":
    reset_iceberg()
