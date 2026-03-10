import os
import sys

# Add parent directory to sys.path to import config
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import DatabaseManager, init_db

def reset_iceberg():
    # Ensure database is initialized
    init_db()
    try:
        count = DatabaseManager.execute("DELETE FROM iceberg")
        # Reset autoincrement
        DatabaseManager.execute("DELETE FROM sqlite_sequence WHERE name='iceberg'")
        
        # Clear cache file
        cache_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "iceberg_cache.png")
        if os.path.exists(cache_path):
            os.remove(cache_path)
            print(f"✅ Successfully reset the iceberg. Removed {count} entries and cleared cache.")
        else:
            print(f"✅ Successfully reset the iceberg. Removed {count} entries.")
    except Exception as e:
        print(f"❌ Failed to reset iceberg data: {e}")

if __name__ == "__main__":
    reset_iceberg()
