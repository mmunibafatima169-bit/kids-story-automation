"""Rights-aware registration for explicitly supplied local media."""

from __future__ import annotations

import hashlib
import html
from html.parser import HTMLParser
import json
import logging
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from geofactory.core.database import Database
from geofactory.wikimedia import WikimediaError, request_bytes, request_json

ALLOWED_LICENSES = {
    "CC0-1.0",
    "CC-BY-2.0", "CC-BY-3.0", "CC-BY-4.0",
    "CC-BY-SA-2.0", "CC-BY-SA-3.0", "CC-BY-SA-4.0",
    "Public Domain",
}
logger = logging.getLogger(__name__)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data.strip())


def _plain_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    parser = _TextExtractor()
    parser.feed(html.unescape(value))
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()


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


def _extract_metadata(info: dict[str, object], key: str) -> str:
    metadata = info.get("extmetadata")
    if not isinstance(metadata, dict):
        return ""
    item = metadata.get(key)
    if isinstance(item, dict):
        return _plain_text(item.get("value"))
    return _plain_text(item)


def _verified_commons_license(info: dict[str, object]) -> tuple[str, str]:
    short_name = _extract_metadata(info, "LicenseShortName")
    license_url = _extract_metadata(info, "LicenseUrl")
    parsed = urlparse(license_url)
    if parsed.scheme == "http" and parsed.hostname == "creativecommons.org":
        license_url = parsed._replace(scheme="https").geturl()
        parsed = urlparse(license_url)
    if parsed.scheme != "https" or parsed.hostname != "creativecommons.org":
        raise AssetRightsError("Commons record has no verifiable Creative Commons licence URL.")
    if parsed.username or parsed.password or parsed.port not in (None, 443):
        raise AssetRightsError("Commons licence URL has an invalid authority.")
    normalized = re.sub(r"\s+", " ", short_name).strip().upper()
    match = re.fullmatch(r"CC BY(-SA)? ([234])\.0", normalized)
    if match:
        license_id = f"CC-BY{'-SA' if match.group(1) else ''}-{match.group(2)}.0"
        expected_path = (
            f"/licenses/{'by-sa' if match.group(1) else 'by'}/{match.group(2)}.0"
        )
        if parsed.path.rstrip("/") not in {expected_path, f"{expected_path}/deed.en"}:
            raise AssetRightsError("Commons licence name and licence URL do not match.")
        return license_id, f"https://creativecommons.org{expected_path}/"
    if normalized in {"CC0", "CC0 1.0", "CC0 1.0 UNIVERSAL"}:
        if parsed.path.rstrip("/") not in {
            "/publicdomain/zero/1.0",
            "/publicdomain/zero/1.0/deed.en",
        }:
            raise AssetRightsError("Commons CC0 licence URL is inconsistent.")
        return "CC0-1.0", "https://creativecommons.org/publicdomain/zero/1.0/"
    if normalized.startswith("PUBLIC DOMAIN"):
        if parsed.path.rstrip("/") not in {
            "/publicdomain/mark/1.0",
            "/publicdomain/mark/1.0/deed.en",
        }:
            raise AssetRightsError("Commons public-domain mark URL is missing or unrecognized.")
        return "Public Domain", "https://creativecommons.org/publicdomain/mark/1.0/"
    raise AssetRightsError(
        f"Commons licence {short_name or '(missing)'} is not approved for commercial reuse."
    )


def register_asset(
    db: Database,
    job_id: str,
    file_path: Path,
    origin_url: str,
    creator: str,
    licence: str,
    attribution: str,
    commercial_use: bool,
    *,
    title: str = "",
    publisher: str = "Owner-provided source",
    licence_url: str = "",
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
    if (
        parsed.scheme != "https" or not parsed.netloc
        or parsed.username or parsed.password
    ):
        raise AssetRightsError("Asset origin must be a valid HTTPS URL.")
    if licence_url:
        parsed_licence = urlparse(licence_url)
        if (
            parsed_licence.scheme != "https" or not parsed_licence.netloc
            or parsed_licence.username or parsed_licence.password
        ):
            raise AssetRightsError("Asset licence URL must be a valid HTTPS URL.")
    if not creator.strip() or not attribution.strip():
        raise AssetRightsError("Creator and attribution text are required.")
    asset_id = f"ast-{uuid.uuid4().hex[:12]}"
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO assets
               (id, job_id, title, publisher, path, origin_url, creator, licence,
                licence_url, attribution, commercial_use, file_sha256, retrieved_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
            (
                asset_id, job_id, title.strip() or path.stem,
                publisher.strip() or "Owner-provided source", str(path),
                origin_url, creator, licence, licence_url, attribution,
                sha256_file(path), datetime.now(timezone.utc).isoformat(),
            ),
        )
    return asset_id


def search_and_download_commons(
    db: Database,
    job_id: str,
    query: str,
    destination_dir: Path,
    *,
    limit: int = 8,
    max_downloads: int = 3,
) -> dict[str, object]:
    """Find Commons files, reject incompatible terms, then download verified images."""
    query = query.strip()
    if not query or len(query) > 250:
        raise AssetRightsError("Commons query must contain 1-250 characters.")
    if not 1 <= limit <= 20 or not 1 <= max_downloads <= 5:
        raise AssetRightsError("Commons limits must be 1-20 results and 1-5 downloads.")
    try:
        response = request_json(
            "commons",
            {
                "action": "query",
                "generator": "search",
                "gsrsearch": f"filetype:bitmap {query}",
                "gsrnamespace": 6,
                "gsrlimit": limit,
                "prop": "imageinfo",
                "iiprop": "url|extmetadata|size|mime",
                "iiurlwidth": 1600,
                "format": "json",
                "formatversion": 2,
            },
        )
    except WikimediaError as exc:
        raise AssetRightsError(str(exc)) from exc
    query_data = response.get("query")
    pages = query_data.get("pages", []) if isinstance(query_data, dict) else []
    if not isinstance(pages, list):
        raise AssetRightsError("Commons API returned an invalid page list.")

    destination_dir.mkdir(parents=True, exist_ok=True)
    registered: list[dict[str, object]] = []
    rejected: list[dict[str, str]] = []
    access_time = datetime.now(timezone.utc).isoformat()
    for page in pages:
        if not isinstance(page, dict):
            continue
        raw_title = page.get("title")
        title = raw_title.removeprefix("File:") if isinstance(raw_title, str) else ""
        imageinfo = page.get("imageinfo")
        if not title or not isinstance(imageinfo, list) or not imageinfo or not isinstance(imageinfo[0], dict):
            rejected.append({"title": str(raw_title or "(untitled)"), "reason": "Missing image metadata."})
            continue
        info = imageinfo[0]
        try:
            license_id, license_url = _verified_commons_license(info)
        except AssetRightsError as exc:
            rejected.append({"title": title, "reason": str(exc)})
            continue

        creator = (
            _extract_metadata(info, "Artist")
            or _extract_metadata(info, "Creator")
            or "Not stated in Commons metadata"
        )
        origin_url = info.get("descriptionurl")
        download_url = info.get("thumburl") or info.get("url")
        if not isinstance(origin_url, str) or not isinstance(download_url, str):
            rejected.append({"title": title, "reason": "Commons record has no source or image URL."})
            continue
        parsed_origin = urlparse(origin_url)
        if parsed_origin.scheme != "https" or parsed_origin.hostname != "commons.wikimedia.org":
            rejected.append({"title": title, "reason": "Description page is not on Wikimedia Commons."})
            continue
        declared_size = info.get("thumbsize", info.get("size", 0))
        if isinstance(declared_size, int) and declared_size > 25 * 1024 * 1024:
            rejected.append({"title": title, "reason": "Image exceeds the 25 MiB download limit."})
            continue
        try:
            payload = request_bytes(
                download_url, max_bytes=25 * 1024 * 1024, media_download=True
            )
        except WikimediaError as exc:
            rejected.append({"title": title, "reason": str(exc)})
            continue
        extension_by_type = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
        }
        extension = extension_by_type.get(payload.content_type)
        if not extension:
            rejected.append(
                {"title": title, "reason": f"Unsupported image media type: {payload.content_type}."}
            )
            continue
        attribution = (
            f"{creator}, “{title},” Wikimedia Commons, {license_id} "
            f"({license_url}), {origin_url}"
        )
        digest = hashlib.sha256(payload.body).hexdigest()
        final_path = destination_dir / f"{digest}{extension}"
        temporary_path: Path | None = None
        check_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", prefix=".geofactory-", suffix=".tmp",
                dir=destination_dir, delete=False,
            ) as output:
                temporary_path = Path(output.name)
                output.write(payload.body)
                output.flush()
            check_path = temporary_path.with_suffix(extension)
            temporary_path.replace(check_path)
            temporary_path = None
            if not _has_image_signature(check_path):
                check_path.unlink(missing_ok=True)
                check_path = None
                rejected.append({"title": title, "reason": "Downloaded bytes do not match an allowed image signature."})
                continue
            with db.connect() as conn:
                existing = conn.execute(
                    "SELECT * FROM assets WHERE job_id = ? AND file_sha256 = ? LIMIT 1",
                    (job_id, digest),
                ).fetchone()
                if existing:
                    check_path.unlink(missing_ok=True)
                    check_path = None
                    registered.append(dict(existing))
                    if len(registered) >= max_downloads:
                        break
                    continue
                if final_path.exists() and sha256_file(final_path) != digest:
                    raise AssetRightsError("Refusing to overwrite an unrelated file at the content-addressed path.")
                if check_path != final_path:
                    check_path.replace(final_path)
                    check_path = None
                asset_id = f"ast-{uuid.uuid4().hex[:12]}"
                conn.execute(
                    """INSERT INTO assets
                       (id, job_id, title, publisher, path, origin_url, creator, licence,
                        licence_url, attribution, commercial_use, file_sha256, retrieved_at)
                       VALUES (?, ?, ?, 'Wikimedia Commons', ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                    (
                        asset_id, job_id, title, str(final_path), origin_url, creator,
                        license_id, license_url, attribution, digest, access_time,
                    ),
                )
                record = conn.execute(
                    "SELECT * FROM assets WHERE id = ?", (asset_id,)
                ).fetchone()
                registered.append(dict(record))
        except OSError as exc:
            if temporary_path:
                temporary_path.unlink(missing_ok=True)
            if check_path:
                check_path.unlink(missing_ok=True)
            raise AssetRightsError(f"Could not store downloaded Commons image: {exc}") from exc
        except Exception:
            if temporary_path:
                temporary_path.unlink(missing_ok=True)
            if check_path:
                check_path.unlink(missing_ok=True)
            raise
        if len(registered) >= max_downloads:
            break
    return {"assets": registered, "rejected": rejected}


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
            f"- {record['attribution']} — "
            f"[licence]({record.get('licence_url') or record['licence']}) — "
            f"[origin]({record['origin_url']})"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
