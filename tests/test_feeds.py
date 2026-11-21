from datetime import datetime, timedelta
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from kontur.data import Event, MSK, load_events, write_events
from kontur.feeds import collect_feeds


RSS = b"""<rss version="2.0"><channel>
  <item><guid>ticker-42</guid><title>Profit grew</title>
    <pubDate>Mon, 08 Jan 2024 12:00:00 GMT</pubDate>
    <author>Publisher</author><description>&lt;p&gt;Quarterly report.&lt;/p&gt;</description>
    <link>https://news.example.com/report/42</link>
  </item>
  <item><guid>missing-date</guid><title>No timestamp</title></item>
  <item><guid>future</guid><title>Bad timestamp</title>
    <pubDate>Tue, 09 Jan 2024 18:00:00 GMT</pubDate>
  </item>
</channel></rss>"""
ATOM = b"""<feed xmlns="http://www.w3.org/2005/Atom">
  <entry><id>issuer-43</id><title>Dividend update</title>
    <published>2024-01-08T12:00:00Z</published>
    <author><name>Issuer press office</name></author>
    <summary>New board decision.</summary>
    <link href="/report/43" rel="alternate"/>
  </entry>
</feed>"""


class FeedsTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.output = Path(directory.name) / "events.csv"
        self.config = Path(directory.name) / "feeds.json"
        self.write_config("https://news.example.com/rss.xml", "SBER")
        self.now = datetime(2024, 1, 9, 11, 30, tzinfo=MSK)

    def write_config(self, url, symbol):
        self.config.write_text(json.dumps([{"url": url, "symbol": symbol, "source": "Example wire"}]))

    def test_rss_preserves_manual_rows_and_first_seen_time(self):
        prior = self.now - timedelta(days=2)
        manual = Event("manual-1", "SBER", prior, prior, "Manual", title="Manual entry")
        write_events(self.output, [manual])
        changed = RSS.replace(b"Profit grew", b"Profit grew (corrected)")
        with patch("kontur.feeds._fetch", side_effect=[RSS, changed]):
            self.assertEqual(collect_feeds(self.config, self.output, now=self.now), 1)
            self.assertEqual(collect_feeds(
                self.config, self.output, now=self.now + timedelta(hours=2)
            ), 0)

        manual_row, captured = load_events(self.output)
        self.assertEqual(manual_row, manual)
        self.assertEqual(captured.title, "Profit grew")
        self.assertEqual(captured.text, "Quarterly report.")
        self.assertEqual(captured.author, "Publisher")
        self.assertEqual(captured.url, "https://news.example.com/report/42")
        self.assertEqual(captured.available_at, self.now)
        self.assertEqual(captured.published_at, datetime(2024, 1, 8, 15, tzinfo=MSK))

    def test_atom_collects_market_wide_items(self):
        self.write_config("https://news.example.com/atom.xml", "*")
        with patch("kontur.feeds._fetch", return_value=ATOM):
            self.assertEqual(collect_feeds(self.config, self.output, now=self.now), 1)
        captured, = load_events(self.output)
        self.assertEqual(captured.symbol, "*")
        self.assertEqual(captured.author, "Issuer press office")
        self.assertEqual(captured.url, "https://news.example.com/report/43")

    def test_bad_source_rejected_before_fetch(self):
        for url in ("http://news.example.com/rss.xml", "https://127.0.0.1/rss.xml"):
            with self.subTest(url=url):
                self.write_config(url, "SBER")
                with patch("kontur.feeds._fetch") as fetch:
                    with self.assertRaises(ValueError):
                        collect_feeds(self.config, self.output, now=self.now)
                    fetch.assert_not_called()

    def test_dtd_does_not_modify_existing_archive(self):
        write_events(self.output, [])
        original = self.output.read_bytes()
        with patch("kontur.feeds._fetch", return_value=b"<!DOCTYPE rss><rss/>"):
            with self.assertRaisesRegex(ValueError, "DTD"):
                collect_feeds(self.config, self.output, now=self.now)
        self.assertEqual(self.output.read_bytes(), original)
