import sqlite3
from contextlib import contextmanager

DB_FILE = 'database.db'

class DatabaseManager:
    @staticmethod
    @contextmanager
    def get_connection():
        conn = sqlite3.connect(DB_FILE)
        try:
            yield conn
        finally:
            conn.close()

    @staticmethod
    def execute(query, params=()):
        with DatabaseManager.get_connection() as conn:
            c = conn.cursor()
            c.execute(query, params)
            conn.commit()
            return c.lastrowid

    @staticmethod
    def fetch_one(query, params=()):
        with DatabaseManager.get_connection() as conn:
            c = conn.cursor()
            c.execute(query, params)
            return c.fetchone()

    @staticmethod
    def fetch_all(query, params=()):
        with DatabaseManager.get_connection() as conn:
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
        conn.commit()

if __name__ == '__main__':
    init_db()