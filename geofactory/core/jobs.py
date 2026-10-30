"""Job lifecycle, recovery, idempotency, and approval integrity."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from geofactory.core.database import Database

STATUSES = {
    "proposed", "researching", "scripting", "awaiting_review", "rendering",
    "qa_failed", "awaiting_approval", "approved", "uploading",
    "uploaded_private", "scheduled", "published", "rejected", "failed",
}
ACTIVE_STATUSES = {"researching", "scripting", "rendering", "uploading"}
TRANSITIONS = {
    "proposed": {"researching", "rejected", "failed"},
    "researching": {"scripting", "qa_failed", "failed", "proposed"},
    "scripting": {"awaiting_review", "qa_failed", "failed", "proposed"},
    "awaiting_review": {"rendering", "scripting", "rejected", "failed"},
    "rendering": {"awaiting_approval", "qa_failed", "failed", "proposed"},
    "qa_failed": {"researching", "scripting", "rendering", "rejected", "failed"},
    "awaiting_approval": {"approved", "rejected", "scripting", "qa_failed"},
    "approved": {"uploading", "awaiting_approval"},
    "uploading": {"uploaded_private", "scheduled", "published", "failed", "approved"},
    "uploaded_private": {"scheduled", "published"},
    "scheduled": {"published"},
    "published": set(),
    "rejected": set(),
    "failed": {"proposed", "rejected"},
}


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_job(
    db: Database,
    topic: str,
    question: str,
    output_root: Path,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    topic, question = topic.strip(), question.strip()
    if not topic:
        raise ValueError("A non-empty documentary topic is required.")
    if not question:
        raise ValueError("A clear viewer question is required for every topic.")
    key = idempotency_key or hashlib.sha256(
        f"{topic.casefold()}|{question.casefold()}".encode("utf-8")
    ).hexdigest()
    job_id = str(uuid.uuid4())
    out = (output_root / job_id).resolve()
    timestamp = now_utc()
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                """INSERT OR IGNORE INTO jobs
                   (id, topic, question, status, idempotency_key, output_dir,
                    created_at, updated_at)
                   VALUES (?, ?, ?, 'proposed', ?, ?, ?, ?)""",
                (job_id, topic, question, key, str(out), timestamp, timestamp),
            )
            row = conn.execute(
                "SELECT * FROM jobs WHERE idempotency_key = ?", (key,)
            ).fetchone()
            if row["id"] == job_id:
                conn.execute(
                    """INSERT INTO job_events
                       (job_id, from_status, to_status, detail, created_at)
                       VALUES (?, '', 'proposed', 'Topic proposal created', ?)""",
                    (job_id, timestamp),
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return dict(row)


def transition_job(
    db: Database, job_id: str, new_status: str, detail: str = "", error: str = ""
) -> dict[str, Any]:
    if new_status not in STATUSES:
        raise ValueError(f"Unknown job status: {new_status}")
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(f"Unknown job {job_id}")
            old_status = row["status"]
            if new_status not in TRANSITIONS[old_status]:
                raise ValueError(f"Invalid job transition: {old_status} -> {new_status}")
            timestamp = now_utc()
            conn.execute(
                "UPDATE jobs SET status = ?, error = ?, updated_at = ? WHERE id = ?",
                (new_status, error, timestamp, job_id),
            )
            conn.execute(
                """INSERT INTO job_events
                   (job_id, from_status, to_status, detail, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (job_id, old_status, new_status, detail, timestamp),
            )
            updated = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return dict(updated)


def list_jobs(db: Database, limit: int = 100) -> list[dict[str, Any]]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(row) for row in rows]


def get_job(db: Database, job_id: str) -> dict[str, Any]:
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise KeyError(f"Unknown job {job_id}")
    return dict(row)


def recover_interrupted(db: Database, max_attempts: int = 3) -> list[str]:
    """Requeue interrupted work; jobs at the retry limit fail with an explanation."""
    recovered: list[str] = []
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            rows = conn.execute(
                f"SELECT * FROM jobs WHERE status IN ({','.join('?' for _ in ACTIVE_STATUSES)})",
                tuple(sorted(ACTIVE_STATUSES)),
            ).fetchall()
            for row in rows:
                attempts = row["attempts"] + 1
                ambiguous_upload = row["status"] == "uploading"
                status = "failed" if ambiguous_upload or attempts >= max_attempts else "proposed"
                detail = (
                    "Upload outcome is unknown after restart; verify provider status before any retry."
                    if ambiguous_upload
                    else "Restart recovery reached retry limit."
                    if status == "failed"
                    else "Restart recovery requeued interrupted work."
                )
                timestamp = now_utc()
                conn.execute(
                    "UPDATE jobs SET status = ?, attempts = ?, error = ?, updated_at = ? WHERE id = ?",
                    (status, attempts, detail if status == "failed" else "", timestamp, row["id"]),
                )
                conn.execute(
                    """INSERT INTO job_events
                       (job_id, from_status, to_status, detail, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (row["id"], row["status"], status, detail, timestamp),
                )
                recovered.append(row["id"])
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return recovered


def package_digest(package_dir: Path) -> str:
    """Hash relative names and bytes of all package files deterministically."""
    if not package_dir.is_dir():
        raise FileNotFoundError(f"Package directory does not exist: {package_dir}")
    digest = hashlib.sha256()
    paths = list(package_dir.rglob("*"))
    if any(path.is_symlink() for path in paths):
        raise ValueError("Package contains a symlink; approval hashing is blocked.")
    files = sorted(
        path for path in paths
        if path.is_file() and path.relative_to(package_dir).as_posix() != "approval.json"
    )
    if not files:
        raise ValueError("Cannot approve an empty package.")
    for path in files:
        relative = path.relative_to(package_dir).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    return digest.hexdigest()


def record_approval(
    db: Database, job_id: str, reviewer: str, note: str, digest: str
) -> str:
    approval_id = str(uuid.uuid4())
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(f"Unknown job {job_id}")
            if row["status"] != "awaiting_approval":
                raise ValueError("Only a job awaiting approval may be approved.")
            timestamp = now_utc()
            conn.execute(
                """INSERT INTO approvals
                   (id, job_id, package_sha256, decision, reviewer, note, created_at)
                   VALUES (?, ?, ?, 'approved', ?, ?, ?)""",
                (approval_id, job_id, digest, reviewer, note, timestamp),
            )
            conn.execute(
                "UPDATE jobs SET status = 'approved', updated_at = ? WHERE id = ?",
                (timestamp, job_id),
            )
            conn.execute(
                """INSERT INTO job_events
                   (job_id, from_status, to_status, detail, created_at)
                   VALUES (?, 'awaiting_approval', 'approved', ?, ?)""",
                (job_id, f"Approved by {reviewer}", timestamp),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return approval_id


def approval_is_current(db: Database, job_id: str, package_dir: Path) -> bool:
    current_digest = package_digest(package_dir)
    with db.connect() as conn:
        row = conn.execute(
            """SELECT * FROM approvals
               WHERE job_id = ? AND decision = 'approved' AND invalidated_at = ''
               ORDER BY created_at DESC LIMIT 1""",
            (job_id,),
        ).fetchone()
        if row is None:
            return False
        if row["package_sha256"] == current_digest:
            return True
        timestamp = now_utc()
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                "UPDATE approvals SET invalidated_at = ? WHERE id = ?",
                (timestamp, row["id"]),
            )
            job = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if job and job["status"] == "approved":
                conn.execute(
                    "UPDATE jobs SET status = 'awaiting_approval', updated_at = ? WHERE id = ?",
                    (timestamp, job_id),
                )
                conn.execute(
                    """INSERT INTO job_events
                       (job_id, from_status, to_status, detail, created_at)
                       VALUES (?, 'approved', 'awaiting_approval',
                               'Package hash changed; approval invalidated.', ?)""",
                    (job_id, timestamp),
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return False


def save_metadata(db: Database, job_id: str, metadata: dict[str, Any]) -> None:
    with db.connect() as conn:
        conn.execute(
            "UPDATE jobs SET metadata_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(metadata, ensure_ascii=False, sort_keys=True), now_utc(), job_id),
        )
