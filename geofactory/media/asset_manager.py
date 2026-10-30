"""Rights-aware registration for explicitly supplied local media."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from geofactory.core.database import Database

ALLOWED_LICENSES = {
    "CC0-1.0", "CC-BY-4.0", "CC-BY-SA-4.0", "Public Domain",
}


class AssetRightsError(ValueError):
    """Raised when an asset's reuse rights cannot be established."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _has_image_signature(path: Path) -> bool:
    with path.open("rb") as stream:
        header = stream.read(12)
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return header.startswith(b"\xff\xd8\xff")
    if suffix == ".png":
        return header.startswith(b"\x89PNG\r\n\x1a\n")
    if suffix == ".webp":
        return len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP"
    if suffix == ".bmp":
        return header.startswith(b"BM")
    return False


def register_asset(
    db: Database,
    job_id: str,
    file_path: Path,
    origin_url: str,
    creator: str,
    licence: str,
    attribution: str,
    commercial_use: bool,
) -> str:
    if file_path.is_symlink():
        raise AssetRightsError("Asset must not be a symbolic link.")
    path = file_path.resolve(strict=True)
    if not path.is_file() or path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}:
        raise AssetRightsError("Asset must be a regular, non-symlink file.")
    if not _has_image_signature(path):
        raise AssetRightsError("Asset extension and image file signature do not match.")
    if licence not in ALLOWED_LICENSES:
        raise AssetRightsError(
            f"Licence {licence!r} is not in the accepted reuse list; unknown rights are rejected."
        )
    if not commercial_use:
        raise AssetRightsError("Asset is not cleared for commercial use.")
    parsed = urlparse(origin_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise AssetRightsError("Asset origin must be a valid HTTPS URL.")
    if not creator.strip() or not attribution.strip():
        raise AssetRightsError("Creator and attribution text are required.")
    asset_id = f"ast-{uuid.uuid4().hex[:12]}"
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO assets
               (id, job_id, path, origin_url, creator, licence, attribution,
                commercial_use, file_sha256, retrieved_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
            (
                asset_id, job_id, str(path), origin_url, creator, licence, attribution,
                sha256_file(path), datetime.now(timezone.utc).isoformat(),
            ),
        )
    return asset_id


def export_asset_ledger(db: Database, job_id: str, destination: Path) -> list[dict[str, object]]:
    with db.connect() as conn:
        records = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM assets WHERE job_id = ? ORDER BY id", (job_id,)
            )
        ]
    for record in records:
        record.pop("job_id", None)
    destination.parent.mkdir(parents=True, exist_ok=True)
    import json

    destination.write_text(
        json.dumps({"assets": records}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return records


def write_attribution_report(records: list[dict[str, object]], destination: Path) -> None:
    lines = ["# Visual attribution", ""]
    if not records:
        lines.append("No external assets are included.")
    for record in records:
        lines.append(
            f"- {record['attribution']} — {record['licence']} — "
            f"[origin]({record['origin_url']})"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
