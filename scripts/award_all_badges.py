import sys
import os

# Add the project root to the python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import award_badge
from config import USERS

def main():
    if len(sys.argv) < 2:
        user_id = str(USERS.OGGERS)
        print(f"No user ID provided, defaulting to Oggers ({user_id})")
    else:
        user_id = sys.argv[1]

    badges = [
        'hof', 'first_purchase', 'shutcoin_user', 'reply_chain', 
        'active_chatter', 'top_chatter', 'stage_fan', 'christmas',
        'halloween', 'vc_legend', 'screensharer', 'americanism_victim',
        'announcement_fast', 'minor_announcement_fast', 'roaster',
        'roast_victim', 'triple_reply', 'shut_victim', 'server_booster',
        'yearly_booster'
    ]

    print(f"Awarding all badges to user ID: {user_id}...")
    for badge in badges:
        result = award_badge(user_id, badge)
        status = "Success" if result else "Already has it or Error"
        print(f" - {badge}: {status}")

    print("\nDone! Run /rank in Discord to see the badges.")

if __name__ == "__main__":
    main()
