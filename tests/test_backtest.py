from datetime import date
from types import SimpleNamespace
import unittest

from kontur.backtest import RiskConfig, simulate
from kontur.data import Bar


class BacktestTest(unittest.TestCase):
    def setUp(self):
        self.signal = Bar("SBER", date(2025, 1, 13), 100, 101, 99, 100, 100_000)
        self.fill = Bar("SBER", date(2025, 1, 14), 100, 103, 98, 101, 100_000)
        self.prediction = SimpleNamespace(
            ticker="SBER", signal_day=self.signal.day, trade_day=self.fill.day,
            signal_at=self.signal.close_at, event_return=.01, evidence=(), fold=1,
        )

    def replay(self, config=RiskConfig()):
        return simulate([self.signal, self.fill], [self.prediction], "event_return", config)

    def test_ambiguous_bar_uses_stop_and_applies_all_costs(self):
        report = self.replay()
        trade, = report["trades"]
        self.assertEqual(trade["reason"], "stop")
        self.assertIsNone(trade["exit_at"])
        self.assertEqual(trade["quantity"] % 10, 0)
        self.assertGreater(trade["entry_price"], 100)
        self.assertLess(trade["exit_price"], 99)
        self.assertGreater(trade["entry_fee"], 0)
        self.assertGreater(trade["exit_fee"], 0)
        self.assertAlmostEqual(report["metrics"]["net_pnl"], trade["net_pnl"])
        self.assertLessEqual(-trade["net_pnl"], 35_000 * .006)
        self.assertGreater(report["metrics"]["max_drawdown_pct"], 0)

    def test_rejects_signal_that_was_not_available_at_open(self):
        self.prediction.signal_at = self.fill.open_at
        with self.assertRaisesRegex(ValueError, "до открытия"):
            self.replay()
