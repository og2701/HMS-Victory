import sqlite3
import threading
from contextlib import contextmanager

DB_FILE = 'database.db'

class DatabaseManager:
    _connection = None
    # The bot shares ONE sqlite connection across the asyncio loop, APScheduler
    # jobs and the Selenium render thread pool. A reentrant lock serialises every
    # cursor so concurrent threads cannot interleave on the shared connection
    # ("recursive use of cursors" / silent read corruption). RLock so a thread
    # already inside a locked block (e.g. award_badge during a transfer) can
    # re-acquire without deadlocking.
    _lock = threading.RLock()

    @classmethod
    def get_connection(cls):
        if cls._connection is None:
            cls._connection = sqlite3.connect(DB_FILE, check_same_thread=False)
            cls._connection.execute("PRAGMA journal_mode=WAL")
            cls._connection.execute("PRAGMA synchronous=NORMAL")
            # Wait (instead of erroring) if another writer holds the file lock.
            cls._connection.execute("PRAGMA busy_timeout=5000")
        return cls._connection

    @staticmethod
    def execute(query, params=()):
        with DatabaseManager._lock:
            conn = DatabaseManager.get_connection()
            c = conn.cursor()
            c.execute(query, params)
            conn.commit()
            return c.rowcount

    @staticmethod
    def execute_insert(query, params=()):
        """Run an INSERT and return the new row id (cursor.lastrowid)."""
        with DatabaseManager._lock:
            conn = DatabaseManager.get_connection()
            c = conn.cursor()
            c.execute(query, params)
            conn.commit()
            return c.lastrowid

    @staticmethod
    def fetch_one(query, params=()):
        with DatabaseManager._lock:
            conn = DatabaseManager.get_connection()
            c = conn.cursor()
            c.execute(query, params)
            return c.fetchone()

    @staticmethod
    def fetch_all(query, params=()):
        with DatabaseManager._lock:
            conn = DatabaseManager.get_connection()
            c = conn.cursor()
            c.execute(query, params)
            return c.fetchall()

    @classmethod
    @contextmanager
    def locked_connection(cls):
        """Hold the global DB lock across a multi-statement block.

        Yields the shared connection wrapped in its own context manager so the
        block commits on success and rolls back on error - a drop-in replacement
        for ``with DatabaseManager.get_connection() as conn:`` that also serialises
        against every other DB caller.
        """
        with cls._lock:
            conn = cls.get_connection()
            with conn:
                yield conn

    @classmethod
    @contextmanager
    def transaction(cls):
        """Run several statements as one atomic, locked transaction.

        Yields a cursor. Commits on clean exit, rolls back on any exception.
        Relies on sqlite3's implicit transaction (opened on the first DML), so
        no explicit BEGIN is issued and it never nests transactions.
        """
        with cls._lock:
            conn = cls.get_connection()
            try:
                yield conn.cursor()
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    @classmethod
    def snapshot_to_file(cls, dest_path: str) -> None:
        """Write a transactionally-consistent copy of the live database to
        ``dest_path`` using SQLite's online backup API. Unlike copying the .db /
        -wal / -shm files separately (which can capture a torn state mid-commit),
        this always produces a single self-consistent file safe to restore."""
        with cls._lock:
            src = cls.get_connection()
            dest = sqlite3.connect(dest_path)
            try:
                src.backup(dest)
            finally:
                dest.close()

    @classmethod
    def shutdown_checkpoint(cls):
        """Flush the WAL into database.db (TRUNCATE) and close the connection on a
        clean shutdown.

        WAL is already crash-safe and ``snapshot_to_file`` already produces clean
        backups, so this isn't required for integrity - it just keeps the on-disk
        .db self-contained and the -wal file small. Safe to call when no connection
        was ever opened; ``get_connection`` transparently reopens if anything queries
        afterwards, so this never wedges the bot.
        """
        with cls._lock:
            if cls._connection is None:
                return
            try:
                cls._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                cls._connection.commit()
                cls._connection.close()
                cls._connection = None
                print("[db] WAL checkpoint (TRUNCATE) complete; connection closed.")
            except Exception as e:
                print(f"[db] WAL checkpoint failed: {e}")

    @staticmethod
    def transfer(src_id, dst_id, amount: int, reason: str = "Transfer") -> bool:
        """Atomically move ``amount`` UKP from one user to another.

        Both balance rows update inside a single locked transaction or neither
        does, so the closed-economy total is always conserved. Returns False
        (touching nothing) if the source lacks funds. The bank is just the bot's
        own user row, so this also covers user↔bank moves.
        """
        if amount <= 0:
            return False
        import time
        now = int(time.time())
        with DatabaseManager.transaction() as c:
            c.execute("SELECT balance FROM ukpence WHERE user_id = ?", (str(src_id),))
            row = c.fetchone()
            if not row or row[0] < amount:
                return False
            c.execute(
                "UPDATE ukpence SET balance = balance - ? WHERE user_id = ?",
                (amount, str(src_id)),
            )
            c.execute(
                "INSERT INTO ukpence (user_id, balance) VALUES (?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET balance = balance + ?",
                (str(dst_id), amount, amount),
            )
            c.execute(
                "INSERT INTO economy_transactions (timestamp, log_text) VALUES (?, ?)",
                (now, f"🔁 <@{src_id}> → <@{dst_id}> `{amount:,}` UKP|{reason}"),
            )
        return True

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
        # Auction feature removed - drop any legacy tables left over from older databases.
        c.execute("DROP TABLE IF EXISTS auctions")
        c.execute("DROP TABLE IF EXISTS auction_history")
        c.execute("DROP TABLE IF EXISTS auction_winners")
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
                total_tax_collected INTEGER NOT NULL DEFAULT 0,
                total_blackjack_in INTEGER NOT NULL DEFAULT 0,
                total_blackjack_out INTEGER NOT NULL DEFAULT 0,
                last_updated INTEGER NOT NULL DEFAULT 0
            )
        ''')
        # Migration: add total_tax_collected if missing on existing databases
        try:
            c.execute("ALTER TABLE bank ADD COLUMN total_tax_collected INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass  # Column already exists
        # Migration: add blackjack in/out columns if missing
        try:
            c.execute("ALTER TABLE bank ADD COLUMN total_blackjack_in INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("ALTER TABLE bank ADD COLUMN total_blackjack_out INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass
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
            INSERT OR IGNORE INTO bank (id, balance, total_revenue, total_tax_collected, total_blackjack_in, total_blackjack_out, last_updated)
            VALUES (1, 0, 0, 0, 0, 0, 0)
        ''')
        
        # Calculate the correct bank balance from the closed economy total (800,000 UKP)
        # Bank = 800,000 - sum(all non-bot user balances)
        from config import BOT_ID
        c.execute("SELECT COALESCE(SUM(balance), 0) FROM ukpence WHERE user_id != ?", (str(BOT_ID),))
        total_user_balances = c.fetchone()[0]
        correct_bank_balance = max(800_000 - total_user_balances, 0)
        
        # Set bot's ukpence to the correct bank balance
        c.execute("INSERT OR REPLACE INTO ukpence (user_id, balance) VALUES (?, ?)", (str(BOT_ID), correct_bank_balance))
        
        # Sync the bank table to match
        import time as _time
        c.execute("UPDATE bank SET balance = ?, last_updated = ? WHERE id = 1", (correct_bank_balance, int(_time.time())))
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

        c.execute('''
            CREATE TABLE IF NOT EXISTS roast_targets (
                user_id TEXT PRIMARY KEY,
                count INTEGER NOT NULL DEFAULT 0
            )
        ''')

        # Per-user, per-(UTC)-day roast usage, so the daily limit survives restarts.
        c.execute('''
            CREATE TABLE IF NOT EXISTS roast_usage (
                user_id TEXT NOT NULL,
                date TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, date)
            )
        ''')

        c.execute('''
            CREATE TABLE IF NOT EXISTS scheduled_predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id TEXT NOT NULL,
                creator_id TEXT NOT NULL,
                title TEXT NOT NULL,
                opt1 TEXT NOT NULL,
                opt2 TEXT NOT NULL,
                duration_minutes INTEGER NOT NULL,
                scheduled_ts INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at INTEGER NOT NULL,
                cm_message_id TEXT
            )
        ''')

        c.execute("PRAGMA table_info(scheduled_predictions)")
        columns = [column[1] for column in c.fetchall()]
        if 'cm_message_id' not in columns:
            c.execute("ALTER TABLE scheduled_predictions ADD COLUMN cm_message_id TEXT")
        # Multi-option support: full outcome list stored as a JSON array. opt1/opt2
        # are kept (NOT NULL) for backward compatibility and hold the first two.
        if 'options_json' not in columns:
            c.execute("ALTER TABLE scheduled_predictions ADD COLUMN options_json TEXT")

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
            ('high_roller', 'High Roller', 'Reach a balance of 30,000 UKPence', '💰', 'Gold'),
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
            ('double_or_nothing', 'Double or Nothing', 'Win a prediction where you bet more than 50% of your total balance', '🎲', 'Gold'),
            ('local_legend', 'Local Legend', 'Have a single message receive 10 or more unique reactions', '🌟', 'Silver'),
            ('town_crier', 'Town Crier', 'Post the first message of the day in the server', '🔔', 'Bronze'),
            ('pillar_1', 'Pillar of the Community (1 Year)', 'Be a member of the server for at least 1 year', '🧱', 'Bronze'),
            ('pillar_3', 'Pillar of the Community (3 Years)', 'Be a member of the server for at least 3 years', '🏛️', 'Silver'),
            ('pillar_5', 'Pillar of the Community (5 Years)', 'Be a member of the server for at least 5 years', '🏰', 'Gold'),
            ('weekend_warrior', 'Weekend Warrior', 'Send 800 or more messages over a single weekend', '⚔️', 'Silver'),
            ('global_citizen', 'Global Citizen', 'Send messages in 5 different channels within 5 minutes', '🗺️', 'Bronze'),
            ('victory_sponsor', 'Victory Sponsor', 'Transfer UKPence directly to HMS Victory (the bank)', '⚓', 'Silver')
        ]
        for b_id, b_name, b_desc, b_icon, b_rarity in badges:
            c.execute("INSERT OR REPLACE INTO badges (id, name, description, icon_path, rarity) VALUES (?, ?, ?, ?, ?)",
                      (b_id, b_name, b_desc, b_icon, b_rarity))

        # Auction feature removed - purge the now-unobtainable market_manipulator badge.
        c.execute("DELETE FROM user_badges WHERE badge_id = 'market_manipulator'")
        c.execute("DELETE FROM badges WHERE id = 'market_manipulator'")

        conn.commit()
        
        # Award every badge to the bot itself
        from config import BOT_ID
        import time
        now_ts = int(time.time())
        c.execute("SELECT id FROM badges")
        all_badge_ids = [row[0] for row in c.fetchall()]
        for badge_id in all_badge_ids:
            c.execute(
                "INSERT OR IGNORE INTO user_badges (user_id, badge_id, awarded_at) VALUES (?, ?, ?)",
                (str(BOT_ID), badge_id, now_ts)
            )
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