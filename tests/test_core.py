from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path

from geofactory.core.config import ConfigurationError, load_config
from geofactory.core.database import Database
from geofactory.core.jobs import (
    approval_is_current, create_job, get_job, package_digest, record_approval,
    recover_interrupted, transition_job,
)
from geofactory.media.asset_manager import AssetRightsError, register_asset


class FactoryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = Database(self.root / "factory.sqlite3")
        self.db.initialize()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def create_job(self, topic: str = "Synthetic place") -> dict[str, object]:
        return create_job(
            self.db, topic, "What can geography explain?", self.root / "output",
            idempotency_key=f"test:{topic}",
        )


class ConfigTests(FactoryTestCase):
    def test_config_uses_local_safe_defaults(self) -> None:
        config = load_config(self.root / "missing.json")
        self.assertEqual(config.bind_host, "127.0.0.1")
        self.assertFalse(config.publishing_enabled)
        self.assertTrue(config.dry_run)

    def test_config_rejects_non_loopback_dashboard_and_publishing(self) -> None:
        path = self.root / "unsafe.json"
        path.write_text(json.dumps({"bind_host": "0.0.0.0"}), encoding="utf-8")
        with self.assertRaises(ConfigurationError):
            load_config(path)
        path.write_text(json.dumps({"publishing_enabled": True}), encoding="utf-8")
        with self.assertRaises(ConfigurationError):
            load_config(path)


class JobTests(FactoryTestCase):
    def test_schema_initialization_and_idempotent_creation(self) -> None:
        self.db.initialize()
        first = self.create_job()
        again = self.create_job()
        self.assertEqual(first["id"], again["id"])
        self.assertEqual(first["status"], "proposed")

    def test_invalid_transitions_are_rejected(self) -> None:
        job = self.create_job()
        with self.assertRaises(ValueError):
            transition_job(self.db, str(job["id"]), "published")

    def test_restart_requeues_processing_but_never_retries_ambiguous_upload(self) -> None:
        job = self.create_job()
        transition_job(self.db, str(job["id"]), "researching")
        self.assertEqual(recover_interrupted(self.db), [job["id"]])
        self.assertEqual(get_job(self.db, str(job["id"]))["status"], "proposed")

        transition_job(self.db, str(job["id"]), "researching")
        transition_job(self.db, str(job["id"]), "scripting")
        transition_job(self.db, str(job["id"]), "awaiting_review")
        transition_job(self.db, str(job["id"]), "rendering")
        transition_job(self.db, str(job["id"]), "awaiting_approval")
        transition_job(self.db, str(job["id"]), "approved")
        transition_job(self.db, str(job["id"]), "uploading")
        recover_interrupted(self.db)
        recovered = get_job(self.db, str(job["id"]))
        self.assertEqual(recovered["status"], "failed")
        self.assertIn("outcome is unknown", recovered["error"])

    def test_package_change_invalidates_approval(self) -> None:
        job = self.create_job()
        for state in ("researching", "scripting", "awaiting_review", "rendering", "awaiting_approval"):
            transition_job(self.db, str(job["id"]), state)
        package = Path(job["output_dir"])
        package.mkdir(parents=True)
        (package / "metadata.json").write_text('{"title":"before"}', encoding="utf-8")
        digest = package_digest(package)
        record_approval(self.db, str(job["id"]), "owner", "", digest)
        self.assertTrue(approval_is_current(self.db, str(job["id"]), package))
        (package / "metadata.json").write_text('{"title":"after"}', encoding="utf-8")
        self.assertFalse(approval_is_current(self.db, str(job["id"]), package))
        self.assertEqual(get_job(self.db, str(job["id"]))["status"], "awaiting_approval")


class RightsTests(FactoryTestCase):
    def test_rights_gate_rejects_unknown_licences_and_bad_media(self) -> None:
        job = self.create_job()
        path = self.root / "not-an-image.png"
        path.write_bytes(b"not an image")
        with self.assertRaises(AssetRightsError):
            register_asset(
                self.db, str(job["id"]), path, "https://example.invalid/item",
                "Synthetic", "unknown", "Synthetic", True,
            )
        with self.assertRaises(AssetRightsError):
            register_asset(
                self.db, str(job["id"]), path, "https://example.invalid/item",
                "Synthetic", "CC0-1.0", "Synthetic", True,
            )

    def test_rights_gate_registers_known_format_and_license(self) -> None:
        job = self.create_job()
        path = self.root / "sample.png"
        path.write_bytes(base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/pWQAAAAASUVORK5CYII="
        ))
        asset_id = register_asset(
            self.db, str(job["id"]), path, "https://example.invalid/synthetic",
            "Synthetic test fixture", "CC0-1.0", "Synthetic test fixture (not real media)", True,
        )
        self.assertTrue(asset_id.startswith("ast-"))


if __name__ == "__main__":
    unittest.main()
