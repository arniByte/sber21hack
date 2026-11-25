from datetime import date
import unittest

from kontur.intraday import Candle, Setup, exit_at, fills, limit_filled, limit_retest, retest, simulate


class IntradayTest(unittest.TestCase):
    def test_short_both_touched_puts_stop_first(self):
        bar = Candle(100, 103, 97, 99)
        self.assertEqual(exit_at(bar, Setup(-1, 101, 98, 1, 2)), (101, "stop", 101))
        self.assertEqual(exit_at(Candle(104, 106, 97, 99), Setup(-1, 101, 98, 1, 2)),
                         (104, "gap", 104))

    def test_retest_uses_closed_bars_and_forces_exit_at_cutoff_open(self):
        quote = Candle(104, 104.2, 103.9, 104)
        bars = {h: quote for h in range(10, 19)}
        bars.update({
            10: Candle(101, 103, 100, 102),
            11: Candle(103, 103.8, 102.9, 103.4),
            12: Candle(103.4, 104, 102.3, 103.3),
            13: Candle(103.3, 104, 103, 104),
            18: Candle(104, 108, 101, 107),
        })
        day = date(2026, 7, 1)
        result = simulate({day: {"SBER": bars}}, entry=retest)
        self.assertEqual(result["metrics"]["trade_count"], 1)
        trade = result["trades"][0]
        self.assertEqual((trade["side"], trade["reason"], trade["exit_hour_msk"]), ("LONG", "cutoff", 18))
        self.assertTrue(trade["entry_at"].startswith(str(day) + "T13"))
        self.assertTrue(trade["exit_at"].startswith(str(day) + "T18"))
        self.assertAlmostEqual(trade["exit_price"], 104 * .9995)
        self.assertLessEqual(trade["planned_risk"], 35000 * .003)
        self.assertLessEqual(trade["notional_multiple"], 1)

    def test_limit_touch_cancels_and_fill_bar_cannot_hit_target(self):
        quote = Candle(104, 104.2, 103.9, 104)
        bars = {h: quote for h in range(10, 19)}
        bars.update({
            10: Candle(101, 103, 100, 102),
            11: Candle(103, 103.8, 102.9, 103.4),
            12: Candle(103.4, 104, 102.15, 103.3),
            13: Candle(103.3, 108, 103, 104),
        })
        setup = limit_retest(bars, 13, .0005)
        self.assertEqual(setup.limit, 103)
        self.assertFalse(limit_filled(bars[13], setup))
        day = date(2026, 7, 1)
        cancelled = simulate({day: {"SBER": bars}}, entry=limit_retest)
        self.assertEqual(cancelled["orders"], 1)
        self.assertEqual(cancelled["metrics"]["trade_count"], 0)
        bars[13] = Candle(103.3, 108, 102.8, 104)
        filled = simulate({day: {"SBER": bars}}, entry=limit_retest)
        trade = filled["trades"][0]
        self.assertEqual((trade["limit"], trade["entry_window"], trade["reason"]), (103, "hour", "cutoff"))
        self.assertAlmostEqual(trade["entry_price"], 103 * 1.0005)
        stop = exit_at(Candle(103.3, 108, 101, 104), setup, fill_bar=True)
        self.assertEqual(stop[1], "stop")

    def test_short_limit_requires_strict_pass_through(self):
        short = Setup(-1, 101, 98, 1, 2, limit=100)
        self.assertFalse(limit_filled(Candle(99, 100, 98, 99), short))
        self.assertTrue(limit_filled(Candle(99, 100.1, 98, 99), short))

    def test_long_and_short_costs_are_conservative(self):
        for side, exit in ((1, 102), (-1, 98)):
            entry, filled, net, fees = fills(side, 100, exit, .0005)
            slip = .0005 * (100 + exit)
            self.assertLess(net, 2)
            self.assertAlmostEqual(net, 2 - fees - slip)
