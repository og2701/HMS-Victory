import os
import sys

# Add parent directory to sys.path to import config
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import ICEBERG_DATA_FILE

def reset_iceberg():
    if os.path.exists(ICEBERG_DATA_FILE):
        try:
            os.remove(ICEBERG_DATA_FILE)
            print(f"✅ Successfully reset the iceberg. Deleted {ICEBERG_DATA_FILE}")
        except Exception as e:
            print(f"❌ Failed to delete iceberg data: {e}")
    else:
        print("ℹ️ Iceberg is already blank (no data file found).")

if __name__ == "__main__":
    reset_iceberg()
