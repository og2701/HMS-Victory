"""Tiered bond rates, the bond book limit, the additive migration and demurrage on bonds.

  - bonds opened before basis-point rates existed keep paying their whole-percent rate_pct;
  - new bonds pay rate_bp on the first tier_split UKP and upper_rate_bp on the rest;
  - the total-locked limit refuses a bond that would push the book past it;
  - the bonds migration only adds nullable columns and is safe to run twice;
  - wealth demurrage counts bonded principal but only ever charges the wallet.
"""

import asyncio
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config

TOTAL_SUPPLY = 800_000


def _fresh_economy(tmpdir):
    import database

    if database.DatabaseManager._connection is not None:
        database.DatabaseManager._connection.close()
        database.DatabaseManager._connection = None
    database.DB_FILE = os.path.join(tmpdir, "test.db")
    database.init_db()

    import lib.economy.economy_manager as economy_manager

    economy_manager._HIST_LAST.clear()
    return economy_manager, database


def _total_supply(database):
    return sum(r[0] for r in database.DatabaseManager.fetch_all("SELECT balance FROM ukpence"))


def _insert_bond(database, user_id, principal, *, rate_pct=30, term=30, matures_ts=9_999_999_999,
                 status="active"):
    """A bond row written the way the old code did: no basis-point columns."""
    database.DatabaseManager.execute(
        "INSERT INTO bonds (user_id, principal, rate_pct, term_days, opened_ts, matures_ts, status) "
        "VALUES (?, ?, ?, ?, 0, ?, ?)",
        (str(user_id), principal, rate_pct, term, matures_ts, status))


# ---------------------------------------------------------------------------
# interest_for
# ---------------------------------------------------------------------------
def test_legacy_whole_percent_bonds_pay_exactly_as_before():
    from lib.economy.bonds import interest_for, bond_interest
    assert interest_for(5000, 30) == 1500
    assert interest_for(917, 30) == 275            # integer floor, as the old code did
    assert interest_for(4691, 2) == 93
    legacy = {"principal": 5000, "rate_pct": 30, "rate_bp": None, "upper_rate_bp": None,
              "tier_split": None}
    assert bond_interest(legacy) == 1500


def test_tiered_basis_point_bonds():
    from lib.economy.bonds import interest_for
    # 30-day: 15% on the first 5,000, 8% on the rest.
    assert interest_for(10_000, 15, 1500, 800, 5000) == 750 + 400
    assert interest_for(5_000, 15, 1500, 800, 5000) == 750
    assert interest_for(3_000, 15, 1500, 800, 5000) == 450      # never reaches the upper tier
    assert interest_for(7_500, 15, 1500, 800, 5000) == 750 + 200
    # 7-day and 3-day.
    assert interest_for(10_000, 3, 300, 160, 5000) == 150 + 80
    assert interest_for(10_000, 1, 100, 50, 5000) == 50 + 25
    # A flat bp bond (no upper rate / no split) pays rate_bp on everything.
    assert interest_for(10_000, 15, 1500, None, None) == 1500


def test_config_terms_pay_more_per_week_for_longer_terms():
    """Rolling short bonds must never beat one long bond."""
    per_week = {d: lo / d for d, (lo, _hi) in config.BOND_TERMS_BP.items()}
    assert per_week[30] > per_week[7] > per_week[3]
    per_week_hi = {d: hi / d for d, (_lo, hi) in config.BOND_TERMS_BP.items()}
    assert per_week_hi[30] > per_week_hi[7] > per_week_hi[3]


# ---------------------------------------------------------------------------
# open / mature
# ---------------------------------------------------------------------------
def test_new_bond_stores_its_own_tiered_rates_and_pays_them():
    with tempfile.TemporaryDirectory() as tmpdir:
        economy, database = _fresh_economy(tmpdir)
        from lib.economy import bonds
        uid = 20_001
        assert economy.add_bb(uid, 12_000, reason="seed", taxable=False)
        bond, err = bonds.open_bond(uid, 10_000, 30)
        assert err is None
        assert (bond["rate_bp"], bond["upper_rate_bp"], bond["tier_split"]) == (1500, 800, 5000)
        assert bond["rate_pct"] == 15
        assert bonds.bond_interest(bond) == 1150
        assert bonds.rate_label(bond) == "15% / 8% over 5,000"

        # A later config change must not alter a bond already running.
        old = config.BOND_TERMS_BP
        config.BOND_TERMS_BP = {3: (1, 1), 7: (1, 1), 30: (1, 1)}
        try:
            database.DatabaseManager.execute("UPDATE bonds SET matures_ts = 0 WHERE id = ?",
                                             (bond["id"],))
            asyncio.run(bonds.mature_due(object()))
        finally:
            config.BOND_TERMS_BP = old
        assert economy.get_bb(uid) == 12_000 + 1150
        assert _total_supply(database) == TOTAL_SUPPLY


def test_bond_over_the_cap_is_refused():
    with tempfile.TemporaryDirectory() as tmpdir:
        economy, _ = _fresh_economy(tmpdir)
        from lib.economy import bonds
        uid = 20_002
        assert economy.add_bb(uid, 20_000, reason="seed", taxable=False)
        bond, err = bonds.open_bond(uid, config.BOND_MAX + 1, 30)
        assert bond is None and "maximum" in err
        assert economy.get_bb(uid) == 20_000


def test_legacy_active_bond_matures_on_its_original_rate():
    with tempfile.TemporaryDirectory() as tmpdir:
        economy, database = _fresh_economy(tmpdir)
        from lib.economy import bonds
        uid = 20_003
        _insert_bond(database, uid, 5_000, rate_pct=30, matures_ts=0)
        # Legacy rows read back with NULL bp columns.
        g = bonds.get_active(uid)
        assert g["rate_bp"] is None and bonds.rate_label(g) == "30%"
        asyncio.run(bonds.mature_due(object()))
        assert economy.get_bb(uid) == 6_500           # 5,000 principal + 30%
        assert bonds.get_active(uid) is None


# ---------------------------------------------------------------------------
# book limit
# ---------------------------------------------------------------------------
def test_book_limit_refuses_a_bond_that_would_overfill_it():
    with tempfile.TemporaryDirectory() as tmpdir:
        economy, database = _fresh_economy(tmpdir)
        from lib.economy import bonds
        old = getattr(config, "BOND_BOOK_LIMIT", None)
        config.BOND_BOOK_LIMIT = 150_000
        try:
            for i in range(29):                       # 145,000 already locked
                _insert_bond(database, 30_000 + i, 5_000)
            _insert_bond(database, 40_000, 9_000, status="matured")   # paid back: not counted
            uid = 20_004
            assert economy.add_bb(uid, 20_000, reason="seed", taxable=False)

            bond, err = bonds.open_bond(uid, 6_000, 30)
            assert bond is None and "bond book is full" in err
            assert economy.get_bb(uid) == 20_000      # nothing moved
            assert bonds.get_active(uid) is None

            bond, err = bonds.open_bond(uid, 5_000, 30)   # exactly fills it
            assert err is None and bond["principal"] == 5_000
            assert bonds.total_active_principal() == 150_000
        finally:
            config.BOND_BOOK_LIMIT = old


# ---------------------------------------------------------------------------
# migration
# ---------------------------------------------------------------------------
def test_migration_adds_nullable_columns_and_is_idempotent():
    import database
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "old.db")
        conn = sqlite3.connect(path)
        conn.execute("""CREATE TABLE bonds (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, principal INTEGER NOT NULL,
            rate_pct INTEGER NOT NULL, term_days INTEGER NOT NULL, opened_ts INTEGER NOT NULL,
            matures_ts INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'active')""")
        conn.execute("INSERT INTO bonds (user_id, principal, rate_pct, term_days, opened_ts, "
                     "matures_ts, status) VALUES ('1', 5000, 30, 30, 0, 1, 'active')")
        conn.commit()
        conn.close()

        if database.DatabaseManager._connection is not None:
            database.DatabaseManager._connection.close()
            database.DatabaseManager._connection = None
        database.DB_FILE = path
        database.init_db()
        database.init_db()                             # second run must be a no-op

        cols = {r[1] for r in database.DatabaseManager.fetch_all("PRAGMA table_info(bonds)")}
        assert {"rate_bp", "upper_rate_bp", "tier_split"} <= cols
        row = database.DatabaseManager.fetch_one(
            "SELECT principal, rate_pct, rate_bp, upper_rate_bp, tier_split FROM bonds")
        assert row == (5000, 30, None, None, None)     # existing bond left untouched


# ---------------------------------------------------------------------------
# demurrage
# ---------------------------------------------------------------------------
def _run_demurrage(tmpdir):
    from lib.bot import scheduled_tasks
    old = scheduled_tasks.ECONOMY_METRICS_FILE
    scheduled_tasks.ECONOMY_METRICS_FILE = os.path.join(tmpdir, "metrics.json")
    try:
        asyncio.run(scheduled_tasks.apply_wealth_demurrage(None))
    finally:
        scheduled_tasks.ECONOMY_METRICS_FILE = old


def test_demurrage_counts_bonded_principal_but_only_charges_the_wallet():
    with tempfile.TemporaryDirectory() as tmpdir:
        economy, database = _fresh_economy(tmpdir)
        from lib.economy import bonds
        old = (config.WEALTH_DEMURRAGE_THRESHOLD, config.WEALTH_DEMURRAGE_RATE,
               getattr(config, "BOND_COUNTS_FOR_DEMURRAGE", True))
        config.WEALTH_DEMURRAGE_THRESHOLD, config.WEALTH_DEMURRAGE_RATE = 10_000, 0.05
        config.BOND_COUNTS_FOR_DEMURRAGE = True
        try:
            saver, hoarder, tiny = 21_001, 21_002, 21_003
            # saver: 8,000 in the wallet + a real 10,000 bond -> effective 18,000.
            assert economy.add_bb(saver, 18_000, reason="seed", taxable=False)
            bond, err = bonds.open_bond(saver, 10_000, 30)
            assert err is None and economy.get_bb(saver) == 8_000
            # hoarder: 12,000 liquid, no bond -> charged as before.
            assert economy.add_bb(hoarder, 12_000, reason="seed", taxable=False)
            # tiny: 50 in the wallet, a huge bond -> the plan exceeds the wallet and is clamped.
            assert economy.add_bb(tiny, 50, reason="seed", taxable=False)
            _insert_bond(database, tiny, 100_000)

            _run_demurrage(tmpdir)

            assert economy.get_bb(saver) == 8_000 - 400        # (18,000 - 10,000) * 5%
            assert economy.get_bb(hoarder) == 12_000 - 100
            assert economy.get_bb(tiny) == 0                   # clamped to the wallet
            # Locked funds are never touched.
            assert bonds.get_active(saver)["principal"] == 10_000
            assert bonds.get_active(tiny)["principal"] == 100_000
            assert bonds.get_active(saver)["status"] == "active"
            assert _total_supply(database) == TOTAL_SUPPLY
        finally:
            (config.WEALTH_DEMURRAGE_THRESHOLD, config.WEALTH_DEMURRAGE_RATE,
             config.BOND_COUNTS_FOR_DEMURRAGE) = old


def test_demurrage_ignores_bonds_when_switched_off():
    with tempfile.TemporaryDirectory() as tmpdir:
        economy, _ = _fresh_economy(tmpdir)
        from lib.economy import bonds
        old = (config.WEALTH_DEMURRAGE_THRESHOLD, config.WEALTH_DEMURRAGE_RATE,
               getattr(config, "BOND_COUNTS_FOR_DEMURRAGE", True))
        config.WEALTH_DEMURRAGE_THRESHOLD, config.WEALTH_DEMURRAGE_RATE = 10_000, 0.05
        config.BOND_COUNTS_FOR_DEMURRAGE = False
        try:
            saver = 21_004
            assert economy.add_bb(saver, 18_000, reason="seed", taxable=False)
            assert bonds.open_bond(saver, 10_000, 30)[1] is None
            _run_demurrage(tmpdir)
            assert economy.get_bb(saver) == 8_000             # under the threshold on its own
        finally:
            (config.WEALTH_DEMURRAGE_THRESHOLD, config.WEALTH_DEMURRAGE_RATE,
             config.BOND_COUNTS_FOR_DEMURRAGE) = old
