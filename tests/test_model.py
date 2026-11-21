from dataclasses import replace
from datetime import date, datetime, time, timedelta
import unittest

from kontur.data import Bar, Event, MSK, synthetic_data
from kontur.model import FEATURE_NAMES, build_features, latest_forecast, walk_forward


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.days = [date(2024, 2, 1) + timedelta(days=offset) for offset in range(40)]
        self.bars = [
            Bar(symbol, day, 100, 102, 99, 101, 10_000)
            for day in self.days for symbol in ("SBER", "GAZP")
        ]

    def test_publication_before_close_can_still_arrive_too_late(self):
        day = self.days[24]
        published = datetime.combine(day, time(17), MSK)
        available = datetime.combine(day, time(19), MSK)
        event = Event("e1", "SBER", published, available, "Лента", "Автор", "Рост прибыли")
        rows = {(row.ticker, row.signal_day): row for row in build_features(self.bars, [event])}

        self.assertEqual(rows["SBER", day].evidence, ())
        acquired = rows["SBER", self.days[25]].evidence[0]
        self.assertEqual((acquired.event_id, acquired.category, acquired.sentiment), ("e1", "earnings", 1))
        self.assertEqual(acquired.available_at, available)
        self.assertEqual(rows["GAZP", self.days[25]].evidence, ())

    def test_missing_symbol_bars_do_not_shift_the_target_to_a_later_session(self):
        bars = [bar for bar in self.bars if not (bar.symbol == "SBER" and bar.day == self.days[25])]
        row = next(row for row in build_features(bars) if row.ticker == "SBER" and row.signal_day == self.days[24])

        self.assertEqual(row.trade_day, self.days[25])
        self.assertIsNone(row.target)

    def test_repeated_headlines_do_not_outvote_an_independent_source(self):
        day = self.days[24]
        before_close = datetime.combine(day, time(17), MSK)
        positive = Event("e1", "SBER", before_close, before_close, "Источник 1", "Иван", "Рост прибыли")
        duplicate = replace(positive, event_id="e2", source="ИСТОЧНИК  1", author="иван")
        negative = replace(positive, event_id="e3", source="Источник 2", author="Анна", title="Убыток")
        rows = build_features(self.bars, [positive, duplicate, negative])
        row = next(row for row in rows if row.ticker == "SBER" and row.signal_day == day)
        features = dict(zip(FEATURE_NAMES, row.values))

        self.assertEqual(features["fresh_count"], 3)
        self.assertEqual(features["fresh_source_count"], 2)
        self.assertEqual(features["fresh_author_count"], 2)
        self.assertEqual(features["source_consensus"], 0)
        self.assertEqual(features["author_consensus"], 0)

    def test_fold_training_uses_only_finalized_dates_and_latest_is_separate(self):
        bars, events = synthetic_data(sessions=84)
        rows = build_features(bars, events)
        result = walk_forward(rows, min_train_days=30, test_days=7, holdout_days=18)

        self.assertEqual(len(result.predictions), 18 * 4)
        self.assertEqual(len(result.folds), 3)
        for fold in result.folds:
            self.assertLess(fold.train_label_end, fold.test_start)
        self.assertEqual(result.diagnostics["event"]["samples"], 18 * 4)
        latest = latest_forecast(rows, min_train_days=30)
        self.assertEqual(len(latest), 4)
        self.assertGreater(latest[0].signal_day, max(item.signal_day for item in result.predictions))


if __name__ == "__main__":
    unittest.main()
