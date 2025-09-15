import sqlite3

def init_db():
    conn = sqlite3.connect('database.db')
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
    conn.commit()
    conn.close()

if __name__ == '__main__':
    init_db()