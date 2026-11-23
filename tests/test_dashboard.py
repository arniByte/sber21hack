from html.parser import HTMLParser
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from kontur.__main__ import standalone_dashboard


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.assets = []
        self.scripts = []
        self.embedded = False
        self.report = ""

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag in {"link", "script"} and ("src" in attributes or "href" in attributes):
            self.assets.append(attributes)
        if tag == "script":
            self.scripts.append(attributes)
            self.embedded = attributes.get("id") == "embedded-report"

    def handle_endtag(self, tag):
        if tag == "script":
            self.embedded = False

    def handle_data(self, data):
        if self.embedded:
            self.report += data


class DashboardTest(unittest.TestCase):
    def test_embeds_report_before_app_without_external_resources(self):
        with TemporaryDirectory() as folder:
            report = Path(folder) / "report.json"
            dashboard = Path(folder) / "dashboard.html"
            payload = {"title": "</script><script>не код</script>"}
            report.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            app = Path(folder) / "app.js"
            app.write_text("window.kontur = true", encoding="utf-8")
            with patch("kontur.__main__.WEB_BUNDLE", app):
                standalone_dashboard(dashboard, report)
            page = Page()
            page.feed(dashboard.read_text(encoding="utf-8"))
            self.assertFalse(page.assets)
            self.assertEqual(page.scripts, [{"id": "embedded-report", "type": "application/json"}, {}])
            self.assertEqual(json.loads(page.report), payload)
