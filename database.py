import sqlite3
from contextlib import contextmanager

DB_FILE = 'database.db'

class DatabaseManager:
    _connection = None

    @classmethod
    def get_connection(cls):
        if cls._connection is None:
            cls._connection = sqlite3.connect(DB_FILE, check_same_thread=False)
            cls._connection.execute("PRAGMA journal_mode=WAL")
            cls._connection.execute("PRAGMA synchronous=NORMAL")
        return cls._connection

    @staticmethod
    def execute(query, params=()):
        conn = DatabaseManager.get_connection()
        c = conn.cursor()
        c.execute(query, params)
        conn.commit()
        return c.rowcount

    @staticmethod
    def fetch_one(query, params=()):
        conn = DatabaseManager.get_connection()
        c = conn.cursor()
        c.execute(query, params)
        return c.fetchone()

    @staticmethod
    def fetch_all(query, params=()):
        conn = DatabaseManager.get_connection()
        c = conn.cursor()
        c.execute(query, params)
        return c.fetchall()

def init_db():
    with DatabaseManager.get_connection() as conn:
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS shutcoins (
                user_id TEXT PRIMARY KEY,
                balance INTEGER NOT NULL DEFAULT 0
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS ukpence (
                user_id TEXT PRIMARY KEY,
                balance INTEGER NOT NULL DEFAULT 0
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS xp (
                user_id TEXT PRIMARY KEY,
                xp INTEGER NOT NULL DEFAULT 0,
                last_xp_time INTEGER NOT NULL DEFAULT 0
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS auctions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_name TEXT NOT NULL,
                description TEXT,
                starting_bid INTEGER NOT NULL,
                current_bid INTEGER NOT NULL,
                current_bidder_id TEXT,
                end_time INTEGER NOT NULL,
                created_by TEXT NOT NULL,
                is_active BOOLEAN NOT NULL DEFAULT 1,
                channel_id TEXT,
                message_id TEXT
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS auction_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                auction_id INTEGER NOT NULL,
                user_id TEXT NOT NULL,
                bid_amount INTEGER NOT NULL,
                bid_time INTEGER NOT NULL,
                FOREIGN KEY (auction_id) REFERENCES auctions (id)
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS auction_winners (
                user_id TEXT NOT NULL,
                won_time INTEGER NOT NULL,
                auction_id INTEGER NOT NULL,
                item_name TEXT NOT NULL,
                winning_bid INTEGER NOT NULL
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS shop_inventory (
                item_id TEXT PRIMARY KEY,
                quantity INTEGER NOT NULL DEFAULT 0,
                max_quantity INTEGER,
                auto_restock BOOLEAN DEFAULT 0,
                restock_amount INTEGER DEFAULT 0,
                last_restock INTEGER DEFAULT 0
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS shop_purchases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                item_id TEXT NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 1,
                price_paid INTEGER NOT NULL,
                purchase_time INTEGER NOT NULL
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS bank (
                id INTEGER PRIMARY KEY,
                balance INTEGER NOT NULL DEFAULT 0,
                total_revenue INTEGER NOT NULL DEFAULT 0,
                last_updated INTEGER NOT NULL DEFAULT 0
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS daily_summaries (
                date TEXT PRIMARY KEY,
                data TEXT NOT NULL
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS economy_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER NOT NULL,
                log_text TEXT NOT NULL
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS circulation_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER NOT NULL,
                total_circulation INTEGER NOT NULL
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS user_rank_customization (
                user_id TEXT PRIMARY KEY,
                background TEXT DEFAULT 'unionjack.png',
                primary_color TEXT DEFAULT '#CF142B',
                secondary_color TEXT DEFAULT '#00247D',
                tertiary_color TEXT DEFAULT '#FFFFFF',
                title TEXT
            )
        ''')
        # Initialize the bank with a single row if it doesn't exist
        c.execute('''
            INSERT OR REPLACE INTO bank (id, balance, total_revenue, last_updated)
            VALUES (1, 
                COALESCE((SELECT balance FROM bank WHERE id = 1), 0),
                COALESCE((SELECT total_revenue FROM bank WHERE id = 1), 0),
                COALESCE((SELECT last_updated FROM bank WHERE id = 1), 0)
            )
        ''')
        # The above INSERT OR REPLACE with COALESCE is a bit complex for init, 
        # simpler to just INSERT OR IGNORE as before, but let's stick to the original logic 
        # which was INSERT OR IGNORE.
        c.execute('''
            INSERT OR IGNORE INTO bank (id, balance, total_revenue, last_updated)
            VALUES (1, 0, 0, 0)
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS pay_transfers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER NOT NULL,
                payer_id TEXT NOT NULL,
                recipient_id TEXT NOT NULL,
                amount INTEGER NOT NULL
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS badges (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                icon_path TEXT NOT NULL,
                rarity TEXT NOT NULL DEFAULT 'Bronze'
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS user_badges (
                user_id TEXT NOT NULL,
                badge_id TEXT NOT NULL,
                awarded_at INTEGER NOT NULL,
                PRIMARY KEY (user_id, badge_id),
                FOREIGN KEY (badge_id) REFERENCES badges (id)
            )
        ''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_pay_payer ON pay_transfers(payer_id)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_pay_recipient ON pay_transfers(recipient_id)')
        
        c.execute('''
            CREATE TABLE IF NOT EXISTS iceberg (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                level INTEGER NOT NULL,
                x INTEGER,
                y INTEGER,
                color TEXT,
                rotation INTEGER DEFAULT 0
            )
        ''')
        
        # Migration: Add x, y, color, rotation if they don't exist
        c.execute("PRAGMA table_info(iceberg)")
        columns = [column[1] for column in c.fetchall()]
        if 'x' not in columns:
            c.execute("ALTER TABLE iceberg ADD COLUMN x INTEGER")
        if 'y' not in columns:
            c.execute("ALTER TABLE iceberg ADD COLUMN y INTEGER")
        if 'color' not in columns:
            c.execute("ALTER TABLE iceberg ADD COLUMN color TEXT")
        if 'rotation' not in columns:
            c.execute("ALTER TABLE iceberg ADD COLUMN rotation INTEGER DEFAULT 0")

        c.execute('''
            CREATE TABLE IF NOT EXISTS pending_iceberg_submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                text TEXT NOT NULL,
                level INTEGER NOT NULL,
                price INTEGER NOT NULL,
                status TEXT DEFAULT 'pending',
                deny_reason TEXT
            )
        ''')

        # Migration: Add deny_reason column if it doesn't exist
        c.execute("PRAGMA table_info(pending_iceberg_submissions)")
        columns = [column[1] for column in c.fetchall()]
        if 'deny_reason' not in columns:
            c.execute("ALTER TABLE pending_iceberg_submissions ADD COLUMN deny_reason TEXT")

        c.execute('''
            CREATE TABLE IF NOT EXISTS shut_counts (
                user_id TEXT PRIMARY KEY,
                count INTEGER NOT NULL DEFAULT 0
            )
        ''')

        c.execute('''
            CREATE TABLE IF NOT EXISTS warden_targets (
                user_id TEXT NOT NULL,
                victim_id TEXT NOT NULL,
                PRIMARY KEY (user_id, victim_id)
            )
        ''')
        
        # Migration: Add rarity column if it doesn't exist
        c.execute("PRAGMA table_info(badges)")
        columns = [column[1] for column in c.fetchall()]
        if 'rarity' not in columns:
            c.execute("ALTER TABLE badges ADD COLUMN rarity TEXT NOT NULL DEFAULT 'Bronze'")

        # Migration for user_rank_customization
        c.execute("PRAGMA table_info(user_rank_customization)")
        columns = [column[1] for column in c.fetchall()]
        if 'title' not in columns:
            c.execute("ALTER TABLE user_rank_customization ADD COLUMN title TEXT")

        # Initial badge data
        badges = [
            ('hof', 'Hall of Fame', 'Get into the Hall of Fame', '🏆', 'Silver'),
            ('first_purchase', 'First Purchase', 'Purchase your first shop item', '🛍️', 'Bronze'),
            ('shutcoin_user', 'Shutcoin User', 'Use a shutcoin', '🤐', 'Bronze'),
            ('reply_chain', 'Chain Linker', 'Be part of a reply chain', '⛓️', 'Bronze'),
            ('active_chatter', 'Active Chatter', 'Achieve a certain level of activity in a day', '⚡', 'Bronze'),
            ('top_chatter', 'Elite Talker', 'One of the top 5 daily chatters', '🥇', 'Silver'),
            ('stage_fan', 'Stage Fan', 'Attend a stage event for X amount of time', '🎭', 'Silver'),
            ('christmas', 'Christmas', 'Message on Christmas day', '🎅', 'Silver'),
            ('halloween', 'Halloween', 'Message on Halloween', '🎃', 'Silver'),
            ('vc_legend', 'Chatterbox', 'One hour in a VC session', '🎙️', 'Silver'),
            ('screensharer', 'Sharing is Caring', 'Screenshare for 30 mins', '🖥️', 'Silver'),
            ('americanism_victim', "English (Simplified)", 'Caught by the Americanism filter', '🇺🇸', 'Bronze'),
            ('announcement_fast', 'Fast Hands', 'React to an announcement within 10 minutes', '📣', 'Silver'),
            ('minor_announcement_fast', 'Small Talker', 'React to a minor announcement within 10 minutes', '📢', 'Silver'),
            ('roaster', 'Chef', 'Use the roast command', '🔥', 'Silver'),
            ('roast_victim', 'Fried', 'Be targeted by a roast command', '💀', 'Bronze'),
            ('triple_reply', 'Popular', 'Have three people reply to one of your messages', '💬', 'Silver'),
            ('shut_victim', 'Silences', 'Be shut by a shutcoin', '🔇', 'Bronze'),
            ('server_booster', 'Supporter', 'Boost the server', '💎', 'Silver'),
            ('yearly_booster', 'Diamond Hands', 'Boost the server for a year', '👑', 'Gold'),
            ('high_roller', 'High Roller', 'Reach a balance of 100,000 UKPence', '💰', 'Gold'),
            ('philanthropist', 'Philanthropist', 'Give away a total of 10,000 UKPence using the /pay command', '💸', 'Silver'),
            ('bankrupt', 'Bankrupt', 'Reach exactly 0 UKPence after having at least 1,000 UKPence previously', '📉', 'Bronze'),
            ('shopaholic', 'Shopaholic', 'Purchase 10 items from the bot''s shop', '🛒', 'Silver'),
            ('party_animal', 'Party Animal', 'Attend 5 different Stage events', '🎉', 'Silver'),
            ('night_owl', 'Night Owl', 'Send 100 messages between 2 AM and 5 AM UK time', '👻', 'Bronze'),
            ('warden', 'The Warden', 'Successfully use a Shutcoin on 10 different people', '🔒', 'Gold'),
            ('oracle', 'Oracle', 'Win 7 predictions in a row', '🔮', 'Gold'),
            ('unlucky', 'Unlucky', 'Lose 5 predictions in a row', '🌧️', 'Bronze'),
            ('high_stakes', 'High Stakes', 'Place a bet of over 5,000 UKPence on a single prediction', '🎰', 'Silver'),
            ('morning_person', 'Morning Person', 'Send 50 messages between 6 AM and 9 AM UK time', '🌅', 'Bronze'),
            ('target_practice', 'Target Practice', 'Be the target of the /roast command 10 or more times', '🎯', 'Bronze'),
            ('new_year_new_me', 'New Year, New Me', 'Send a message within the first 5 minutes of the New Year (UK Time)', '🎆', 'Gold'),
            ('valentine', 'Valentine', 'Send someone UKPence on Valentine\'s Day', '💖', 'Silver'),
            ('april_fools', 'April Fools', 'Send a message on April 1st', '🤡', 'Silver'),
            ('guy_fawkes', 'Guy Fawkes', 'Send a message on November 5th', '🧨', 'Silver'),
            ('echo', 'Echo', '[REDACTED]', '🗣️', 'Secret'),
            ('lurker', 'Lurker', '[REDACTED]', '🪟', 'Secret'),
            ('indecisive', 'Indecisive', '[REDACTED]', '⚖️', 'Secret'),
            ('market_manipulator', 'Market Manipulator', 'Be the highest bidder on 3 different active auctions at the same time', '🏦', 'Silver'),
            ('double_or_nothing', 'Double or Nothing', 'Win a prediction where you bet more than 50% of your total balance', '🎲', 'Gold'),
            ('local_legend', 'Local Legend', 'Have a single message receive 10 or more unique reactions', '🌟', 'Silver'),
            ('town_crier', 'Town Crier', 'Post the first message of the day in the server', '🔔', 'Bronze'),
            ('pillar_1', 'Pillar of the Community (1 Year)', 'Be a member of the server for at least 1 year', '🧱', 'Bronze'),
            ('pillar_3', 'Pillar of the Community (3 Years)', 'Be a member of the server for at least 3 years', '🏛️', 'Silver'),
            ('pillar_5', 'Pillar of the Community (5 Years)', 'Be a member of the server for at least 5 years', '🏰', 'Gold'),
            ('weekend_warrior', 'Weekend Warrior', 'Send 800 or more messages over a single weekend', '⚔️', 'Silver'),
            ('global_citizen', 'Global Citizen', 'Send messages in 5 different channels within 5 minutes', '🗺️', 'Bronze')
        ]
        for b_id, b_name, b_desc, b_icon, b_rarity in badges:
            c.execute("INSERT OR REPLACE INTO badges (id, name, description, icon_path, rarity) VALUES (?, ?, ?, ?, ?)", 
                      (b_id, b_name, b_desc, b_icon, b_rarity))
        
        conn.commit()

if __name__ == '__main__':
    init_db()

def award_badge(user_id: str, badge_id: str):
    import time
    try:
        # returns rowcount: 1 if inserted, 0 if ignored
        result = DatabaseManager.execute(
            "INSERT OR IGNORE INTO user_badges (user_id, badge_id, awarded_at) VALUES (?, ?, ?)",
            (str(user_id), badge_id, int(time.time()))
        )
        return result > 0
    except Exception as e:
        print(f"Error awarding badge: {e}")
        return False

def get_user_badges(user_id: str):
    query = """
        SELECT b.id, b.name, b.description, b.icon_path, ub.awarded_at, b.rarity
        FROM badges b
        JOIN user_badges ub ON b.id = ub.badge_id
        WHERE ub.user_id = ?
        ORDER BY ub.awarded_at ASC
    """
    return DatabaseManager.fetch_all(query, (str(user_id),))