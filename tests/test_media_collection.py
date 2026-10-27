"""Stage 2 collection, normalization, mirror, and health tests."""
from __future__ import annotations

import copy
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from media_collect import (  # noqa: E402
    CollectionFailure,
    FetchResult,
    assign_mirror_groups,
    collect_registry,
    parse_bluesky,
    parse_feed,
)
from media_validate import (  # noqa: E402
    MediaValidationError,
    validate_collection_batch,
    validate_collection_health,
)


NOW = datetime(2026, 10, 14, 15, 0, 0, tzinfo=timezone.utc)


def rss(*items: tuple[str, str, str, str]) -> bytes:
    body = "".join(
        f"<item><title>{title}</title><link>{url}</link><guid>{guid}</guid>"
        f"<pubDate>{published}</pubDate><description>Unretained body</description></item>"
        for title, url, guid, published in items
    )
    return f"<?xml version='1.0'?><rss><channel>{body}</channel></rss>".encode()


class MediaCollectionTests(unittest.TestCase):
    def setUp(self):
        self.registry = {
            "document_type": "media-observer-registry",
            "schema_version": 1,
            "updated_at": "2026-10-12T08:05:00Z",
            "brand_families": [
                {"id": "brand-atlas", "display_name": "Synthetic atlas family"},
                {"id": "brand-beacon", "display_name": "Synthetic beacon family"},
            ],
            "observers": [
                {
                    "id": "observer-atlas",
                    "display_name": "Synthetic atlas",
                    "brand_family_id": "brand-atlas",
                    "panel_status": "shadow",
                    "status_since": "2026-10-01",
                    "independence_basis": "Synthetic editorial product with two mirror channels.",
                    "channels": [
                        {
                            "id": "channel-atlas-web",
                            "medium": "website",
                            "collection_method": "feed",
                            "endpoint_url": "https://atlas.example/feed",
                            "public_url": "https://atlas.example/",
                            "enabled": True,
                        },
                        {
                            "id": "channel-atlas-video",
                            "medium": "youtube",
                            "collection_method": "feed",
                            "endpoint_url": "https://video.example/atlas-feed",
                            "public_url": "https://video.example/atlas",
                            "enabled": True,
                        },
                    ],
                },
                {
                    "id": "observer-beacon",
                    "display_name": "Synthetic beacon",
                    "brand_family_id": "brand-beacon",
                    "panel_status": "candidate",
                    "status_since": "2026-10-01",
                    "independence_basis": "Synthetic independent editorial product.",
                    "channels": [
                        {
                            "id": "channel-beacon",
                            "medium": "podcast",
                            "collection_method": "feed",
                            "endpoint_url": "https://beacon.example/feed",
                            "public_url": "https://beacon.example/",
                            "enabled": False,
                        }
                    ],
                },
            ],
        }
        self.payloads = {
            "https://atlas.example/feed": rss(
                ("Shared &amp; exact story", "https://atlas.example/story?utm_source=test", "atlas-1", "Wed, 14 Oct 2026 13:00:00 GMT"),
                ("Older item", "https://atlas.example/old", "atlas-old", "Wed, 01 Jul 2026 13:00:00 GMT"),
            ),
            "https://video.example/atlas-feed": rss(
                ("Shared &amp; exact story", "https://video.example/watch/1", "video-1", "Wed, 14 Oct 2026 14:00:00 GMT"),
            ),
        }

    def fetcher(self, url: str) -> FetchResult:
        if url not in self.payloads:
            raise CollectionFailure("http-error", "http-404", http_status=404)
        return FetchResult(self.payloads[url], url, 200, "application/rss+xml")

    def collect(self):
        return collect_registry(
            self.registry,
            lookback_days=35,
            workers=2,
            fetcher=self.fetcher,
            clock=lambda: NOW,
        )

    def test_collection_batch_and_health_validate_together(self):
        batch, health, _ = self.collect()
        validate_collection_batch(batch, self.registry)
        validate_collection_health(health, self.registry, batch)
        self.assertEqual(health["summary"]["retained_item_count"], 2)
        self.assertEqual(health["summary"]["mirror_group_count"], 1)

    def test_cross_platform_exact_title_is_one_mirror_group(self):
        batch, _, _ = self.collect()
        group = batch["mirror_groups"][0]
        self.assertEqual(group["match_basis"], "exact-title")
        self.assertEqual(len(group["item_ids"]), 2)
        self.assertEqual(
            next(item for item in batch["items"] if item["id"] == group["canonical_item_id"])["attention_at"],
            "2026-10-14T13:00:00Z",
        )

    def test_collector_does_not_retain_body_or_create_story_fields(self):
        batch, _, _ = self.collect()
        for item in batch["items"]:
            self.assertNotIn("description", item)
            self.assertNotIn("full_text", item)
            self.assertNotIn("story_id", item)
            self.assertNotIn("cluster_confidence", item)

    def test_tracking_parameters_are_removed(self):
        batch, _, _ = self.collect()
        web_item = next(item for item in batch["items"] if item["channel_id"] == "channel-atlas-web")
        self.assertEqual(web_item["url"], "https://atlas.example/story")

    def test_old_items_are_parsed_but_not_retained(self):
        _, health, _ = self.collect()
        web = next(row for row in health["channels"] if row["channel_id"] == "channel-atlas-web")
        self.assertEqual(web["item_count"], 2)
        self.assertEqual(web["qualifying_item_count"], 1)

    def test_disabled_channels_are_reported_without_attempt(self):
        _, health, _ = self.collect()
        beacon = next(row for row in health["channels"] if row["channel_id"] == "channel-beacon")
        self.assertEqual(beacon["status"], "disabled")
        self.assertIsNone(beacon["attempted_at"])

    def test_missing_publication_time_uses_stable_first_seen(self):
        self.payloads["https://atlas.example/feed"] = (
            b"<rss><channel><item><title>Undated</title><link>https://atlas.example/undated</link>"
            b"<guid>undated-1</guid></item></channel></rss>"
        )
        batch, _, state = self.collect()
        web_first = next(item for item in batch["items"] if item["channel_id"] == "channel-atlas-web")
        self.assertEqual(web_first["timestamp_basis"], "first-seen")
        later = datetime(2026, 10, 15, 15, 0, 0, tzinfo=timezone.utc)
        second, _, _ = collect_registry(
            self.registry,
            lookback_days=35,
            workers=1,
            first_seen=state,
            fetcher=self.fetcher,
            clock=lambda: later,
        )
        web = next(item for item in second["items"] if item["channel_id"] == "channel-atlas-web")
        self.assertEqual(web["attention_at"], "2026-10-14T15:00:00Z")

    def test_failed_feed_is_not_reported_as_empty(self):
        self.registry["observers"][1]["channels"][0]["enabled"] = True
        _, health, _ = self.collect()
        beacon = next(row for row in health["channels"] if row["channel_id"] == "channel-beacon")
        self.assertEqual(beacon["status"], "http-error")
        self.assertEqual(health["summary"]["failed_observer_ids"], ["observer-beacon"])

    def test_unsupported_method_is_explicit_failure(self):
        self.registry["observers"][0]["channels"][0]["collection_method"] = "proprietary-api"
        _, health, _ = self.collect()
        row = next(row for row in health["channels"] if row["channel_id"] == "channel-atlas-web")
        self.assertEqual(row["status"], "unsupported-method")

    def test_mirror_groups_cannot_cross_observers(self):
        batch, _, _ = self.collect()
        altered = copy.deepcopy(batch)
        altered["items"][1]["observer_id"] = "observer-beacon"
        with self.assertRaises(MediaValidationError):
            validate_collection_batch(altered, self.registry)

    def test_health_counts_are_recomputed(self):
        batch, health, _ = self.collect()
        health["summary"]["retained_item_count"] += 1
        with self.assertRaises(MediaValidationError):
            validate_collection_health(health, self.registry, batch)

    def test_health_newest_timestamp_is_recomputed(self):
        batch, health, _ = self.collect()
        row = next(item for item in health["channels"] if item["status"] == "ok")
        row["newest_attention_at"] = "2026-10-14T12:00:00Z"
        with self.assertRaises(MediaValidationError):
            validate_collection_health(health, self.registry, batch)

    def test_same_channel_repeated_title_is_not_mirror_collapsed(self):
        items = [
            {
                "id": f"item-repeat-{index}",
                "observer_id": "observer-atlas",
                "channel_id": "channel-atlas-web",
                "medium": "website",
                "normalized_title": "Recurring program title",
                "url": f"https://atlas.example/{index}",
                "external_id": f"repeat-{index}",
                "attention_at": f"2026-10-14T1{index}:00:00Z",
                "timestamp_basis": "published",
                "collected_at": "2026-10-14T15:00:00Z",
                "mirror_group_id": "pending-group",
            }
            for index in (2, 3)
        ]
        groups = assign_mirror_groups(items)
        self.assertEqual(len(groups), 2)

    def test_mirror_group_cannot_bridge_different_titles_and_urls(self):
        items = [
            {
                "id": f"item-{index}",
                "observer_id": "observer-sample",
                "channel_id": channel,
                "medium": "website",
                "normalized_title": title,
                "url": url,
                "external_id": f"external-{index}",
                "attention_at": f"2026-10-26T0{index}:00:00Z",
                "timestamp_basis": "published",
                "collected_at": "2026-10-26T04:00:00Z",
                "mirror_group_id": "pending-group",
            }
            for index, (channel, title, url) in enumerate(
                [
                    ("channel-a", "Shared title", "https://example.com/one"),
                    ("channel-b", "Shared title", "https://example.com/two"),
                    ("channel-c", "Different title", "https://example.com/two"),
                ]
            )
        ]
        groups = assign_mirror_groups(items)
        self.assertEqual(len(groups), 2)
        self.assertEqual(sorted(group["match_basis"] for group in groups), ["canonical-url", "singleton"])

    def test_feed_parser_accepts_atom_links(self):
        payload = b"""<feed xmlns='http://www.w3.org/2005/Atom'><entry><title>Atom title</title>
        <link rel='alternate' href='https://atlas.example/atom'/><id>tag:atlas,1</id>
        <published>2026-10-14T12:00:00Z</published></entry></feed>"""
        parsed = parse_feed(payload, "https://atlas.example/feed")
        self.assertEqual(parsed[0]["url"], "https://atlas.example/atom")

    def test_html_response_is_parse_failure_not_empty_feed(self):
        payload = b"<html><head><title>Not a feed</title></head><body></body></html>"
        with self.assertRaisesRegex(CollectionFailure, "unexpected-feed-root"):
            parse_feed(payload, "https://atlas.example/feed")

    def test_bluesky_parser_keeps_only_authored_posts(self):
        def wrapper(handle, key, *, reason=None):
            value = {
                "post": {
                    "uri": f"at://did:plc:atlas/app.bsky.feed.post/{key}",
                    "author": {
                        "handle": handle,
                        "did": "did:plc:atlas" if handle == "atlas.example" else "did:plc:other",
                    },
                    "record": {"text": f"Post {key}", "createdAt": "2026-10-14T12:00:00Z"},
                }
            }
            if reason is not None:
                value["reason"] = reason
            return value

        payload = json.dumps(
            {
                "feed": [
                    wrapper("atlas.example", "own"),
                    wrapper("other.example", "other"),
                    wrapper("atlas.example", "repost", reason={"by": {}}),
                ]
            }
        ).encode()
        parsed = parse_bluesky(payload, "atlas.example")
        self.assertEqual([item["external_id"] for item in parsed], ["at://did:plc:atlas/app.bsky.feed.post/own"])


if __name__ == "__main__":
    unittest.main()
