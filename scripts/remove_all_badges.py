import asyncio
import os
import sys

# Add the parent directory to the path so we can import database
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import DatabaseManager
from config import USERS

def main():
    # Default to Oggers (matches award_all_badges.py) - the old hardcoded id was stale.
    target_user_id = str(USERS.OGGERS)

    if len(sys.argv) > 1:
        target_user_id = sys.argv[1]

    print(f"Removing all badges from user ID: {target_user_id}...")

    try:
        with DatabaseManager.locked_connection() as conn:
            c = conn.cursor()
            c.execute("DELETE FROM user_badges WHERE user_id = ?", (target_user_id,))
            removed_count = c.rowcount
            conn.commit()

        print(f"Successfully removed {removed_count} badges from user {target_user_id}.")
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    main()
