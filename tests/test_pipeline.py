from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from collect import collect_scotus, feed_entries, make_item, person_matches
from build import item_local, render_item
from common import canonical_url, validate_people


class PipelineTests(unittest.TestCase):
    def test_feed_parser_handles_rss(self) -> None:
        payload = b"""<?xml version="1.0"?>
        <rss version="2.0"><channel><item>
        <title>A measured release</title>
        <link>https://example.gov/report?utm_source=x</link>
        <guid>report-1</guid>
        <pubDate>Tue, 28 Jul 2026 12:00:00 GMT</pubDate>
        <description><![CDATA[<p>Source summary.</p>]]></description>
        </item></channel></rss>"""
        entries = feed_entries(payload, "https://example.gov/")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["external_id"], "report-1")

    def test_tracking_parameters_are_removed(self) -> None:
        self.assertEqual(
            canonical_url("https://Example.gov/a?x=1&utm_source=y#part"),
            "https://example.gov/a?x=1",
        )

    def test_summary_date_is_used_only_when_source_authorizes_it(self) -> None:
        fetched_at = datetime(2026, 7, 29, 17, 0, tzinfo=timezone.utc)
        source = {
            "id": "ny-test",
            "name": "New York decisions",
            "publisher": "New York State Law Reporting Bureau",
            "evidence": "ADJUDGED",
            "channel": "courts",
            "geography": "New York",
            "date_from_summary": True,
        }
        item = make_item(
            source,
            {
                "title": "Example v Example",
                "url": "https://example.gov/decision",
                "summary": "decided July 23, 2026 No. 1",
            },
            fetched_at,
        )
        self.assertEqual(item["published"], "2026-07-23T00:00:00Z")
        self.assertEqual(item["published_precision"], "date")
        self.assertEqual(item_local(item).date().isoformat(), "2026-07-23")
        self.assertNotIn("PM", render_item(item))

    def test_people_match_requires_name_and_context(self) -> None:
        person = {
            "display_name": "Jane Example",
            "aliases": [],
            "distinctive_name": False,
            "require_any": ["economist"],
            "exclude_any": [],
        }
        self.assertTrue(
            person_matches(
                person,
                {"title": "Economist Jane Example publishes new paper", "summary": ""},
            )
        )
        self.assertFalse(
            person_matches(person, {"title": "Jane Example wins a race", "summary": ""})
        )

    def test_watchlist_rejects_duplicate_ids(self) -> None:
        registry = {
            "schema_version": 1,
            "people": [
                {"id": "same", "display_name": "One"},
                {"id": "same", "display_name": "Two"},
            ],
        }
        self.assertTrue(any("duplicates" in error for error in validate_people(registry)))


if __name__ == "__main__":
    unittest.main()
