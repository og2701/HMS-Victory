import os
import sys

# Add parent directory to sys.path to import modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import DatabaseManager, init_db

def remove_entry():
    init_db()

    rows = DatabaseManager.fetch_all("SELECT id, text, level FROM iceberg ORDER BY level, id")
    if not rows:
        print("🧊 The iceberg is empty. Nothing to remove.")
        return

    print("\n🧊 Current iceberg entries:\n")
    for idx, (entry_id, text, level) in enumerate(rows, 1):
        print(f"  {idx}. [Level {level}] {text}  (ID: {entry_id})")

    print(f"\n  0. Cancel\n")

    while True:
        try:
            choice = int(input("Select entry to remove: "))
        except (ValueError, EOFError):
            print("Invalid input.")
            continue

        if choice == 0:
            print("Cancelled.")
            return

        if 1 <= choice <= len(rows):
            break
        print(f"Please enter a number between 0 and {len(rows)}.")

    entry_id, text, level = rows[choice - 1]
    DatabaseManager.execute("DELETE FROM iceberg WHERE id = ?", (entry_id,))

    # Clear cache so it gets re-rendered next time
    cache_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "iceberg_cache.png")
    if os.path.exists(cache_path):
        os.remove(cache_path)

    print(f"\n✅ Removed '{text}' (Level {level}) from the iceberg. Cache cleared.")

if __name__ == "__main__":
    remove_entry()
