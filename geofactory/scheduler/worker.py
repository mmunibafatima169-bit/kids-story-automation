"""Restart-safe offline package generation; rendering is a separate gated step."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from geofactory.agents.research_agent import export_ledger
from geofactory.agents.script_agent import write_script_preview
from geofactory.core.config import FactoryConfig
from geofactory.core.database import Database
from geofactory.core.jobs import get_job, list_jobs, recover_interrupted, transition_job
from geofactory.media.asset_manager import export_asset_ledger
from geofactory.media.thumbnail_maker import make_thumbnail
from geofactory.quality.qa_engine import run_qa

logger = logging.getLogger(__name__)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def process_offline_job(db: Database, config: FactoryConfig, job_id: str) -> dict[str, Any]:
    """Create an honest review package; it does not fabricate research or render media."""
    job = get_job(db, job_id)
    if job["status"] != "proposed":
        raise ValueError(f"Offline processing requires proposed state, got {job['status']}.")
    package = Path(job["output_dir"])
    package.mkdir(parents=True, exist_ok=True)
    transition_job(db, job_id, "researching", "Offline research ledger initialized.")
    transition_job(db, job_id, "scripting", "Writing a source-aware draft placeholder.")
    write_script_preview(db, job_id, package / "script.md", demo=True)
    export_ledger(db, job_id, package / "source_ledger.json")
    export_asset_ledger(db, job_id, package / "asset_ledger.json")
    _write_text(
        package / "narration.txt",
        "DEMO PLACEHOLDER — no narration audio generated. Add reviewed, source-linked script text.",
    )
    _write_text(
        package / "subtitles.srt",
        "1\n00:00:00,000 --> 00:00:04,000\nDEMO PLACEHOLDER — not synchronized narration.\n",
    )
    title = f"Geography documentary draft: {job['topic']}"
    metadata = {
        "title": title,
        "title_candidates": [title, f"The geography behind: {job['topic']}"],
        "description": (
            "WORKFLOW DEMO ONLY. This is not a factual geography documentary. "
            "Research, narration, rights-cleared visuals, and human fact review are still required."
        ),
        "chapters": [
            {"time": "00:00", "title": "The question and geographic context"},
            {"time": "02:00", "title": "Evidence and local variation"},
            {"time": "05:00", "title": "What the evidence cannot show"},
            {"time": "07:00", "title": "A measured conclusion"},
        ],
        "tags": [],
        "language": "en",
        "pinned_comment_suggestion": "What geography question should we research next?",
        "visibility": "private",
        "disclaimer": "Synthetic workflow package; not ready to publish.",
    }
    _write_text(package / "metadata.json", json.dumps(metadata, indent=2) + "\n")
    short_text = "RESEARCH FIRST"
    make_thumbnail(job["topic"], short_text, package / "thumbnail.png", config)
    _write_text(
        package / "attribution.md",
        "# Visual attribution\n\nNo external assets are included in this demo package.\n",
    )
    (package / "source_ledger.json").touch(exist_ok=True)
    transition_job(db, job_id, "awaiting_review", "Draft package prepared for review.")
    transition_job(db, job_id, "rendering", "Render prerequisites checked.")
    report = run_qa(db, job_id, config)
    if report["approval_blocked"]:
        transition_job(
            db, job_id, "qa_failed",
            "Critical render, narration, research, and/or asset prerequisites are missing.",
        )
    else:
        transition_job(db, job_id, "awaiting_approval", "Automatic QA passed; manual approval required.")
    logger.info("Prepared offline package for job %s; approval_blocked=%s", job_id, report["approval_blocked"])
    return report


def recover_and_process(db: Database, config: FactoryConfig, max_jobs: int = 1) -> list[str]:
    if max_jobs < 1:
        raise ValueError("max_jobs must be at least one.")
    recovered = recover_interrupted(db)
    logger.info("Restart recovery processed %d interrupted job(s).", len(recovered))
    finished = []
    for job in reversed(list_jobs(db)):
        if job["status"] != "proposed":
            continue
        process_offline_job(db, config, job["id"])
        finished.append(job["id"])
        if len(finished) >= max_jobs:
            break
    return finished
