import csv
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from kontur.contest import FILL_COLUMNS, MARK_COLUMNS, audit_executions, contest_scores, drawdown_score, profit_factor_score


def write(path, columns, rows):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(columns)
        writer.writerows(rows)


class ContestTest(unittest.TestCase):
    def test_ladder_and_simulation_not_scored_as_real(self):
        self.assertEqual([drawdown_score(value) for value in [2, 2.001, 4, 6, 8, 10, 10.01]],
                         [25, 20, 20, 15, 10, 5, 0])
        self.assertEqual([profit_factor_score(value) for value in [.99, 1, 1.19, 1.20, 1.49, 1.50]],
                         [0, 5, 5, 10, 10, 15])
        metrics = {"net_pnl": 350, "max_drawdown_pct": 1, "profit_factor": None,
                   "no_losses": True, "concentration_pct": 61}
        score = contest_scores(metrics, best_positive_return_pct=2)
        self.assertEqual(score["components"]["financial_result"], 20)
        self.assertEqual(score["components"]["profit_factor"], 15)
        self.assertEqual(score["components"]["repeatability"], 0)
        self.assertTrue(score["concentration_review"])
        self.assertIsNone(score["total"])
        metrics["origin"] = "execution_audit"
        score = contest_scores(metrics, actual_situations=2, actual_days=1, validated_holdout_situations=30)
        self.assertEqual(score["components"]["repeatability"], 10)

    def test_partial_fills_are_one_situation_and_marked_loss_counts(self):
        with TemporaryDirectory() as directory:
            fills = Path(directory) / "fills.csv"
            marks = Path(directory) / "marks.csv"
            timestamp = "2025-01-13T"
            write(fills, FILL_COLUMNS, [
                ("A", "1", "signal-1", timestamp + "10:00:00+03:00", "SBER", "BUY", 10, 100, 1, 0),
                ("A", "2", "signal-1", timestamp + "10:01:00+03:00", "SBER", "BUY", 10, 101, 1, 0),
                ("A", "3", "signal-1", timestamp + "18:40:00+03:00", "SBER", "SELL", 20, 105, 2, 0),
            ])
            mark_rows = [
                ("A", timestamp + "09:00:00+03:00", 35000, "{}"),
                ("A", timestamp + "11:00:00+03:00", 34948, '{"SBER":{"quantity":20,"mark_price":98}}'),
                ("A", timestamp + "18:50:00+03:00", 35086, "{}"),
            ]
            write(marks, MARK_COLUMNS, mark_rows)
            audit = audit_executions(fills, marks)
            self.assertEqual(audit["metrics"]["trade_count"], 1)
            self.assertEqual(audit["metrics"]["net_pnl"], 86)
            self.assertAlmostEqual(audit["metrics"]["max_drawdown_pct"], 52 / 35000 * 100)
            mark_rows[1] = ("A", timestamp + "11:00:00+03:00", 35000, '{"SBER":{"quantity":20,"mark_price":98}}')
            write(marks, MARK_COLUMNS, mark_rows)
            with self.assertRaisesRegex(ValueError, "не совпадает"):
                audit_executions(fills, marks)
