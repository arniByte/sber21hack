from datetime import date
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from kontur.data import synthetic_data
from kontur.horizons import experiment, months_before
from kontur.mishka import answer
from kontur.model import build_features, execution_targets, walk_forward
from kontur.movers import movers
from kontur.profiles import configuration


class ResearchTest(unittest.TestCase):
    def test_presets_round_trip_and_reject_undeclared_fields(self):
        profile, risk = configuration("contest", None, {"SBER": 10})
        with TemporaryDirectory() as folder:
            path = Path(folder) / "preset"
            path.write_text(json.dumps(profile))
            self.assertEqual(configuration("baseline", path, {"SBER": 1}), (profile, risk))
            profile["risk"]["max_position_pct"] = True
            path.write_text(json.dumps(profile))
            with self.assertRaises(ValueError):
                configuration("baseline", path, {})
            profile["token"] = "unused"
            path.write_text(json.dumps(profile))
            with self.assertRaises(ValueError):
                configuration("baseline", path, {})

    def test_exit_labels_use_next_bar_without_changing_features_or_unresolved_labels(self):
        bars, events = synthetic_data(seed=21, sessions=55)
        rows = build_features(bars, events)
        _, risk = configuration("payoff", None, {})
        revised = execution_targets(rows, bars, risk)
        self.assertEqual(len(rows), len(revised))
        self.assertTrue(any(row.target != updated.target for row, updated in zip(rows, revised)))
        for row, updated in zip(rows, revised):
            self.assertEqual(row.values, updated.values)
            self.assertEqual(row.signal_at, updated.signal_at)
            if row.target is None:
                self.assertIsNone(updated.target)
            else:
                self.assertGreaterEqual(updated.target, -0.010001)
                self.assertLessEqual(updated.target, 0.016001)

    def test_windows_cover_calendar_and_refuse_short_history(self):
        self.assertEqual(months_before(date(2024, 8, 31), 6), date(2024, 2, 29))
        bars, events = synthetic_data(seed=21, sessions=550)
        predictions = walk_forward(build_features(bars, events)).predictions
        _, risk = configuration("contest", None, {b.symbol: 1 for b in bars})
        windows = experiment(bars, predictions, "event_return", risk)
        self.assertEqual([w["available"] for w in windows], [True, True, False, False])
        for window in windows[:2]:
            self.assertEqual(window["metrics"]["initial_capital"], 35000)
            self.assertGreater(window["start"], window["after"])
            self.assertEqual(window["end"], predictions[-1].trade_day)
            self.assertGreater(window["metrics"]["trade_count"], 0)

    def test_market_snapshot_drops_stale_flat_and_missing_previous(self):
        a, b = date(2026, 9, 4), date(2026, 9, 7)
        bars = [SimpleNamespace(day=d, symbol=s, close=v) for d, s, v in [
            (a,"UP",10),(a,"DOWN",10),(a,"FLAT",10),(a,"STALE",10),
            (b,"UP",12),(b,"DOWN",9),(b,"FLAT",10),(b,"NEW",100)]]
        result = movers(bars)
        self.assertEqual(result["count"], 3)
        self.assertEqual([row["symbol"] for row in result["gainers"]], ["UP"])
        self.assertEqual([row["symbol"] for row in result["losers"]], ["DOWN"])

    def test_explanation_uses_selected_multiday_window_without_trades(self):
        from kontur.mishka import report_context

        latest = {"start": "2026-09-08", "end": "2026-11-20", "drawdown_pct": .79, "trades": ["private"]}
        history = {"months": 120, "available": True, "start": "2016-11-21", "end": "2026-11-20",
                   "drawdown_pct": 5.15, "trades": ["private"]}
        report = {"meta": {"dataset": "moex"}, "strategies": {},
                  "long_study": {"strategies": {"rotation": {"horizons": [history], "fresh": latest}}}}
        context = report_context(report, "rotation", "120")
        self.assertEqual(context["metrics"]["drawdown_pct"], 5.15)
        self.assertNotIn("trades", context["metrics"])
        self.assertEqual(report_context(report, "rotation", "recent")["period"][0], "2026-09-08")
        with self.assertRaises(ValueError):
            report_context(report, "rotation", "24")

    @patch.dict("os.environ", {"OPENROUTER_MODEL": "test/model", "OPENROUTER_API_KEY": "fixture-key"})
    def test_mishka_sends_only_aggregate_context(self):
        response = io.BytesIO(b'{"choices":[{"message":{"content":"ok"}}]}')
        with patch("kontur.mishka.urlopen", return_value=response) as send:
            report = {"meta": {"dataset": "csv", "oos_start": "2024-01-01", "oos_end": "2024-12-31"},
                      "strategies": {"market": {"metrics": {"net_pnl": 1}, "trades": ["private-trade"]}}}
            self.assertEqual(answer(report, "market", "Доходность?"), "ok")
            request = send.call_args.args[0]
            self.assertNotIn(b"private-trade", request.data)
            self.assertNotIn(b"fixture-key", request.data)
            self.assertEqual(request.full_url, "https://openrouter.ai/api/v1/chat/completions")
        with self.assertRaises(ValueError):
            answer(report, "missing", "test")
