"""Evidence-led source and claim records; no generated citations are accepted."""

from __future__ import annotations

import json
import re
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from geofactory.core.database import Database


class ResearchError(ValueError):
    """Raised when a source or claim is incomplete or invalid."""


def import_ledger(db: Database, job_id: str, ledger_path: Path) -> dict[str, int]:
    try:
        data = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResearchError(f"Could not read source ledger {ledger_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ResearchError("Source ledger root must be a JSON object.")
    sources = data.get("sources", [])
    claims = data.get("claims", [])
    if not isinstance(sources, list) or not isinstance(claims, list):
        raise ResearchError("Source ledger must contain sources and claims arrays.")
    source_ids: set[str] = set()
    inserted_sources = 0
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            for source in sources:
                if not isinstance(source, dict):
                    raise ResearchError("Every source record must be a JSON object.")
                required = ("title", "url", "publisher", "evidence")
                if any(
                    not isinstance(source.get(key), str) or not source[key].strip()
                    for key in required
                ):
                    raise ResearchError(f"Source is missing a required field: {required}")
                parsed = urlparse(source["url"])
                if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
                    raise ResearchError("Source URLs must be valid HTTPS URLs.")
                accessed_at = source.get("accessed_at") or datetime.now(timezone.utc).date().isoformat()
                published_at = source.get("published_at", "")
                if not isinstance(accessed_at, str) or not isinstance(published_at, str):
                    raise ResearchError("Source dates must be ISO date strings.")
                try:
                    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", accessed_at):
                        raise ValueError
                    if published_at and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", published_at):
                        raise ValueError
                    date.fromisoformat(accessed_at)
                    if published_at:
                        date.fromisoformat(published_at)
                except ValueError as exc:
                    raise ResearchError("Source dates must use YYYY-MM-DD.") from exc
                source_id_value = source.get("id") or f"src-{uuid.uuid4().hex[:12]}"
                if (
                    not isinstance(source_id_value, str)
                    or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", source_id_value)
                ):
                    raise ResearchError("Source ID must use 1-80 letters, digits, underscores, or hyphens.")
                source_id = source_id_value.strip()
                if source_id in source_ids:
                    raise ResearchError(f"Duplicate source ID in ledger: {source_id}")
                existing = conn.execute(
                    "SELECT job_id FROM sources WHERE id = ?", (source_id,)
                ).fetchone()
                if existing and existing["job_id"] != job_id:
                    raise ResearchError(f"Source ID is already assigned to another job: {source_id}")
                source_ids.add(source_id)
                conn.execute(
                    """INSERT INTO sources
                       (id, job_id, title, url, publisher, accessed_at, published_at,
                        evidence, notes)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(id) DO UPDATE SET
                         title=excluded.title, url=excluded.url, publisher=excluded.publisher,
                         accessed_at=excluded.accessed_at, published_at=excluded.published_at,
                         evidence=excluded.evidence, notes=excluded.notes
                       WHERE sources.job_id=excluded.job_id""",
                    (
                        source_id, job_id, source["title"], source["url"],
                        source["publisher"],
                        accessed_at, published_at, source["evidence"], str(source.get("notes", "")),
                    ),
                )
                inserted_sources += 1
            inserted_claims = 0
            claim_ids_seen: set[str] = set()
            for claim in claims:
                if not isinstance(claim, dict):
                    raise ResearchError("Every claim record must be a JSON object.")
                text_value = claim.get("text", "")
                kind_value = claim.get("kind", "fact")
                if not isinstance(text_value, str) or not isinstance(kind_value, str):
                    raise ResearchError("Claim text and kind must be strings.")
                text = text_value.strip()
                kind = kind_value.strip().lower()
                refs = claim.get("source_ids", [])
                if not text or kind not in {"fact", "interpretation", "opinion", "uncertain"}:
                    raise ResearchError("Each claim needs text and a supported kind.")
                if not isinstance(refs, list) or any(
                    not isinstance(ref, str) or ref not in source_ids for ref in refs
                ):
                    raise ResearchError(f"Claim cites a source absent from this ledger: {text[:80]}")
                if kind == "fact" and not refs:
                    raise ResearchError(f"Factual claim has no evidence: {text[:80]}")
                claim_id_value = claim.get("id") or f"clm-{uuid.uuid4().hex[:12]}"
                if (
                    not isinstance(claim_id_value, str)
                    or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", claim_id_value)
                ):
                    raise ResearchError("Claim ID must use 1-80 letters, digits, underscores, or hyphens.")
                if claim_id_value in claim_ids_seen:
                    raise ResearchError(f"Duplicate claim ID in ledger: {claim_id_value}")
                claim_ids_seen.add(claim_id_value)
                existing = conn.execute(
                    "SELECT job_id FROM claims WHERE id = ?", (claim_id_value,)
                ).fetchone()
                if existing and existing["job_id"] != job_id:
                    raise ResearchError(f"Claim ID is already assigned to another job: {claim_id_value}")
                conn.execute(
                    """INSERT INTO claims
                       (id, job_id, text, kind, source_ids_json, uncertainty)
                       VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(id) DO UPDATE SET
                         text=excluded.text, kind=excluded.kind,
                         source_ids_json=excluded.source_ids_json,
                         uncertainty=excluded.uncertainty
                       WHERE claims.job_id=excluded.job_id""",
                    (
                        claim_id_value,
                        job_id, text, kind, json.dumps(refs), str(claim.get("uncertainty", "")),
                    ),
                )
                inserted_claims += 1
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return {"sources": inserted_sources, "claims": inserted_claims}


def export_ledger(db: Database, job_id: str, destination: Path) -> None:
    with db.connect() as conn:
        sources = [
            dict(row)
            for row in conn.execute("SELECT * FROM sources WHERE job_id = ?", (job_id,))
        ]
        claims = []
        for row in conn.execute("SELECT * FROM claims WHERE job_id = ?", (job_id,)):
            claim = dict(row)
            claim["source_ids"] = json.loads(claim.pop("source_ids_json"))
            claims.append(claim)
    for source in sources:
        source.pop("job_id", None)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps({"sources": sources, "claims": claims}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def claim_record(
    text: str, kind: str, source_ids: list[str] | None = None, uncertainty: str = ""
) -> dict[str, object]:
    if kind == "fact" and not source_ids:
        raise ResearchError("Factual claim requires at least one source reference.")
    return {"text": text, "kind": kind, "source_ids": source_ids or [], "uncertainty": uncertainty}
