"""The /ukpeconomy card's numbers: summary maths, flow merging, the 24h change and empty states."""

import os
import sys
import time
from datetime import date
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OPENAI_TOKEN", "mock-token")

from lib.economy import economy_stats_html as es


def test_summary_counts_median_and_brackets():
    s = es._summary({"a": 0, "b": 100, "c": 500, "d": 2000, "e": 50000})
    assert s["total"] == 52600
    assert s["holders"] == 4
    assert s["over_1k"] == 2
    assert s["median"] == 2000                     # of the balances over 250: 500, 2000, 50000
    assert s["brackets"] == {"1-1k": 2, "1k-10k": 1, "10k-100k": 1, "100k+": 0}
    assert s["top"][0] == ("e", 50000)


def test_gini_runs_from_equal_to_one_person_owning_everything():
    assert es._summary({str(i): 100 for i in range(10)})["gini"] == 0
    lopsided = {str(i): 1 for i in range(99)} | {"rich": 1_000_000}
    assert es._summary(lopsided)["gini"] > 0.95


def test_small_flows_are_merged_so_every_band_can_be_labelled():
    to_players, to_bank = es._split_flows({"Rewards": 900, "Bond": 20, "Welcome": 30, "Tax": -500, "Shop": -10})
    assert to_players == [("Rewards", 900), ("Smaller payouts", 50)]
    assert to_bank == [("Tax", 500), ("Smaller sinks", 10)]


def test_week_flows_leave_out_player_to_player_transfers(monkeypatch):
    from lib.economy import statement
    rows = [(100, "reward", None), (-40, "casino", None), (-25, "casino", None), (300, "pay", "123")]
    monkeypatch.setattr(es, "DatabaseManager", SimpleNamespace(fetch_all=lambda *_a, **_k: rows))
    monkeypatch.setattr(statement, "_categorize",
                        lambda reason, cp=None: ("Transfers", "") if cp else (reason.title(), ""))
    assert es._week_flows() == {"Reward": 100, "Casino": -65}


class _Snapshots:
    def __init__(self, rows):
        self.rows = list(rows)

    def execute(self, sql, params):
        if sql.startswith("INSERT"):
            self.rows.append(params)
        elif sql.startswith("DELETE"):
            self.rows = [r for r in self.rows if r[0] >= params[0]]

    def fetch_one(self, _sql, params):
        older = [r for r in self.rows if r[0] <= params[0]]
        return (max(older)[1],) if older else None


def test_yesterdays_snapshot_counts_even_if_a_few_seconds_short_of_24h(monkeypatch):
    now = int(time.time())
    db = _Snapshots([(now - 86_395, 1_000)])        # yesterday's 00:05 post, 5s short of a day
    monkeypatch.setattr(es, "DatabaseManager", db)
    assert es._record_circulation(1_250) == 250
    assert db.rows[-1][1] == 1_250


def test_no_change_without_a_day_old_snapshot(monkeypatch):
    monkeypatch.setattr(es, "DatabaseManager", _Snapshots([]))
    assert es._record_circulation(500) is None


def _card(**over):
    s = es._summary({"1": 500, "2": 300})
    args = dict(bank=200, change24=None, series=[(date(2026, 10, 1), 800)], to_players=[], to_bank=[],
                payouts=[], richest=[("<b>Shouty</b>", None, 500)], date_label="1 October 2026")
    args.update(over)
    return es._build_html(s, **args)


def test_a_quiet_economy_still_renders_with_plain_empty_states():
    page = _card()
    assert "No 24h data yet" in page
    assert "Not enough history yet" in page
    assert "No money moved between the bank and players this week" in page
    assert "Nothing paid out yesterday" in page
    assert "THE 1,000 UKP SUPPLY" in page


def test_names_are_escaped_and_flows_drawn():
    page = _card(change24=-40, to_players=[("Rewards", 900)], to_bank=[("Tax", 500)],
                 series=[(date(2026, 9, 30), 700), (date(2026, 10, 1), 800)], payouts=[("Bumps", 400)])
    assert "&lt;b&gt;Shouty&lt;/b&gt;" in page and "<b>Shouty</b>" not in page
    assert "Players −40 in 24h" in page
    assert "PAID OUT 900" in page and "TAKEN BACK 500" in page
    assert "Players +400" in page                   # the week's net, 900 out less 500 back
    assert "Bumps <b class='num'>400</b>" in page
