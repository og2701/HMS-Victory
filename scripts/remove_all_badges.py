import asyncio
import os
import sys

# Add the parent directory to the path so we can import database
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import DatabaseManager

def main():
    # Oggers ID by default
    target_user_id = "226058098024218625"
    
    if len(sys.argv) > 1:
        target_user_id = sys.argv[1]
    
    print(f"Removing all badges from user ID: {target_user_id}...")
    
    try:
        with DatabaseManager.get_connection() as conn:
            c = conn.cursor()
            c.execute("DELETE FROM user_badges WHERE user_id = ?", (target_user_id,))
            removed_count = c.rowcount
            conn.commit()
            
        print(f"Successfully removed {removed_count} badges from user {target_user_id}.")
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    main()
