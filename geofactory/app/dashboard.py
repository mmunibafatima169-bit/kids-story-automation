"""Human review dashboard with hash-bound approvals and a per-process CSRF token."""

from __future__ import annotations

import html
import json
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from geofactory.core.config import FactoryConfig
from geofactory.core.database import Database
from geofactory.core.jobs import (
    approval_is_current, get_job, list_jobs, package_digest, record_approval, transition_job,
)
from geofactory.quality.qa_engine import assert_approvable, run_qa

SAFE_ARTIFACTS = {
    "final.mp4", "script.md", "narration.txt", "narration.wav", "subtitles.srt",
    "thumbnail.svg", "thumbnail.png", "metadata.json", "source_ledger.json",
    "asset_ledger.json", "attribution.md", "qa_report.md", "qa_report.json",
}


def create_handler(db: Database, config: FactoryConfig, token: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "GeoFactoryReview/0.1"

        def _send(self, status: int, body: str, content_type: str = "text/html; charset=utf-8") -> None:
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self' 'unsafe-inline'; media-src 'self'; frame-ancestors 'none';")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            pieces = [unquote(part) for part in parsed.path.strip("/").split("/") if part]
            if not pieces:
                self._send(200, self._index())
                return
            if len(pieces) == 3 and pieces[0] == "artifact":
                job_id, name = pieces[1], pieces[2]
                if name not in SAFE_ARTIFACTS:
                    self._send(404, "Not found")
                    return
                try:
                    job = get_job(db, job_id)
                    package = Path(job["output_dir"]).resolve()
                    path = (package / name).resolve()
                    if package not in path.parents or not path.is_file():
                        self._send(404, "Not found")
                        return
                except (KeyError, OSError):
                    self._send(404, "Not found")
                    return
                content_type = "video/mp4" if name.endswith(".mp4") else (
                    "image/svg+xml" if name.endswith(".svg") else "application/octet-stream"
                )
                data = path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
                return
            if len(pieces) == 2 and pieces[0] == "job":
                try:
                    self._send(200, self._job_page(pieces[1]))
                except (KeyError, OSError, ValueError):
                    self._send(404, "Job not found or package integrity check failed.")
                return
            self._send(404, "Not found")

        def do_POST(self) -> None:
            pieces = [unquote(part) for part in urlparse(self.path).path.strip("/").split("/") if part]
            if len(pieces) != 4 or pieces[0] != "api" or pieces[1] != "jobs":
                self._send(404, json.dumps({"error": "Not found"}), "application/json")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 1 or length > 16_384:
                    raise ValueError("Request body must be between 1 and 16384 bytes.")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError("JSON request body must be an object.")
                if not secrets.compare_digest(str(payload.get("token", "")), token):
                    self._send(403, json.dumps({"error": "Invalid session token."}), "application/json")
                    return
                result = self._action(pieces[2], pieces[3], payload)
                self._send(200, json.dumps(result), "application/json")
            except (ValueError, KeyError, RuntimeError, OSError, json.JSONDecodeError) as exc:
                self._send(400, json.dumps({"error": str(exc)}), "application/json")

        def _action(self, job_id: str, action: str, payload: dict[str, Any]) -> dict[str, str]:
            job = get_job(db, job_id)
            note = str(payload.get("note", "")).strip()
            reviewer = str(payload.get("reviewer", "local owner")).strip() or "local owner"
            if action == "approve":
                if job["status"] != "awaiting_approval":
                    raise ValueError("Job is not awaiting approval.")
                package = Path(job["output_dir"])
                report = run_qa(db, job_id, config)
                if report["approval_blocked"]:
                    raise ValueError("A fresh QA run found critical failures; approval is blocked.")
                assert_approvable(job, package)
                digest = package_digest(package)
                approval_id = record_approval(db, job_id, reviewer, note, digest)
                (package / "approval.json").write_text(
                    json.dumps(
                        {
                            "approval_id": approval_id,
                            "reviewer": reviewer,
                            "note": note,
                            "package_sha256": digest,
                            "decision": "approved",
                        },
                        indent=2,
                    ) + "\n",
                    encoding="utf-8",
                )
                return {"status": "approved", "package_sha256": digest}
            if action == "reject":
                transition_job(db, job_id, "rejected", note or "Rejected by reviewer.")
                return {"status": "rejected"}
            if action == "request-changes":
                if job["status"] != "awaiting_approval":
                    raise ValueError("Changes can only be requested while awaiting approval.")
                transition_job(db, job_id, "scripting", note or "Reviewer requested changes.")
                return {"status": "scripting"}
            raise ValueError("Unknown review action.")

        def _index(self) -> str:
            rows = list_jobs(db)
            rendered = "".join(
                f'<tr><td><a href="/job/{html.escape(row["id"])}">{html.escape(row["topic"])}</a></td>'
                f'<td>{html.escape(row["status"])}</td><td>{html.escape(row["created_at"])}</td></tr>'
                for row in rows
            ) or '<tr><td colspan="3">No jobs yet. Run <code>python -m geofactory demo</code>.</td></tr>'
            return _page(
                "Review queue",
                f"<p>Local review only. Publishing is disabled.</p><table><tr><th>Topic</th><th>Status</th><th>Created</th></tr>{rendered}</table>",
            )

        def _job_page(self, job_id: str) -> str:
            job = get_job(db, job_id)
            if job["status"] == "approved":
                approval_is_current(db, job_id, Path(job["output_dir"]))
                job = get_job(db, job_id)
            package = Path(job["output_dir"])
            files = sorted(path.name for path in package.iterdir() if path.is_file())
            links = "".join(
                f'<li><a href="/artifact/{html.escape(job_id)}/{html.escape(name)}">{html.escape(name)}</a></li>'
                for name in files if name in SAFE_ARTIFACTS
            )
            video = (
                f'<video controls width="800" src="/artifact/{html.escape(job_id)}/final.mp4"></video>'
                if "final.mp4" in files else "<p>No final video yet.</p>"
            )
            thumbnail = (
                f'<img width="640" alt="Draft thumbnail" src="/artifact/{html.escape(job_id)}/thumbnail.svg">'
                if "thumbnail.svg" in files else ""
            )
            actions = ""
            if job["status"] == "awaiting_approval":
                actions = f"""
<label>Reviewer <input id="reviewer" value="local owner"></label>
<label>Note <input id="note"></label>
<button onclick="act('approve')">Approve exact package</button>
<button onclick="act('request-changes')">Request changes</button>
<button onclick="act('reject')">Reject</button>
<p id="result"></p>
<script>
const token={json.dumps(token)};
async function act(action) {{
 const response=await fetch('/api/jobs/{html.escape(job_id)}/'+action,{{method:'POST',
  headers:{{'Content-Type':'application/json'}},
  body:JSON.stringify({{token,reviewer:document.getElementById('reviewer').value,
   note:document.getElementById('note').value}})}});
 const result=await response.json();
 document.getElementById('result').textContent=JSON.stringify(result);
 if(response.ok) location.reload();
}}
</script>"""
            qa = ""
            qa_path = package / "qa_report.md"
            if qa_path.is_file():
                qa = f"<h2>QA report</h2><pre>{html.escape(qa_path.read_text(encoding='utf-8'))}</pre>"
            return _page(
                html.escape(job["topic"]),
                f"<p><strong>Status:</strong> {html.escape(job['status'])}</p>"
                f"<p><strong>Question:</strong> {html.escape(job['question'])}</p>{video}{thumbnail}"
                f"<h2>Package files</h2><ul>{links}</ul>{qa}{actions}",
            )

        def log_message(self, format_string: str, *args: object) -> None:
            return

    return Handler


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang=\"en\"><meta charset=\"utf-8\">"
        f"<title>{title} — GeoFactory</title>"
        "<style>body{font:16px system-ui;max-width:1000px;margin:2rem auto;padding:0 1rem;color:#17252e}"
        "table{border-collapse:collapse;width:100%}td,th{padding:.6rem;border-bottom:1px solid #ccd5da}"
        "button{padding:.7rem;margin:.5rem}pre{white-space:pre-wrap;background:#f0f3f5;padding:1rem}</style>"
        f"<h1>{title}</h1><p><a href=\"/\">Queue</a></p>{body}</html>"
    )


def serve(db: Database, config: FactoryConfig) -> None:
    token = secrets.token_urlsafe(32)
    server = ThreadingHTTPServer(
        (config.bind_host, config.dashboard_port), create_handler(db, config, token)
    )
    print(f"Review dashboard: http://{config.bind_host}:{config.dashboard_port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
    finally:
        server.server_close()
