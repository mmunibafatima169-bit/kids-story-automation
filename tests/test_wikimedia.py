from __future__ import annotations

import base64
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from geofactory.agents.research_agent import discover_wikipedia_sources
from geofactory.core.database import Database, SCHEMA_VERSION
from geofactory.core.jobs import create_job
from geofactory.media.asset_manager import (
    _verified_commons_license, search_and_download_commons,
)
from geofactory.wikimedia import HttpPayload, WikimediaError, request_bytes

PNG_FIXTURE = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/pWQAAAAASUVORK5CYII="
)


class WikimediaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = Database(self.root / "factory.sqlite3")
        self.db.initialize()
        self.job = create_job(
            self.db, "Venice lagoon", "How is Venice connected to its lagoon?",
            self.root / "output", idempotency_key="test-wikimedia",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_wikipedia_discovery_persists_api_records_and_text_license(self) -> None:
        api_response = {
            "query": {
                "pages": [{
                    "pageid": 123,
                    "title": "Venice",
                    "fullurl": "https://en.wikipedia.org/wiki/Venice",
                    "extract": "Synthetic test extract; not displayed as a verified claim.",
                }]
            }
        }
        with patch(
            "geofactory.agents.research_agent.request_json",
            return_value=api_response,
        ) as request:
            sources = discover_wikipedia_sources(
                self.db, str(self.job["id"]), "Venice geography", limit=2
            )
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["licence"], "CC BY-SA 4.0")
        self.assertEqual(sources[0]["publisher"], "English Wikipedia")
        self.assertEqual(sources[0]["id"], f"wiki-123-{self.job['id']}")
        request.assert_called_once()
        # A page may be sourced by multiple production jobs; IDs are job-scoped.
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT title, url, publisher, accessed_at, licence, licence_url "
                "FROM sources WHERE id = ?",
                (sources[0]["id"],),
            ).fetchone()
        self.assertEqual(row["title"], "Venice")
        self.assertEqual(row["licence"], "CC BY-SA 4.0")
        self.assertEqual(row["licence_url"], "https://creativecommons.org/licenses/by-sa/4.0/")
        other_job = create_job(
            self.db, "Another Venice job", "A different question?",
            self.root / "output", idempotency_key="test-wikimedia-other",
        )
        with patch(
            "geofactory.agents.research_agent.request_json",
            return_value=api_response,
        ):
            other_sources = discover_wikipedia_sources(
                self.db, str(other_job["id"]), "Venice", limit=1
            )
        self.assertNotEqual(sources[0]["id"], other_sources[0]["id"])

    def test_commons_download_verifies_license_and_records_attribution(self) -> None:
        commons_response = {
            "query": {
                "pages": [{
                    "title": "File:Venice lagoon synthetic fixture.png",
                    "imageinfo": [{
                        "descriptionurl": "https://commons.wikimedia.org/wiki/File:Venice_lagoon_synthetic_fixture.png",
                        "thumburl": "https://thumb.wikimedia.org/wikipedia/commons/thumb/test.png",
                        "thumbsize": len(PNG_FIXTURE),
                        "extmetadata": {
                            "LicenseShortName": {"value": "CC BY-SA 4.0"},
                            "LicenseUrl": {
                                "value": "https://creativecommons.org/licenses/by-sa/4.0/"
                            },
                            "Artist": {"value": "<a href='https://example.invalid'>Test creator</a>"},
                        },
                    }],
                }]
            }
        }
        with (
            patch(
                "geofactory.media.asset_manager.request_json",
                return_value=commons_response,
            ),
            patch(
                "geofactory.media.asset_manager.request_bytes",
                return_value=HttpPayload(
                    body=PNG_FIXTURE,
                    content_type="image/png",
                    final_url="https://thumb.wikimedia.org/wikipedia/commons/thumb/test.png",
                ),
            ),
        ):
            result = search_and_download_commons(
                self.db, str(self.job["id"]), "Venice lagoon", self.root / "assets"
            )
        self.assertEqual(len(result["assets"]), 1)
        self.assertEqual(result["rejected"], [])
        asset = result["assets"][0]
        self.assertEqual(asset["licence"], "CC-BY-SA-4.0")
        self.assertEqual(asset["title"], "Venice lagoon synthetic fixture.png")
        self.assertEqual(asset["publisher"], "Wikimedia Commons")
        self.assertIn("Test creator", asset["attribution"])
        self.assertTrue(Path(asset["path"]).is_file())
        self.assertEqual(asset["licence_url"], "https://creativecommons.org/licenses/by-sa/4.0/")

    def test_commons_download_limit_applies_to_reused_assets(self) -> None:
        commons_response = {
            "query": {
                "pages": [
                    {
                        "title": f"File:Fixture {index}.png",
                        "imageinfo": [{
                            "descriptionurl": f"https://commons.wikimedia.org/wiki/File:Fixture_{index}.png",
                            "thumburl": f"https://thumb.wikimedia.org/wikipedia/commons/thumb/{index}.png",
                            "extmetadata": {
                                "LicenseShortName": {"value": "CC BY 4.0"},
                                "LicenseUrl": {
                                    "value": "https://creativecommons.org/licenses/by/4.0/"
                                },
                                "Artist": {"value": "Test creator"},
                            },
                        }],
                    }
                    for index in (1, 2)
                ]
            }
        }
        payload = HttpPayload(
            body=PNG_FIXTURE,
            content_type="image/png",
            final_url="https://thumb.wikimedia.org/wikipedia/commons/thumb/test.png",
        )
        with (
            patch("geofactory.media.asset_manager.request_json", return_value=commons_response),
            patch("geofactory.media.asset_manager.request_bytes", return_value=payload),
        ):
            first = search_and_download_commons(
                self.db, str(self.job["id"]), "test", self.root / "assets", max_downloads=1
            )
        self.assertEqual(len(first["assets"]), 1)
        with (
            patch("geofactory.media.asset_manager.request_json", return_value=commons_response),
            patch("geofactory.media.asset_manager.request_bytes", return_value=payload),
        ):
            repeated = search_and_download_commons(
                self.db, str(self.job["id"]), "test", self.root / "assets", max_downloads=1
            )
        self.assertEqual(len(repeated["assets"]), 1)

    def test_commons_noncommercial_asset_is_rejected_without_download(self) -> None:
        commons_response = {
            "query": {
                "pages": [{
                    "title": "File:Noncommercial image.jpg",
                    "imageinfo": [{
                        "descriptionurl": "https://commons.wikimedia.org/wiki/File:Noncommercial_image.jpg",
                        "thumburl": "https://upload.wikimedia.org/wikipedia/commons/thumb/test.jpg",
                        "extmetadata": {
                            "LicenseShortName": {"value": "CC BY-NC 4.0"},
                            "LicenseUrl": {
                                "value": "https://creativecommons.org/licenses/by-nc/4.0/"
                            },
                        },
                    }],
                }]
            }
        }
        with (
            patch("geofactory.media.asset_manager.request_json", return_value=commons_response),
            patch("geofactory.media.asset_manager.request_bytes") as download,
        ):
            result = search_and_download_commons(
                self.db, str(self.job["id"]), "test image", self.root / "assets"
            )
        self.assertEqual(result["assets"], [])
        self.assertIn("not approved", result["rejected"][0]["reason"])
        download.assert_not_called()

    def test_commons_cc0_http_deed_url_is_canonicalized_safely(self) -> None:
        licence, licence_url = _verified_commons_license(
            {
                "extmetadata": {
                    "LicenseShortName": {"value": "CC0"},
                    "LicenseUrl": {
                        "value": "http://creativecommons.org/publicdomain/zero/1.0/deed.en"
                    },
                }
            }
        )
        self.assertEqual(licence, "CC0-1.0")
        self.assertEqual(licence_url, "https://creativecommons.org/publicdomain/zero/1.0/")

    def test_wikimedia_fetch_rejects_non_wikimedia_host(self) -> None:
        with self.assertRaises(WikimediaError):
            request_bytes("https://example.invalid/file.png", media_download=True)

    def test_schema_version_one_migrates_source_and_asset_licence_columns(self) -> None:
        path = self.root / "legacy.sqlite3"
        conn = sqlite3.connect(path)
        conn.executescript(
            """
            CREATE TABLE sources (
                id TEXT PRIMARY KEY, job_id TEXT NOT NULL, title TEXT NOT NULL,
                url TEXT NOT NULL, publisher TEXT NOT NULL, accessed_at TEXT NOT NULL,
                published_at TEXT NOT NULL DEFAULT '', evidence TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE assets (
                id TEXT PRIMARY KEY, job_id TEXT NOT NULL, path TEXT NOT NULL,
                origin_url TEXT NOT NULL, creator TEXT NOT NULL, licence TEXT NOT NULL,
                attribution TEXT NOT NULL, commercial_use INTEGER NOT NULL,
                file_sha256 TEXT NOT NULL, retrieved_at TEXT NOT NULL
            );
            PRAGMA user_version = 1;
            """
        )
        conn.close()
        migrated = Database(path)
        migrated.initialize()
        self.assertEqual(SCHEMA_VERSION, 3)
        with migrated.connect() as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
            source_columns = {row["name"] for row in conn.execute("PRAGMA table_info(sources)")}
            asset_columns = {row["name"] for row in conn.execute("PRAGMA table_info(assets)")}
        self.assertTrue({"licence", "licence_url"}.issubset(source_columns))
        self.assertTrue({"title", "publisher", "licence_url"}.issubset(asset_columns))


if __name__ == "__main__":
    unittest.main()
