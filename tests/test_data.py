from datetime import date, time, timedelta
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from kontur.data import (
    ISS_CLOSE,
    download_moex,
    load_bars,
    load_events,
    synthetic_data,
    write_bars,
    write_events,
)


class DataTests(unittest.TestCase):
    def test_iss_paginates_raw_rows_including_days_without_trades(self):
        calls = []
        columns = ["SECID", "TRADEDATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"]
        rows = [
            ["SBER", "2024-01-08", None, None, None, None, 0],
            ["SBER", "2024-01-09", 270, 276, 269, 275, 35_000],
            ["SBER", "2024-01-10", 275, 279, 273, 278, 54_000],
        ]

        def open_iss(request, *, timeout):
            url = urlsplit(request.full_url)
            self.assertEqual(url.hostname, "iss.moex.com")
            self.assertTrue(url.path.endswith("/TQBR/securities/SBER.json"))
            params = parse_qs(url.query)
            self.assertEqual(params["from"], ["2024-01-08"])
            self.assertEqual(params["till"], ["2024-01-10"])
            offset = int(params["start"][0])
            calls.append(offset)
            payload = {
                "history": {"columns": columns, "data": rows[offset:offset + 2]},
                "history.cursor": {
                    "columns": ["INDEX", "TOTAL", "PAGESIZE"],
                    "data": [[offset, len(rows), 2]],
                },
            }
            return BytesIO(json.dumps(payload).encode())

        with patch("kontur.data.urlopen", side_effect=open_iss):
            bars = download_moex(["SBER", "sber"], date(2024, 1, 8), date(2024, 1, 10))

        self.assertEqual(calls, [0, 2])
        self.assertEqual([bar.day for bar in bars], [date(2024, 1, 9), date(2024, 1, 10)])
        self.assertEqual(bars[-1].close, 278)
        self.assertEqual(bars[-1].volume, 54_000)
        self.assertEqual(bars[-1].close_time, ISS_CLOSE)

    def test_events_convert_to_moscow_and_reject_impossible_receipts(self):
        header = "event_id,symbol,published_at,available_at,source,title\n"
        published = "2024-01-08T12:00:00Z"
        with TemporaryDirectory() as directory:
            path = Path(directory) / "events.csv"
            path.write_text(
                header + f"e1,*,{published},2024-01-08T15:15:00+03:00,Источник,Событие\n",
                encoding="utf-8-sig",
            )
            event, = load_events(path)
            self.assertEqual(event.symbol, "*")
            self.assertEqual(event.published_at.hour, 15)
            self.assertEqual(event.available_at - event.published_at, timedelta(minutes=15))

            for stamp in ("2024-01-08T14:59:59+03:00", "2024-01-08T15:15:00"):
                with self.subTest(stamp=stamp):
                    path.write_text(
                        header + f"e1,*,{published},{stamp},Источник,Событие\n", encoding="utf-8"
                    )
                    with self.assertRaisesRegex(ValueError, r"events\.csv:2"):
                        load_events(path)

    def test_bars_respect_session_override_and_reject_duplicate_sessions(self):
        start = "symbol,date,open,high,low,close,volume,close_time\n"
        row = "SBER,2024-01-08,100,105,95,103,1200,19:30\n"
        with TemporaryDirectory() as directory:
            path = Path(directory) / "bars.csv"
            path.write_text(start + row, encoding="utf-8")
            bar, = load_bars(path, open_time=time(7))
            self.assertEqual(bar.open_at.isoformat(), "2024-01-08T07:00:00+03:00")
            self.assertEqual(bar.close_at.isoformat(), "2024-01-08T19:30:00+03:00")
            path.write_text(start + row + row, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, r"bars\.csv:3"):
                load_bars(path)

    def test_synthetic_roundtrip_keeps_receipts_and_prices(self):
        bars, events = synthetic_data(seed=21, sessions=100)
        self.assertTrue(events)
        self.assertEqual((bars, events), synthetic_data(seed=21, sessions=100))
        with TemporaryDirectory() as directory:
            bars_path, events_path = Path(directory) / "bars.csv", Path(directory) / "events.csv"
            write_bars(bars_path, bars)
            write_events(events_path, events)
            self.assertEqual(load_bars(bars_path), bars)
            self.assertEqual(load_events(events_path), events)
