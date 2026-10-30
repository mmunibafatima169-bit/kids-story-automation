"""Command-line entry point for the local documentary factory."""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

from geofactory.agents.research_agent import export_ledger, import_ledger
from geofactory.agents.script_agent import write_script_preview
from geofactory.agents.topic_agent import propose_topics
from geofactory.analytics.feedback import import_csv, report as analytics_report
from geofactory.app.dashboard import serve
from geofactory.core.config import ConfigurationError, FactoryConfig, load_config
from geofactory.core.database import Database
from geofactory.core.jobs import (
    approval_is_current, create_job, get_job, list_jobs, package_digest,
    record_approval, transition_job,
)
from geofactory.media.asset_manager import (
    export_asset_ledger, register_asset, write_attribution_report,
)
from geofactory.media.tts_engine import synthesize, write_estimated_srt
from geofactory.media.video_editor import RenderError, render_video
from geofactory.publishing.youtube_client import upload_approved_video
from geofactory.quality.qa_engine import assert_approvable, run_qa
from geofactory.scheduler.worker import process_offline_job, recover_and_process


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="geofactory", description="Local-first geography documentary tools")
    parser.add_argument("--config", type=Path, help="JSON configuration path (defaults to ./config.json)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="Create local config and initialize the SQLite database")
    sub.add_parser("doctor", help="Check machine capabilities and application health")
    topics = sub.add_parser("topics", help="Generate locally scored topic proposals")
    topics.add_argument("--limit", type=int, default=5)

    new = sub.add_parser("new", help="Save a topic proposal as a persistent job")
    new.add_argument("--topic", required=True)
    new.add_argument("--question", required=True)

    demo = sub.add_parser("demo", help="Build a clearly labelled offline sample package")
    demo.add_argument("--topic", default="River cities")
    demo.add_argument("--question", default="Why do some cities grow around rivers?")

    research = sub.add_parser("research", help="Import a human-prepared source and claim ledger")
    research.add_argument("job_id")
    research.add_argument("--ledger", type=Path, required=True)

    script = sub.add_parser("script", help="Write an evidence-aware script preview")
    script.add_argument("job_id")

    asset = sub.add_parser("asset-add", help="Register one local asset after rights verification")
    asset.add_argument("job_id")
    asset.add_argument("--file", type=Path, required=True)
    asset.add_argument("--origin-url", required=True)
    asset.add_argument("--creator", required=True)
    asset.add_argument("--licence", required=True)
    asset.add_argument("--attribution", required=True)
    asset.add_argument("--commercial-use", action="store_true", required=True)

    narrate = sub.add_parser("narrate", help="Generate local Piper narration from reviewed text")
    narrate.add_argument("job_id")
    narrate.add_argument("--text-file", type=Path, required=True)

    render = sub.add_parser("render", help="Render from registered assets and narration using FFmpeg")
    render.add_argument("job_id")

    qa = sub.add_parser("qa", help="Run release-gate checks for a job")
    qa.add_argument("job_id")

    approve = sub.add_parser("approve", help="Approve exact final package bytes after passing QA")
    approve.add_argument("job_id")
    approve.add_argument("--reviewer", default="local owner")
    approve.add_argument("--note", default="")

    reject = sub.add_parser("reject", help="Reject a job")
    reject.add_argument("job_id")
    reject.add_argument("--note", default="Rejected by owner.")

    sub.add_parser("status", help="List persisted jobs")
    work = sub.add_parser("work", help="Recover interrupted jobs and process queued proposals")
    work.add_argument("--max-jobs", type=int, default=1)
    review_ready = sub.add_parser("review-ready", help="Mark a manually revised package ready for review")
    review_ready.add_argument("job_id")
    dashboard = sub.add_parser("serve", help="Start the loopback-only approval dashboard")
    dashboard.add_argument("--port", type=int, help="Override the configured local port")

    backup = sub.add_parser("backup", help="Create a consistent SQLite backup")
    backup.add_argument("--destination", type=Path, required=True)

    analytics = sub.add_parser("analytics-import", help="Import owner-provided analytics CSV")
    analytics.add_argument("csv_file", type=Path)
    sub.add_parser("analytics-report", help="Summarize imported analytics descriptively")

    upload = sub.add_parser("upload", help="Attempt the fail-closed optional upload path")
    upload.add_argument("job_id")
    return parser


def _init(config_path: Path | None, config: FactoryConfig) -> None:
    effective_path = config_path or (Path.cwd() / "config.json")
    if not effective_path.exists():
        effective_path.parent.mkdir(parents=True, exist_ok=True)
        effective_path.write_text(
            json.dumps(_example_config(), indent=2) + "\n", encoding="utf-8"
        )
        print(f"Created {effective_path}")
    config.output_dir.mkdir(parents=True, exist_ok=True)
    Database(config.database_path).initialize()
    print(f"Database ready: {config.database_path}")


def _example_config() -> dict[str, Any]:
    return {
        "root": ".",
        "database_path": "data/geofactory.sqlite3",
        "output_dir": "output",
        "bind_host": "127.0.0.1",
        "dashboard_port": 8765,
        "require_topic_approval": True,
        "dry_run": True,
        "publishing_enabled": False,
        "ffmpeg": "ffmpeg",
        "ffprobe": "ffprobe",
        "tts_backend": "none",
        "piper_executable": "piper",
        "piper_voice_model": "",
        "piper_voice_license": "",
        "local_llm_url": "http://127.0.0.1:11434",
        "local_llm_model": "",
        "minimum_free_disk_gb": 2.0,
        "width": 1920,
        "height": 1080,
    }


def _doctor(config: FactoryConfig) -> dict[str, Any]:
    usage = shutil.disk_usage(config.root)
    tools = {}
    for name in ("ffmpeg", "ffprobe", "ollama", config.piper_executable):
        executable = config.ffmpeg if name == "ffmpeg" else (
            config.ffprobe if name == "ffprobe" else name
        )
        tools[name] = shutil.which(executable) or "not found"
    memory: dict[str, str] = {}
    meminfo = Path("/proc/meminfo")
    if meminfo.is_file():
        for line in meminfo.read_text(encoding="utf-8").splitlines():
            if line.startswith(("MemTotal:", "MemAvailable:")):
                memory[line.split(":", 1)[0]] = line.split(":", 1)[1].strip()
    gpu = shutil.which("nvidia-smi")
    gpu_info = "nvidia-smi not found"
    if gpu:
        result = subprocess.run([gpu, "--query-gpu=name,memory.total", "--format=csv,noheader"], capture_output=True, text=True, timeout=10)
        gpu_info = result.stdout.strip() if result.returncode == 0 else "GPU query failed"
    db = Database(config.database_path)
    db.initialize()
    return {
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "machine": __import__("platform").machine(),
        "memory": memory,
        "disk_free_bytes": usage.free,
        "disk_free_gib": round(usage.free / (1024 ** 3), 2),
        "gpu": gpu_info,
        "tools": tools,
        "database": str(config.database_path),
        "dashboard_bind": f"{config.bind_host}:{config.dashboard_port}",
        "publishing_enabled": config.publishing_enabled,
        "assessment": (
            "Hardware-independent workflow is available. Rendering and narration require "
            "separately installed, configured tools."
        ),
    }


def _create_or_get_job(db: Database, config: FactoryConfig, topic: str, question: str) -> dict[str, Any]:
    return create_job(db, topic, question, config.output_dir)


def _process_demo(db: Database, config: FactoryConfig, topic: str, question: str) -> None:
    job = _create_or_get_job(db, config, topic, question)
    print(f"Job {job['id']} ({job['status']})")
    if job["status"] == "proposed":
        report = process_offline_job(db, config, job["id"])
        print(f"Offline package: {job['output_dir']}")
        print(f"Status: {get_job(db, job['id'])['status']}; critical QA failures: {report['critical_failures']}")
    else:
        print(f"Existing job package: {job['output_dir']}")


def _approve(db: Database, job_id: str, reviewer: str, note: str) -> None:
    job = get_job(db, job_id)
    if job["status"] != "awaiting_approval":
        raise ValueError(f"Job status must be awaiting_approval, not {job['status']}.")
    package = Path(job["output_dir"])
    assert_approvable(job, package)
    digest = package_digest(package)
    approval_id = record_approval(db, job_id, reviewer, note, digest)
    (package / "approval.json").write_text(
        json.dumps(
            {"approval_id": approval_id, "reviewer": reviewer, "note": note,
             "package_sha256": digest, "decision": "approved"},
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    print(f"Approved exact package sha256={digest}")


def run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    db = Database(config.database_path)
    db.initialize()

    if args.command == "init":
        _init(args.config, config)
    elif args.command == "doctor":
        print(json.dumps(_doctor(config), indent=2))
    elif args.command == "topics":
        if args.limit < 1 or args.limit > 25:
            raise ValueError("Topic limit must be between 1 and 25.")
        print(json.dumps(propose_topics(db, args.limit), indent=2, ensure_ascii=False))
    elif args.command == "new":
        job = _create_or_get_job(db, config, args.topic, args.question)
        print(json.dumps({"id": job["id"], "status": job["status"], "output_dir": job["output_dir"]}, indent=2))
    elif args.command == "demo":
        _process_demo(db, config, args.topic, args.question)
    elif args.command == "research":
        job = get_job(db, args.job_id)
        counts = import_ledger(db, args.job_id, args.ledger)
        export_ledger(db, args.job_id, Path(job["output_dir"]) / "source_ledger.json")
        print(json.dumps(counts, indent=2))
    elif args.command == "script":
        job = get_job(db, args.job_id)
        if config.local_llm_model:
            from geofactory.agents.script_agent import generate_evidence_script

            words = generate_evidence_script(
                db, args.job_id, Path(job["output_dir"]) / "script.md", config
            )
            print(f"Wrote local-model draft with {words} words; fact-check before use.")
        else:
            words = write_script_preview(db, args.job_id, Path(job["output_dir"]) / "script.md")
            print(f"Wrote evidence-aware draft with {words} words; configure Ollama for a full draft.")
    elif args.command == "asset-add":
        job = get_job(db, args.job_id)
        asset_id = register_asset(
            db, args.job_id, args.file, args.origin_url, args.creator, args.licence,
            args.attribution, args.commercial_use,
        )
        package = Path(job["output_dir"])
        records = export_asset_ledger(db, args.job_id, package / "asset_ledger.json")
        write_attribution_report(records, package / "attribution.md")
        print(f"Registered asset {asset_id}")
    elif args.command == "narrate":
        job = get_job(db, args.job_id)
        text = args.text_file.read_text(encoding="utf-8")
        package = Path(job["output_dir"])
        duration = synthesize(text, package / "narration.wav", config)
        (package / "narration.txt").write_text(text, encoding="utf-8")
        write_estimated_srt(text, package / "subtitles.srt")
        print(f"Generated narration ({duration:.1f}s). Subtitle timings are estimated; review alignment.")
    elif args.command == "render":
        job = get_job(db, args.job_id)
        package = Path(job["output_dir"])
        if job["status"] not in {"awaiting_review", "qa_failed"}:
            raise ValueError(f"Render requires awaiting_review or qa_failed, got {job['status']}.")
        transition_job(db, args.job_id, "rendering", "Owner requested local FFmpeg render.")
        with db.connect() as conn:
            records = [dict(row) for row in conn.execute(
                "SELECT * FROM assets WHERE job_id = ? ORDER BY retrieved_at, id", (args.job_id,)
            )]
        images = [Path(record["path"]) for record in records]
        try:
            render_video(config, images, package / "narration.wav", package / "final.mp4", records)
        except RenderError as exc:
            transition_job(
                db, args.job_id, "qa_failed", "FFmpeg render did not complete.", error=str(exc)
            )
            raise
        report = run_qa(db, args.job_id, config)
        target = "qa_failed" if report["approval_blocked"] else "awaiting_approval"
        transition_job(db, args.job_id, target, "QA completed after render.")
        print(f"Render complete. Status: {target}; critical failures: {report['critical_failures']}")
    elif args.command == "qa":
        job = get_job(db, args.job_id)
        had_approval = job["status"] == "approved"
        if had_approval:
            approval_is_current(db, args.job_id, Path(job["output_dir"]))
            job = get_job(db, args.job_id)
        report = run_qa(db, args.job_id, config)
        if had_approval and get_job(db, args.job_id)["status"] == "approved":
            approval_is_current(db, args.job_id, Path(job["output_dir"]))
            job = get_job(db, args.job_id)
        if job["status"] in {"qa_failed", "awaiting_approval"}:
            target = "qa_failed" if report["approval_blocked"] else "awaiting_approval"
            if target != job["status"]:
                transition_job(db, args.job_id, target, "QA state updated.")
        print(json.dumps(report, indent=2))
        if report["approval_blocked"]:
            return 1
    elif args.command == "approve":
        run_qa(db, args.job_id, config)
        _approve(db, args.job_id, args.reviewer, args.note)
    elif args.command == "reject":
        transition_job(db, args.job_id, "rejected", args.note)
        print("Job rejected.")
    elif args.command == "status":
        rows = list_jobs(db)
        for row in rows:
            if row["status"] == "approved":
                approval_is_current(db, row["id"], Path(row["output_dir"]))
        print(json.dumps(list_jobs(db), indent=2))
    elif args.command == "work":
        processed = recover_and_process(db, config, args.max_jobs)
        print(json.dumps({"processed_job_ids": processed}, indent=2))
    elif args.command == "review-ready":
        transition_job(
            db, args.job_id, "awaiting_review",
            "Owner marked revised package ready for review; rerun QA before approval.",
        )
        print("Package marked awaiting_review. Run QA and review all files.")
    elif args.command == "serve":
        if args.port is not None:
            if not 1024 <= args.port <= 65535:
                raise ValueError("Dashboard port must be between 1024 and 65535.")
            from dataclasses import replace

            config = replace(config, dashboard_port=args.port)
        serve(db, config)
    elif args.command == "backup":
        args.destination.parent.mkdir(parents=True, exist_ok=True)
        source = db.connect()
        destination = sqlite3.connect(args.destination)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
        print(f"Database backup written: {args.destination}")
    elif args.command == "analytics-import":
        print(f"Imported {import_csv(db, args.csv_file)} analytics row(s).")
    elif args.command == "analytics-report":
        print(json.dumps(analytics_report(db), indent=2))
    elif args.command == "upload":
        job = get_job(db, args.job_id)
        if job["status"] != "approved" or not approval_is_current(
            db, args.job_id, Path(job["output_dir"])
        ):
            raise ValueError("Publishing requires current approval for the exact package.")
        upload_approved_video(config, args.job_id)
    else:
        raise ValueError(f"Unhandled command: {args.command}")
    return 0


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        raise SystemExit(run(args))
    except (ConfigurationError, ValueError, KeyError, RuntimeError, OSError, sqlite3.Error) as exc:
        parser.exit(2, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
