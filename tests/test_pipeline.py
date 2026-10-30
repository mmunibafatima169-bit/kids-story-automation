from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from geofactory.agents.research_agent import ResearchError, import_ledger
from geofactory.agents.script_agent import generate_evidence_script
from geofactory.core.config import FactoryConfig
from geofactory.core.database import Database
from geofactory.core.jobs import create_job, get_job
from geofactory.media.tts_engine import write_estimated_srt
from geofactory.quality.qa_engine import run_qa, validate_srt
from geofactory.scheduler.worker import process_offline_job, recover_and_process


class PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = Database(self.root / "data.sqlite3")
        self.db.initialize()
        self.config = FactoryConfig(
            root=self.root,
            database_path=self.root / "data.sqlite3",
            output_dir=self.root / "output",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_offline_demo_generates_package_and_blocks_approval(self) -> None:
        job = create_job(
            self.db, "Synthetic river towns", "How is this fictional region organized?",
            self.config.output_dir,
        )
        report = process_offline_job(self.db, self.config, job["id"])
        package = Path(job["output_dir"])
        self.assertTrue((package / "script.md").is_file())
        self.assertTrue((package / "source_ledger.json").is_file())
        self.assertTrue((package / "asset_ledger.json").is_file())
        self.assertTrue((package / "metadata.json").is_file())
        self.assertTrue((package / "qa_report.md").is_file())
        self.assertTrue(report["approval_blocked"])
        self.assertEqual(get_job(self.db, job["id"])["status"], "qa_failed")

    def test_worker_processes_a_queued_job_sequentially(self) -> None:
        job = create_job(
            self.db, "Synthetic queued job", "What does this queue do?",
            self.config.output_dir,
        )
        self.assertEqual(recover_and_process(self.db, self.config, max_jobs=1), [job["id"]])
        self.assertEqual(get_job(self.db, job["id"])["status"], "qa_failed")

    def test_research_ledger_requires_traceable_fact_sources(self) -> None:
        job = create_job(
            self.db, "Synthetic test topic", "A test question?", self.config.output_dir
        )
        valid = self.root / "ledger.json"
        valid.write_text(json.dumps({
            "sources": [{
                "id": "test-source", "title": "Synthetic fixture",
                "url": "https://example.invalid/synthetic-fixture",
                "publisher": "Synthetic test fixture — not a real publisher",
                "evidence": "Synthetic-only test datum: sample value is 7.",
            }],
            "claims": [{
                "id": "test-claim", "text": "Synthetic-only sample value is 7.",
                "kind": "fact", "source_ids": ["test-source"],
            }],
        }), encoding="utf-8")
        self.assertEqual(import_ledger(self.db, job["id"], valid), {"sources": 1, "claims": 1})
        self.assertEqual(import_ledger(self.db, job["id"], valid), {"sources": 1, "claims": 1})

        other_job = create_job(
            self.db, "Another synthetic topic", "Another question?", self.config.output_dir
        )
        with self.assertRaises(ResearchError):
            import_ledger(self.db, other_job["id"], valid)
        with self.db.connect() as conn:
            owner = conn.execute(
                "SELECT job_id FROM sources WHERE id = 'test-source'"
            ).fetchone()
        self.assertEqual(owner["job_id"], job["id"])

        invalid = self.root / "invalid.json"
        invalid.write_text(json.dumps({
            "sources": [],
            "claims": [{"text": "Unsupported", "kind": "fact", "source_ids": []}],
        }), encoding="utf-8")
        with self.assertRaises(ResearchError):
            import_ledger(self.db, job["id"], invalid)

    def test_subtitle_generation_and_validation(self) -> None:
        path = self.root / "captions.srt"
        write_estimated_srt("One short line. Another short line.", path)
        self.assertEqual(validate_srt(path), [])
        path.write_text("1\n99:99:99,000 --> 00:00:00,000\nBad\n", encoding="utf-8")
        self.assertTrue(validate_srt(path))

    def test_local_script_draft_requires_word_count_and_known_claim_ids(self) -> None:
        job = create_job(
            self.db, "Synthetic test topic", "How does the fictional place work?",
            self.config.output_dir,
        )
        ledger = self.root / "script-ledger.json"
        ledger.write_text(json.dumps({
            "sources": [{
                "id": "src-1", "title": "Synthetic source",
                "url": "https://example.invalid/synthetic-source",
                "publisher": "Synthetic test fixture",
                "evidence": "Synthetic fact for a unit test only.",
            }],
            "claims": [{
                "id": "clm-1", "text": "A synthetic test claim.",
                "kind": "fact", "source_ids": ["src-1"],
            }],
        }), encoding="utf-8")
        import_ledger(self.db, job["id"], ledger)
        narration = "Careful geography brings several scales together. " * 240 + "[clm-1]"
        config = replace(self.config, local_llm_model="test-model")
        destination = Path(job["output_dir"]) / "script.md"
        with patch(
            "geofactory.agents.script_agent.generate_json",
            return_value={
                "narration": narration,
                "chapters": [{"title": "Context"}],
                "scene_plan": [{"narration_section": "Opening", "visual_direction": "Illustrative diagram"}],
            },
        ):
            words = generate_evidence_script(self.db, job["id"], destination, config)
        self.assertGreaterEqual(words, 1200)
        self.assertIn("[clm-1]", destination.read_text(encoding="utf-8"))

    def test_documented_cli_dry_run_works_without_api_keys(self) -> None:
        config_path = self.root / "config.json"
        base = [sys.executable, "-m", "geofactory", "--config", str(config_path)]
        init = subprocess.run(base + ["init"], capture_output=True, text=True, timeout=30)
        self.assertEqual(init.returncode, 0, init.stderr)
        demo = subprocess.run(
            base + ["demo", "--topic", "Synthetic workflow test", "--question", "Is this only a demo?"],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(demo.returncode, 0, demo.stderr)
        self.assertIn("critical QA failures", demo.stdout)
        config = json.loads(config_path.read_text(encoding="utf-8"))
        package_root = Path(config["root"]).resolve() / config["output_dir"]
        self.assertTrue(list(package_root.glob("*/qa_report.json")))


if __name__ == "__main__":
    unittest.main()
