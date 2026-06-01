import sys
import os

# Add the project root to the python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import award_badge, init_db, DatabaseManager
from config import USERS

def main():
    if len(sys.argv) < 2:
        user_id = str(USERS.OGGERS)
        print(f"No user ID provided, defaulting to Oggers ({user_id})")
    else:
        user_id = sys.argv[1]

    # Derive the badge list from the database (single source of truth) so this
    # script can never drift from the canonical badge definitions in init_db.
    init_db()
    badges = [row[0] for row in DatabaseManager.fetch_all("SELECT id FROM badges ORDER BY id")]

    print(f"Awarding all badges to user ID: {user_id}...")
    for badge in badges:
        result = award_badge(user_id, badge)
        status = "Success" if result else "Already has it or Error"
        print(f" - {badge}: {status}")

    print("\nDone! Run /rank in Discord to see the badges.")

if __name__ == "__main__":
    main()
