"""Technical, evidence, and rights checks that gate human approval."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from geofactory.core.config import FactoryConfig
from geofactory.core.database import Database
from geofactory.media.asset_manager import ALLOWED_LICENSES, sha256_file
from geofactory.media.video_editor import probe_video


TIMESTAMP_RE = re.compile(r"^(\d{2}):(\d{2}):(\d{2}),(\d{3})$")


def _check(severity: str, code: str, message: str) -> dict[str, str]:
    return {"severity": severity, "code": code, "message": message}


def validate_srt(path: Path) -> list[str]:
    if not path.is_file():
        return ["Subtitles file is missing."]
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return ["Subtitles file is empty."]
    problems = []
    for block_number, block in enumerate(re.split(r"\n\s*\n", text), 1):
        lines = block.splitlines()
        if len(lines) < 3 or not lines[0].isdigit() or " --> " not in lines[1]:
            problems.append(f"Subtitle block {block_number} has invalid structure.")
            continue
        start, end = lines[1].split(" --> ", 1)
        match_start, match_end = TIMESTAMP_RE.fullmatch(start), TIMESTAMP_RE.fullmatch(end)
        if not match_start or not match_end:
            problems.append(f"Subtitle block {block_number} has invalid timestamps.")
            continue
        start_ms = _to_ms(match_start.groups())
        end_ms = _to_ms(match_end.groups())
        if start_ms < 0 or end_ms < 0 or end_ms <= start_ms or end_ms - start_ms > 10_000:
            problems.append(f"Subtitle block {block_number} has an invalid duration.")
        if any(len(line) > 46 for line in lines[2:]):
            problems.append(f"Subtitle block {block_number} has a line over 46 characters.")
    return problems


def _to_ms(parts: tuple[str, ...]) -> int:
    hours, minutes, seconds, millis = (int(part) for part in parts)
    if minutes > 59 or seconds > 59:
        return -1
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + millis


def run_qa(db: Database, job_id: str, config: FactoryConfig) -> dict[str, Any]:
    from geofactory.core.jobs import get_job

    job = get_job(db, job_id)
    package = Path(job["output_dir"])
    checks: list[dict[str, str]] = []
    video_path = package / "final.mp4"
    if not video_path.is_file() or video_path.stat().st_size == 0:
        checks.append(_check("critical", "missing_video", "Final video is missing or empty."))
    else:
        try:
            probe = probe_video(config, video_path)
            streams = probe.get("streams", [])
            video_stream = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
            audio_stream = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
            if video_stream is None:
                checks.append(_check("critical", "missing_video_stream", "Video has no video stream."))
            if audio_stream is None:
                checks.append(_check("critical", "missing_audio_stream", "Video has no audio stream."))
            if video_stream:
                width, height = int(video_stream.get("width", 0)), int(video_stream.get("height", 0))
                if width < 1280 or height < 720:
                    checks.append(_check("critical", "low_resolution", f"Resolution is {width}x{height}; minimum is 1280x720."))
            duration = float(probe.get("format", {}).get("duration", 0))
            if duration < 480 or duration > 900:
                checks.append(_check("critical", "duration_out_of_range", f"Video duration is {duration:.1f}s; target is 480-900s."))
            checks.append(_check("info", "probe_passed", "FFprobe opened the rendered file. Manual frame/audio review remains required."))
        except (RuntimeError, ValueError, OSError, StopIteration) as exc:
            checks.append(_check("critical", "video_probe_failed", str(exc)))

    subtitle_problems = validate_srt(package / "subtitles.srt")
    for problem in subtitle_problems:
        checks.append(_check("critical", "subtitle_invalid", problem))
    if not subtitle_problems:
        checks.append(_check("info", "subtitles_present", "Subtitle file has valid SRT structure and line-length limits."))

    narration = package / "narration.wav"
    if not narration.is_file() or narration.stat().st_size == 0:
        checks.append(_check("critical", "missing_narration", "Narration audio WAV is missing or empty."))

    with db.connect() as conn:
        sources = conn.execute("SELECT * FROM sources WHERE job_id = ?", (job_id,)).fetchall()
        claims = conn.execute(
            "SELECT * FROM claims WHERE job_id = ?",
            (job_id,),
        ).fetchall()
        assets = conn.execute("SELECT * FROM assets WHERE job_id = ?", (job_id,)).fetchall()
    known_source_ids = {row["id"] for row in sources}
    try:
        source_ledger = json.loads((package / "source_ledger.json").read_text(encoding="utf-8"))
        source_records = {record["id"]: record for record in source_ledger.get("sources", [])}
        claim_records = {record["id"]: record for record in source_ledger.get("claims", [])}
        if set(source_records) != known_source_ids or set(claim_records) != {row["id"] for row in claims}:
            checks.append(_check("critical", "source_ledger_mismatch", "Source/claim ledger does not match persisted research records."))
        for source in sources:
            if not str(source["licence"]).strip() or source["licence"].casefold() == "unknown":
                checks.append(_check("critical", "source_licence_missing", f"Source {source['id']} has no verified licence record."))
            record = source_records.get(source["id"], {})
            if any(
                record.get(field) != source[field]
                for field in ("title", "url", "publisher", "licence", "licence_url", "evidence")
            ):
                checks.append(_check("critical", "source_ledger_mismatch", f"Source {source['id']} differs from its persisted record."))
        for claim in claims:
            record = claim_records.get(claim["id"], {})
            if (
                record.get("text") != claim["text"]
                or record.get("kind") != claim["kind"]
                or record.get("source_ids") != json.loads(claim["source_ids_json"])
            ):
                checks.append(_check("critical", "claim_ledger_mismatch", f"Claim {claim['id']} differs from its persisted record."))
    except (OSError, json.JSONDecodeError, KeyError, TypeError, AttributeError) as exc:
        checks.append(_check("critical", "source_ledger_invalid", f"Source ledger is invalid: {exc}"))

    for claim in claims:
        refs = json.loads(claim["source_ids_json"])
        if claim["kind"] == "fact" and not refs:
            checks.append(_check("critical", "unsupported_claim", f"Factual claim {claim['id']} has no source."))
        elif any(ref not in known_source_ids for ref in refs):
            checks.append(_check("critical", "unknown_claim_source", f"Claim {claim['id']} cites an unknown source."))
        elif claim["kind"] == "uncertain" and not claim["uncertainty"]:
            checks.append(_check("medium", "uncertainty_unexplained", f"Claim {claim['id']} needs an uncertainty note."))
        script_path = package / "script.md"
        if claim["kind"] == "fact" and script_path.is_file():
            script_text = script_path.read_text(encoding="utf-8")
            narration = script_text.partition("## Narration")[2]
            if not narration or f"[{claim['id']}]" not in narration:
                checks.append(_check("critical", "claim_not_in_script", f"Factual claim {claim['id']} is not cited in the script."))
    if not claims:
        checks.append(_check("medium", "no_claim_ledger", "No researched claims exist; factual content cannot be approved."))
    if claims and not any(check["code"] in {"unsupported_claim", "unknown_claim_source"} for check in checks):
        checks.append(_check("info", "claims_have_sources", "Every factual claim references a recorded source."))

    try:
        asset_ledger = json.loads((package / "asset_ledger.json").read_text(encoding="utf-8"))
        asset_records = {record["id"]: record for record in asset_ledger.get("assets", [])}
        if set(asset_records) != {row["id"] for row in assets}:
            checks.append(_check("critical", "asset_ledger_mismatch", "Asset ledger does not match persisted asset records."))
    except (OSError, json.JSONDecodeError, KeyError, TypeError, AttributeError) as exc:
        asset_records = {}
        checks.append(_check("critical", "asset_ledger_invalid", f"Asset ledger is invalid: {exc}"))
    for asset in assets:
        path = Path(asset["path"])
        if (
            asset["licence"] not in ALLOWED_LICENSES
            or not asset["commercial_use"]
            or not asset["attribution"].strip()
            or not asset["title"].strip()
            or not asset["publisher"].strip()
            or not asset["creator"].strip()
            or not asset["retrieved_at"].strip()
        ):
            checks.append(_check("critical", "asset_rights_invalid", f"Asset {asset['id']} does not have approved rights."))
        elif not path.is_file() or sha256_file(path) != asset["file_sha256"]:
            checks.append(_check("critical", "asset_missing_or_changed", f"Asset {asset['id']} is missing or changed."))
        record = asset_records.get(asset["id"], {})
        if (
            record.get("file_sha256") != asset["file_sha256"]
            or record.get("title") != asset["title"]
            or record.get("publisher") != asset["publisher"]
            or record.get("licence") != asset["licence"]
            or record.get("licence_url") != asset["licence_url"]
            or record.get("attribution") != asset["attribution"]
        ):
            checks.append(_check("critical", "asset_ledger_mismatch", f"Asset ledger entry {asset['id']} differs from its persisted record."))
    if not assets:
        checks.append(_check("critical", "no_rights_cleared_visuals", "No rights-cleared visual assets are registered."))
    attribution_path = package / "attribution.md"
    if assets and not attribution_path.is_file():
        checks.append(_check("critical", "missing_attribution_report", "Asset attribution report is missing."))
    elif assets:
        attribution_text = attribution_path.read_text(encoding="utf-8")
        for asset in assets:
            if asset["attribution"] not in attribution_text:
                checks.append(_check("critical", "asset_attribution_missing", f"Attribution for asset {asset['id']} is missing."))

    for required in ("script.md", "metadata.json", "thumbnail.svg", "source_ledger.json", "asset_ledger.json"):
        if not (package / required).is_file():
            checks.append(_check("critical", "missing_package_file", f"Required package file is missing: {required}."))
    if (package / "metadata.json").is_file():
        try:
            metadata = json.loads((package / "metadata.json").read_text(encoding="utf-8"))
            if not str(metadata.get("title", "")).strip() or not str(metadata.get("description", "")).strip():
                checks.append(_check("critical", "metadata_incomplete", "Metadata title and description are required."))
        except (OSError, json.JSONDecodeError):
            checks.append(_check("critical", "metadata_invalid", "Metadata JSON is unreadable."))
    checks.append(_check("medium", "manual_review_required", "Check factual nuance, title/thumbnail accuracy, black/frozen frames, levels, and policy manually."))

    report: dict[str, Any] = {
        "job_id": job_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
        "critical_failures": sum(check["severity"] == "critical" for check in checks),
        "approval_blocked": any(check["severity"] == "critical" for check in checks),
        "automatic_review_limits": [
            "Automated checks cannot establish factual accuracy or detect every misleading edit.",
            "Manual full-length viewing/listening and current platform-policy review are required.",
        ],
    }
    package.mkdir(parents=True, exist_ok=True)
    (package / "qa_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    markdown = [
        f"# QA report — {job['topic']}",
        "",
        f"**Approval blocked:** {'yes' if report['approval_blocked'] else 'no'}",
        f"**Critical failures:** {report['critical_failures']}",
        "",
    ]
    markdown.extend(
        f"- **{check['severity'].upper()} — {check['code']}:** {check['message']}"
        for check in checks
    )
    markdown.append("")
    (package / "qa_report.md").write_text("\n".join(markdown), encoding="utf-8")
    return report


def assert_approvable(job: dict[str, Any], package: Path) -> dict[str, Any]:
    report_path = package / "qa_report.json"
    if not report_path.is_file():
        raise ValueError("A current QA report is required before approval.")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("job_id") != job["id"] or report.get("approval_blocked"):
        raise ValueError("QA has critical failures; approval is blocked.")
    if not (package / "final.mp4").is_file() or not (package / "metadata.json").is_file():
        raise ValueError("Final video and metadata must exist before approval.")
    return report
