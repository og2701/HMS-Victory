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
        return c.lastrowid

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
                tertiary_color TEXT DEFAULT '#FFFFFF'
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
                icon_path TEXT NOT NULL
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
        
        # Initial badge data
        badges = [
            ('hof', 'Hall of Fame', 'Get into the Hall of Fame', '🏆'),
            ('first_purchase', 'First Purchase', 'Purchase your first shop item', '🛍️'),
            ('shutcoin_user', 'Shutcoin User', 'Use a shutcoin', '🤐'),
            ('reply_chain', 'Chain Linker', 'Be part of a reply chain', '⛓️'),
            ('active_chatter', 'Active Chatter', 'Achieve a certain level of activity in a day', '⚡'),
            ('top_chatter', 'Elite Talker', 'One of the top 5 daily chatters', '🥇'),
            ('stage_fan', 'Stage Fan', 'Attend a stage event for X amount of time', '🎭')
        ]
        for b_id, b_name, b_desc, b_icon in badges:
            c.execute("INSERT OR REPLACE INTO badges (id, name, description, icon_path) VALUES (?, ?, ?, ?)", 
                      (b_id, b_name, b_desc, b_icon))
        
        conn.commit()

if __name__ == '__main__':
    init_db()

def award_badge(user_id: str, badge_id: str):
    import time
    try:
        DatabaseManager.execute(
            "INSERT OR IGNORE INTO user_badges (user_id, badge_id, awarded_at) VALUES (?, ?, ?)",
            (str(user_id), badge_id, int(time.time()))
        )
        return True
    except Exception as e:
        print(f"Error awarding badge: {e}")
        return False

def get_user_badges(user_id: str):
    query = """
        SELECT b.id, b.name, b.description, b.icon_path, ub.awarded_at
        FROM badges b
        JOIN user_badges ub ON b.id = ub.badge_id
        WHERE ub.user_id = ?
        ORDER BY ub.awarded_at ASC
    """
    return DatabaseManager.fetch_all(query, (str(user_id),))