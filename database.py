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
            old_src_balance = row[0]

            c.execute("SELECT balance FROM ukpence WHERE user_id = ?", (str(dst_id),))
            dst_row = c.fetchone()
            old_dst_balance = dst_row[0] if dst_row else 0

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

        new_src_balance = old_src_balance - amount
        new_dst_balance = old_dst_balance + amount
        # /pay bypasses set_balance/remove_amount, so record both legs here for the balance
        # graph (balance_history) and the statement ledger (user_transactions), after commit.
        try:
            from lib.economy.economy_manager import record_balance_point, record_transaction
            record_balance_point(src_id, new_src_balance, now)
            record_balance_point(dst_id, new_dst_balance, now)
            record_transaction(src_id, -amount, new_src_balance, reason, counterparty_id=dst_id, ts=now)
            record_transaction(dst_id, amount, new_dst_balance, reason, counterparty_id=src_id, ts=now)
        except Exception:
            pass

        from config import BOT_ID
        if new_dst_balance >= 30000 and old_dst_balance < 30000 and str(dst_id) != str(BOT_ID):
            try:
                from lib.bot.event_handlers import award_badge_notify
                award_badge_notify(int(dst_id), 'high_roller')
            except (ImportError, Exception):
                award_badge(dst_id, 'high_roller')
        return True

    @classmethod
    def save_archived_channel(cls, channel_id: int, original_category_id: int, original_overwrites: str):
        import time
        with DatabaseManager.transaction() as c:
            c.execute('''
                INSERT OR REPLACE INTO archived_channels (channel_id, original_category_id, original_overwrites, archived_at)
                VALUES (?, ?, ?, ?)
            ''', (str(channel_id), str(original_category_id) if original_category_id else None, original_overwrites, int(time.time())))

    @classmethod
    def get_archived_channel(cls, channel_id: int):
        with DatabaseManager.get_connection() as conn:
            c = conn.cursor()
            c.execute('SELECT original_category_id, original_overwrites FROM archived_channels WHERE channel_id = ?', (str(channel_id),))
            return c.fetchone()

    @classmethod
    def delete_archived_channel(cls, channel_id: int):
        with DatabaseManager.transaction() as c:
            c.execute('DELETE FROM archived_channels WHERE channel_id = ?', (str(channel_id),))

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
                total_higherlower_in INTEGER NOT NULL DEFAULT 0,
                total_higherlower_out INTEGER NOT NULL DEFAULT 0,
                total_slots_in INTEGER NOT NULL DEFAULT 0,
                total_slots_out INTEGER NOT NULL DEFAULT 0,
                total_videopoker_in INTEGER NOT NULL DEFAULT 0,
                total_videopoker_out INTEGER NOT NULL DEFAULT 0,
                total_reddog_in INTEGER NOT NULL DEFAULT 0,
                total_reddog_out INTEGER NOT NULL DEFAULT 0,
                total_tcp_in INTEGER NOT NULL DEFAULT 0,
                total_tcp_out INTEGER NOT NULL DEFAULT 0,
                total_roulette_in INTEGER NOT NULL DEFAULT 0,
                total_roulette_out INTEGER NOT NULL DEFAULT 0,
                total_mines_in INTEGER NOT NULL DEFAULT 0,
                total_mines_out INTEGER NOT NULL DEFAULT 0,
                total_penalty_in INTEGER NOT NULL DEFAULT 0,
                total_penalty_out INTEGER NOT NULL DEFAULT 0,
                total_chest_in INTEGER NOT NULL DEFAULT 0,
                total_chest_out INTEGER NOT NULL DEFAULT 0,
                last_updated INTEGER NOT NULL DEFAULT 0
            )
        ''')
        # Migration: add total_tax_collected if missing on existing databases
        try:
            c.execute("ALTER TABLE bank ADD COLUMN total_tax_collected INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass  # Column already exists
        # Migration: add per-game house P/L columns if missing
        for _col in ("total_blackjack_in", "total_blackjack_out",
                     "total_higherlower_in", "total_higherlower_out",
                     "total_slots_in", "total_slots_out",
                     "total_videopoker_in", "total_videopoker_out",
                     "total_reddog_in", "total_reddog_out",
                     "total_tcp_in", "total_tcp_out",
                     "total_roulette_in", "total_roulette_out",
                     "total_mines_in", "total_mines_out",
                     "total_penalty_in", "total_penalty_out",
                     "total_chest_in", "total_chest_out"):
            try:
                c.execute(f"ALTER TABLE bank ADD COLUMN {_col} INTEGER NOT NULL DEFAULT 0")
            except sqlite3.OperationalError:
                pass
        # One-time backfill: deposit_tax historically only credited total_tax_collected for
        # "Wealth tax" descriptions, so the inactivity tax + wealth demurrage were dropped from the
        # tax figure. Fold those missing amounts (from the durable user_transactions ledger - taxes
        # are stored as negative entries) into total_tax_collected EXACTLY ONCE; the tax_backfill_v1
        # guard makes it impossible to double-count on any later boot. The wealth tax already in the
        # counter is preserved (we only ADD the missing pieces).
        try:
            c.execute("ALTER TABLE bank ADD COLUMN tax_backfill_v1 INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            _bf = c.execute("SELECT COALESCE(tax_backfill_v1, 0) FROM bank WHERE id = 1").fetchone()
            if _bf is not None and not _bf[0]:
                _missing = c.execute(
                    "SELECT COALESCE(SUM(-amount), 0) FROM user_transactions "
                    "WHERE reason LIKE 'Inactivity tax%' OR reason LIKE 'Wealth demurrage%'"
                ).fetchone()[0] or 0
                c.execute("UPDATE bank SET total_tax_collected = total_tax_collected + ?, "
                          "tax_backfill_v1 = 1 WHERE id = 1", (_missing,))
                print(f"[db] Tax backfill: added {_missing} UKP (historical inactivity tax + "
                      f"demurrage) to total_tax_collected.")
        except sqlite3.Error as _e:
            print(f"[db] Tax backfill skipped (will retry next boot): {_e}")
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

        # One row per finished casino round (every game, every player). `staked` is the
        # total put at risk (incl. doubles/raises), `payout` the total returned, `net`
        # = payout - staked, and `result` is the normalised win/loss/push. Enables
        # per-user stats, leaderboards and accurate house analytics.
        c.execute('''
            CREATE TABLE IF NOT EXISTS casino_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                game TEXT NOT NULL,
                bet INTEGER NOT NULL,
                staked INTEGER NOT NULL,
                payout INTEGER NOT NULL,
                net INTEGER NOT NULL,
                outcome TEXT,
                result TEXT NOT NULL,
                timestamp INTEGER NOT NULL
            )
        ''')
        c.execute("CREATE INDEX IF NOT EXISTS idx_casino_results_user ON casino_results(user_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_casino_results_game ON casino_results(game)")

        # National Lottery: one row per round, plus aggregated per-user entries. A round
        # is 'open' (selling tickets) then 'drawn' (winner picked, pot paid). tickets_sold
        # is SUM(lottery_entries.tickets); the draw weights by ticket count.
        c.execute('''
            CREATE TABLE IF NOT EXISTS lottery_rounds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                status TEXT NOT NULL DEFAULT 'open',
                ticket_price INTEGER NOT NULL,
                ticket_cap INTEGER NOT NULL,
                rake_pct INTEGER NOT NULL DEFAULT 0,
                draw_ts INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                drawn_at INTEGER,
                winner_id TEXT,
                winning_ticket INTEGER,
                pot INTEGER,
                prize INTEGER,
                message_id TEXT,
                channel_id TEXT
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS lottery_entries (
                round_id INTEGER NOT NULL,
                user_id TEXT NOT NULL,
                tickets INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (round_id, user_id)
            )
        ''')
        c.execute("CREATE INDEX IF NOT EXISTS idx_lottery_entries_round ON lottery_entries(round_id)")
        # Small key/value store for lottery scheduling state (e.g. the next random-reminder
        # time) so it survives restarts instead of resetting each boot.
        c.execute('''
            CREATE TABLE IF NOT EXISTS lottery_state (
                key TEXT PRIMARY KEY,
                value INTEGER NOT NULL
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS circulation_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER NOT NULL,
                total_circulation INTEGER NOT NULL
            )
        ''')
        # Granular per-user balance history (every change, from any source) for /balance graph.
        c.execute('''
            CREATE TABLE IF NOT EXISTS balance_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                ts INTEGER NOT NULL,
                balance INTEGER NOT NULL
            )
        ''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_balance_history_user_ts ON balance_history (user_id, ts)')
        # Durable per-user ledger of signed money moves (with the human-readable reason that
        # flows through the economy chokepoints) for the /balance "Statement" feature.
        c.execute('''
            CREATE TABLE IF NOT EXISTS user_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                ts INTEGER NOT NULL,
                amount INTEGER NOT NULL,          -- signed: positive=credit, negative=debit
                balance_after INTEGER NOT NULL,
                reason TEXT NOT NULL,
                counterparty_id TEXT,
                source TEXT NOT NULL DEFAULT 'live'  -- 'live' or 'backfill' (reconstructed history)
            )
        ''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_user_transactions_user_ts ON user_transactions (user_id, ts)')
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
        # Net loser→winner flow from PvP games (Connect 4, Battleship, wagers). Kept separate
        # from pay_transfers so it feeds ONLY the anti-shuffle effective-wealth calc (it stops
        # "lose on purpose" being an untracked way to move UKP), without touching the /pay cap,
        # philanthropist badge, or benefits checks that read pay_transfers.
        c.execute('''
            CREATE TABLE IF NOT EXISTS game_transfers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER NOT NULL,
                loser_id TEXT NOT NULL,
                winner_id TEXT NOT NULL,
                amount INTEGER NOT NULL
            )
        ''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_game_loser ON game_transfers(loser_id)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_game_winner ON game_transfers(winner_id)')
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
        # Idempotency ledger for one-time badge UKPence rewards: a (user, badge) is paid at
        # most once, shared by the live grant hook and the backfill script.
        c.execute('''
            CREATE TABLE IF NOT EXISTS badge_rewards (
                user_id TEXT NOT NULL,
                badge_id TEXT NOT NULL,
                amount INTEGER NOT NULL,
                paid_at INTEGER NOT NULL,
                PRIMARY KEY (user_id, badge_id)
            )
        ''')
        # Dedup ledger for flag translations: a given message is translated to a given target
        # (the flag emoji) at most once, regardless of reaction add/remove churn or who reacts.
        c.execute('''
            CREATE TABLE IF NOT EXISTS translation_log (
                message_id TEXT NOT NULL,
                target     TEXT NOT NULL,
                PRIMARY KEY (message_id, target)
            )
        ''')
        # One row per finished Connect 4 match (kept separate from casino_results so PvP
        # games don't pollute the house-casino stats/leaderboard). winner_id NULL on a draw.
        c.execute('''
            CREATE TABLE IF NOT EXISTS connect4_results (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                winner_id  TEXT,
                loser_id   TEXT,
                stake      INTEGER NOT NULL,
                timestamp  INTEGER NOT NULL
            )
        ''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_c4_winner ON connect4_results(winner_id)')
        # Unified results for ALL 1v1 PvP wager games (connect4, battleship, and future ones).
        # winner_id NULL on a draw. outcome: 'win' | 'draw' | 'forfeit'. Kept out of
        # casino_results so PvP games don't skew the house-casino stats/leaderboard.
        c.execute('''
            CREATE TABLE IF NOT EXISTS pvp_results (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                game       TEXT NOT NULL,
                winner_id  TEXT,
                loser_id   TEXT,
                stake      INTEGER NOT NULL,
                outcome    TEXT,
                timestamp  INTEGER NOT NULL
            )
        ''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_pvp_game ON pvp_results(game)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_pvp_winner ON pvp_results(game, winner_id)')
        # One-time migration: fold the legacy connect4_results into the unified table. Guarded
        # on "no connect4 rows yet" so it runs exactly once (afterwards live games write here).
        if c.execute("SELECT COUNT(*) FROM pvp_results WHERE game='connect4'").fetchone()[0] == 0:
            c.execute(
                "INSERT INTO pvp_results (game, winner_id, loser_id, stake, outcome, timestamp) "
                "SELECT 'connect4', winner_id, loser_id, stake, "
                "CASE WHEN winner_id IS NULL THEN 'draw' ELSE 'win' END, timestamp "
                "FROM connect4_results")
        c.execute('CREATE INDEX IF NOT EXISTS idx_pay_payer ON pay_transfers(payer_id)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_pay_recipient ON pay_transfers(recipient_id)')
        # Fixed-term savings ("bonds"): principal held in the bank while locked; on maturity
        # the bank repays principal + interest. status: active | matured | withdrawn.
        c.execute('''
            CREATE TABLE IF NOT EXISTS bonds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                principal INTEGER NOT NULL,
                rate_pct INTEGER NOT NULL,
                term_days INTEGER NOT NULL,
                opened_ts INTEGER NOT NULL,
                matures_ts INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'active'
            )
        ''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_bonds_user ON bonds(user_id)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_bonds_status ON bonds(status)')
        
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

        # Custom rank-card background submissions awaiting staff approval. The 700 UKP
        # is charged on upload (stored in `price`) and refunded on denial; `filename`
        # is the saved file in data/rank_cards/ and becomes the user's background once
        # approved.
        c.execute('''
            CREATE TABLE IF NOT EXISTS pending_rank_background_submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                price INTEGER NOT NULL,
                status TEXT DEFAULT 'pending',
                deny_reason TEXT,
                cm_message_id TEXT,
                created_at INTEGER DEFAULT (strftime('%s','now'))
            )
        ''')

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

        # Rolling short-retention copy of every user message, so bulk deletes (ban purges,
        # mod sweeps) can be logged even though Discord only sends the message IDs and the
        # in-memory cache rarely still holds them. Purged daily past the retention window.
        c.execute('''
            CREATE TABLE IF NOT EXISTS message_archive (
                message_id TEXT PRIMARY KEY,
                channel_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                content TEXT NOT NULL,
                attachments TEXT,
                ts INTEGER NOT NULL
            )
        ''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_message_archive_ts ON message_archive (ts)')

        c.execute('''
            CREATE TABLE IF NOT EXISTS archived_channels (
                channel_id TEXT PRIMARY KEY,
                original_category_id TEXT,
                original_overwrites TEXT NOT NULL,
                archived_at INTEGER NOT NULL
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
            ('active_chatter', 'Active Chatter', 'Achieve a certain level of activity in a day', '⚡', 'Silver'),
            ('top_chatter', 'Elite Talker', 'One of the top 5 daily chatters', '🥇', 'Silver'),
            ('stage_fan', 'Stage Fan', 'Attend a stage event for X amount of time', '🎭', 'Silver'),
            ('christmas', 'Christmas', 'Message on Christmas day', '🎅', 'Silver'),
            ('halloween', 'Halloween', 'Message on Halloween', '🎃', 'Silver'),
            ('vc_legend', 'Chatterbox', 'One hour in a VC session', '🎙️', 'Silver'),
            ('screensharer', 'Sharing is Caring', 'Screenshare for 30 mins', '🖥️', 'Silver'),
            ('americanism_victim', "English (Simplified)", 'Caught by the Americanism filter', '🇺🇸', 'Bronze'),
            ('announcement_fast', 'Fast Hands', 'React to an announcement within 10 minutes', '📣', 'Bronze'),
            ('minor_announcement_fast', 'Small Talker', 'React to a minor announcement within 10 minutes', '📢', 'Bronze'),
            ('roaster', 'Chef', 'Use the roast command', '🔥', 'Bronze'),
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
            ('night_owl', 'Night Owl', 'Send 100 messages between 2 AM and 5 AM UK time', '👻', 'Silver'),
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
            ('double_or_nothing', 'Double or Nothing', 'Win a prediction where you bet more than 50% of your total balance', '🎲', 'Gold'),
            ('local_legend', 'Local Legend', 'Have a single message receive 10 or more unique reactions', '🌟', 'Silver'),
            ('town_crier', 'Town Crier', 'Post the first message of the day in the server', '🔔', 'Bronze'),
            ('pillar_1', 'Pillar of the Community (1 Year)', 'Be a member of the server for at least 1 year', '🧱', 'Bronze'),
            ('pillar_3', 'Pillar of the Community (3 Years)', 'Be a member of the server for at least 3 years', '🏛️', 'Silver'),
            ('pillar_5', 'Pillar of the Community (5 Years)', 'Be a member of the server for at least 5 years', '🏰', 'Gold'),
            ('weekend_warrior', 'Weekend Warrior', 'Send 800 or more messages over a single weekend', '⚔️', 'Silver'),
            ('global_citizen', 'Global Citizen', 'Send messages in 5 different channels within 5 minutes', '🗺️', 'Bronze'),
            ('victory_sponsor', 'Victory Sponsor', 'Transfer UKPence directly to HMS Victory (the bank)', '⚓', 'Bronze'),
            ('green_fingers', 'Green Fingers', 'Water the server tree for the first time', '🌱', 'Bronze'),
            ('sir_branchalot', 'Sir Branchalot', 'Water the server tree 100 times', '🌳', 'Silver'),
            ('drip', 'Drip Feed', 'Water the tree enough in one day to decay your reward down to 10 UKPence', '💧', 'Silver'),
            ('saver', 'Prudent Saver', 'Open your first bond', '🏦', 'Bronze'),
            ('bond_villain', 'Bond Villain', 'Earn 10,000 UKPence in total bond interest', '🕴️', 'Gold'),
            ('long_game', 'The Long Game', 'Open a 30-day bond', '⏳', 'Silver'),
            ('paper_hands', 'Paper Hands', 'Break a bond early and forfeit the interest', '🧻', 'Bronze'),
            ('on_the_dole', 'On the Dole', 'Claim benefits for the first time', '🧾', 'Bronze'),
            ('career_claimant', 'Career Claimant', 'Claim benefits 7 days in a row', '🛋️', 'Silver'),
            ('rock_bottom', 'Rock Bottom', 'Claim benefits with under 5 UKPence to your name', '🪨', 'Bronze'),
            ('good_samaritan', 'Good Samaritan', "Pay off another member's benefits fine for them", '🤝', 'Silver'),
            ('lucky_number', 'Lucky Number', 'Win a straight-up number bet on roulette (35:1) covering at most 3 numbers and making a net profit', '🎯', 'Gold'),
            ('slots_jackpot', 'Jackpot', 'Hit a Jackpot (three Crowns) on the Fruit Machine / Slots', '🎰', 'Gold'),
            ('zero_hero', 'Zero Hero', 'Be at the roulette table when the ball lands on the green zero', '🟢', 'Silver'),
            ('red_letter_day', 'Red Letter Day', 'Win 1,000 or more on a single roulette spin', '🔴', 'Silver'),
            ('squeaky_wheel', 'Squeaky Wheel', 'Be awarded UKPence for a support ticket', '🎟️', 'Bronze'),
            ('jack_of_all_trades', 'Jack of All Trades', 'Earn UKPence from 5 different income sources', '🧩', 'Silver'),
            # Connect 4 (1v1 wager game)
            ('first_blood', 'First Blood', 'Win your first Connect 4 match', '🩸', 'Bronze'),
            ('four_in_a_row', 'Four in a Row', 'Win 10 Connect 4 matches', '🟡', 'Silver'),
            ('trash_talker', 'Trash Talker', 'Win a Connect 4 match staked at 1,000 UKPence or more', '🗯️', 'Silver'),
            ('grandmaster', 'Grandmaster', 'Win 100 Connect 4 matches', '♟️', 'Gold'),
            # Higher or Lower
            ('on_the_up', 'On the Up', 'Win 3 Higher or Lower guesses in a single game', '🪜', 'Bronze'),
            ('vertigo', 'Vertigo', 'Reach a 5x multiplier in Higher or Lower and cash out', '🗼', 'Silver'),
            # Blackjack
            ('hot_hand', 'Hot Hand', 'Win 5 Blackjack hands in a row', '♠️', 'Gold'),
            # Casino (any game)
            ('dealers_choice', "Dealer's Choice", 'Play every casino game at least once', '🎴', 'Bronze'),
            ('on_a_heater', 'On a Heater', 'Win 5 casino games in a row', '♨️', 'Silver'),
            ('comeback_kid', 'Comeback Kid', 'Win a casino game after dropping below 100 UKPence', '🪃', 'Silver'),
            ('centurion', 'Centurion', 'Win 1,000 casino games in total', '🏵️', 'Gold'),
            # Translation
            ('ooga_booga', 'Ooga Booga', 'Have one of your messages translated to Caveman', '🦴', 'Bronze'),
            # Battleship
            ('broadside', 'Broadside', 'Win a Battleship match staked at 1,000 UKPence or more', '💣', 'Silver'),
            ('ironclad', 'Ironclad', 'Win a Battleship match without any of your own ships being hit', '🛡️', 'Gold'),
        ]
        for b_id, b_name, b_desc, b_icon, b_rarity in badges:
            c.execute("INSERT OR REPLACE INTO badges (id, name, description, icon_path, rarity) VALUES (?, ?, ?, ?, ?)",
                      (b_id, b_name, b_desc, b_icon, b_rarity))

        # Secret-tier badges are kept OUT of the open source: their names/icons live in an
        # encrypted blob (secret_badges.json.enc), decrypted at boot with BADGE_SECRET_KEY. With
        # no key they simply don't seed (see lib/economy/secret_config.py).
        try:
            from lib.economy import secret_config
            for s_id, s_name, s_desc, s_icon, s_rarity in secret_config.badges():
                c.execute("INSERT OR REPLACE INTO badges (id, name, description, icon_path, rarity) VALUES (?, ?, ?, ?, ?)",
                          (s_id, s_name, s_desc, s_icon, s_rarity))
        except Exception:
            pass

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