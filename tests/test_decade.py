from datetime import date, timedelta
import json
import unittest
from unittest.mock import patch

from kontur.data import Bar
from kontur.research import dossier, messages, review
from kontur.series import extend_with_iss
from kontur.study import session_returns, target_summary, trade_attribution
from kontur.swing import PROFILES, Signal, forecasts, replay, shared_calendar


class DecadeTest(unittest.TestCase):
    def test_gap_stop_executes_at_open_and_no_same_day_reentry(self):
        d1, d2 = date(2022, 2, 24), date(2022, 3, 24)
        bars = [Bar("SBER", d1, 100, 102, 99, 101, 1000),
                Bar("SBER", d2, 90, 93, 88, 91, 1000)]
        days, _, book = shared_calendar(bars)
        result = replay(days, book, {day: [Signal("SBER", 1)] for day in days}, {"SBER": 1}, cost_bps=0)
        self.assertEqual(len(result["trades"]), 1)
        trade = result["trades"][0]
        self.assertEqual((trade["exit_day"], trade["exit_price"], trade["reason"]), (d2, 90, "gap"))
        self.assertEqual(trade["net_pnl"], -10 * trade["quantity"])

    def test_five_session_hold_and_reinvestment_change_the_next_size(self):
        start = date(2024, 1, 1)
        days = [start + timedelta(days=i) for i in range(10)]
        bars = [Bar("SBER", day, 10, 20, 10, 20 if i % 5 == 4 else 10, 1000)
                for i, day in enumerate(days)]
        days, _, book = shared_calendar(bars)
        args = (days, book, {day: [Signal("SBER", 1)] for day in days}, {"SBER": 1})
        compounded = replay(*args, cost_bps=0)
        fixed = replay(*args, cost_bps=0, reinvest=False)
        first, second = compounded["trades"]
        self.assertEqual((first["entry_day"], first["exit_day"]), (days[0], days[4]))
        self.assertEqual(second["entry_day"], days[5])
        self.assertGreater(second["quantity"], fixed["trades"][1]["quantity"])
        for result in (compounded, fixed):
            self.assertAlmostEqual(result["metrics"]["net_pnl"], sum(t["net_pnl"] for t in result["trades"]))

    def test_folds_score_each_session_once_and_purge_unresolved_targets(self):
        start = date(2016, 1, 1)
        bars = [Bar("SBER", start + timedelta(days=i), close*.99, close*1.01, close*.98, close, 1000)
                for i in range(430) if i != 370 for close in [10 + i/100]]
        days, symbols, book = shared_calendar(bars)
        plays, folds = forecasts(days, symbols, book)
        self.assertGreater(len(folds), 1)
        self.assertTrue(all(fold["train_label_end"] < fold["first_signal"] for fold in folds))
        self.assertEqual(list(plays["ridge"]), list(plays["trend"]))
        self.assertEqual(list(plays["ridge"])[-1], days[-1])

    def test_extension_uses_previous_close_and_preserves_cost_accounting(self):
        start = date(2024, 4, 1)
        days = [start + timedelta(days=i) for i in range(10)]
        bars = [Bar("SBER", day, 100 + i, 102 + i, 99.5 + i, 101 + i, 1000)
                for i, day in enumerate(days)]
        days, _, book = shared_calendar(bars)
        args = (days, book, {days[0]: [Signal("SBER", 1)]}, {"SBER": 1})
        baseline = replay(*args)
        extended = replay(*args, rules=PROFILES["extended"])
        self.assertEqual(baseline["trades"][0]["exit_day"], days[4])
        self.assertEqual(extended["trades"][0]["exit_day"], days[9])
        attribution = trade_attribution(extended["trades"])
        self.assertAlmostEqual(extended["metrics"]["net_pnl"],
                               attribution["gross_pnl"] - attribution["commission"] - attribution["slippage"])

    def test_daily_targets_use_capital_and_include_flat_sessions(self):
        values = session_returns([(None, 100, 0), (None, 102, 0), (None, 102, 0)], capital=100)
        summary = target_summary(values, (2, 3))
        self.assertEqual(summary["samples"], 3)
        self.assertEqual(summary["hits"], {"2": 1, "3": 0})

    def test_rotation_trails_prior_close_and_exits_before_later_low(self):
        monday = date(2024, 4, 1)
        days = [monday + timedelta(days=i) for i in (0, 1, 7)]
        bars = [Bar("SBER", days[0], 100, 115, 99, 110, 1000),
                Bar("SBER", days[1], 105, 117, 104.75, 112, 1000),
                Bar("SBER", days[2], 108, 111, 92, 108, 1000)]
        days, _, book = shared_calendar(bars)
        result = replay(days, book, {days[0]: [Signal("SBER", 1)], days[2]: []}, {"SBER": 1},
                        cost_bps=0, rules=PROFILES["rotation"])
        self.assertEqual(len(result["trades"]), 1)
        trade = result["trades"][0]
        self.assertEqual((trade["exit_day"], trade["exit_price"], trade["reason"]), (days[2], 108, "rotation"))

    def test_splice_preserves_archive_and_converts_only_new_volume(self):
        first = date(2026, 8, 24)
        days = [first + timedelta(days=i) for i in range(7)]
        archive = [Bar("SBER", day, 100, 105, 97, 102, 100) for day in days[:5]]
        official = [Bar("SBER", day, 101, 105, 98, 102, 1000) for day in days]
        stitched, stats = extend_with_iss(archive, official)
        self.assertEqual(stitched[:5], archive)
        self.assertEqual([bar.volume for bar in stitched[5:]], [100, 100])
        self.assertEqual(stats["new_sessions"], 2)
        series = official[:]
        series[4] = Bar("SBER", days[4], 101, 105, 98, 103, 1000)
        with self.assertRaisesRegex(ValueError, "расходятся"):
            extend_with_iss(archive, series)

    @patch("kontur.research.complete", side_effect=("Гипотеза", "Риск"))
    def test_independent_prompts_use_only_selected_summaries(self, send):
        summary = {"count": 1, "profitable": 0, "median_pct": -1, "max_drawdown_pct": 2,
                   "windows_30_trades": 0, "worst": {"return_pct": -1, "team_id": "private-team"}}
        strategy = {"annual_summary": summary, "rolling_summary": summary,
                    "horizons": [{"available": True, "return_pct": -1, "trades": ["raw-fill"]}]}
        raw = {"sample_start": "2016-01-01", "sample_end": "2026-01-01", "oos_start": "2016-01-10",
               "bars": 5, "symbols": ["SBER"], "bars_sha256": "fixture",
               "strategies": {"trend": strategy, "ridge": strategy}}
        context = dossier(raw)
        self.assertNotIn("private-team", json.dumps(context))
        self.assertNotIn("raw-fill", json.dumps(context))
        answers = review(context, ["hypothesis", "risk"])
        self.assertEqual(answers["risk"], {"status": "answered", "text": "Риск"})
        self.assertNotEqual(send.call_args_list[0].args[0][0], send.call_args_list[1].args[0][0])
        self.assertEqual(send.call_args_list[0].args[0][1], send.call_args_list[1].args[0][1])
        self.assertEqual(messages(context, "risk")[-1]["role"], "user")
