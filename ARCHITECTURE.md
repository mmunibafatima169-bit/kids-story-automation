# Architecture and data flow

## Runtime shape

The project is a dependency-free Python package (`geofactory`) with a CLI, a loopback-only HTTP review dashboard, SQLite persistence, and optional external executables. `python -m geofactory` is the entry point. The pre-existing story-generation files remain untouched and separate.

```text
CLI / loopback dashboard
          |
          +-- core/config.py (validated safe defaults)
          +-- core/database.py (SQLite schema/version)
          +-- core/jobs.py (idempotency, transitions, recovery, approval hash)
          |
          +-- agents/topic_agent.py (deterministic proposals/history scoring)
          +-- agents/research_agent.py (Wikipedia API search, owner-reviewed ledger import/export)
          +-- agents/script_agent.py (source-aware notes; optional Ollama draft)
          +-- media/asset_manager.py (Commons API search/download, licence records/attribution)
          +-- wikimedia.py (shared HTTPS API client, User-Agent, rate limit, bounded downloads)
          +-- media/tts_engine.py (optional Piper WAV + estimated SRT)
          +-- media/visual_builder.py (original labelled SVG charts)
          +-- media/video_editor.py (FFmpeg stills + narration assembly)
          +-- media/thumbnail_maker.py (original SVG, optional raster conversion)
          +-- quality/qa_engine.py (technical/evidence/rights release gates)
          +-- scheduler/worker.py (sequential offline package and recovery)
          +-- analytics/feedback.py (owner CSV import, descriptive summary)
          +-- publishing/youtube_client.py (deliberately fail-closed)
```

## Persistent model

The database has a schema version (`PRAGMA user_version`) and tables for jobs, topic proposals, job events, research sources, claims, local assets, approvals, upload attempts, and imported analytics. Foreign keys are enabled. Job transitions are checked centrally. Job creation uses a unique idempotency key. SQLite transactions protect state changes and ledger imports.

Output files are human-editable and stored under `output/<job-id>/`. Approval hashes sorted relative paths and file bytes, excluding only `approval.json`. The database stores the approval digest and the receipt stores the same digest. CLI status, dashboard review, and any upload gate check the digest; a mismatch invalidates the approval. Public publishing is unavailable in this build.

## Workflow boundaries

1. Topic proposals are local scoring seeds, saved for review; they make no factual availability claims.
2. Research leads can be discovered through the public English Wikipedia API. Source title, page URL, publisher, retrieval date, evidence excerpt, and CC BY-SA 4.0 text licence are recorded. API discovery is not fact verification; inspect the article's citations and use independent sources. Hand-imported sources must also state their licence. No general-purpose web scraping is performed.
3. Script notes are evidence-aware. If Ollama and a model are explicitly configured, a local-only request drafts narration and validates word count and IDs. Model prose is not proof; a human must verify every claim.
4. Narration uses Piper only with an existing voice path and a non-empty owner-recorded licence. The program cannot validate external voice-model terms.
5. Commons images can be searched and downloaded through the public Wikimedia Commons API. The client identifies itself, serializes requests at one per second, bounds responses/downloads, uses HTTPS, and only fetches from approved Wikimedia media hosts. Commons licence name/URL pairs are checked against the supported CC0, CC BY, CC BY-SA, and public-domain-mark forms; unknown, NC, ND, missing, or mismatched terms are rejected. Creator/title/source/publisher/retrieval time/licence/licence URL/attribution/hash are saved. The metadata is not a legal guarantee; the owner reviews item pages and terms. Manually supplied assets remain available separately.
6. FFmpeg assembles rights-cleared stills and narration. It does not currently generate authoritative maps, motion graphics, transitions, or audio-aligned subtitles.
7. QA blocks approval on missing render/audio/subtitles, out-of-range duration/resolution, unsupported claims, unknown asset rights, missing attribution, or incomplete package files. Automated QA cannot check every frame, fact, legal right, or platform policy.
8. The dashboard exposes an exact-package approval action with a process-local anti-CSRF token. Approval requires fresh QA and is never inferred from a default or prior artifact.
9. Upload integration is intentionally unavailable. The CLI refuses upload even with a configuration change; OAuth, private-first API upload, schedule/publication workflows, quota handling, and remote status verification need a separate reviewed implementation.
10. CSV analytics are imported from the owner. Reports summarize observations and do not claim causation.

## Recovery and failure behavior

Transient worker states are visible in SQLite. Recovery requeues interrupted local research/script/render steps with incremented attempts. Once the retry limit is reached, the job is failed with an explanation. An interrupted upload has an ambiguous external outcome, so it is never blindly retried. Low disk, missing FFmpeg/Piper/Ollama, invalid rights, invalid URLs, and failed QA surface explicit errors or blocking QA findings.

## Security and privacy

- The dashboard and local model endpoint are restricted to loopback.
- No `.env` parser exists; secrets are not needed by supported paths.
- `config.json`, media, database, backups, credentials, and logs are ignored.
- subprocesses use argument arrays, not a shell.
- Rights/source URLs must be HTTPS. Only documented public Wikimedia APIs and media hosts are retrieved; no rate-limit or access restrictions are bypassed.
- Approval is content-hash-bound; changed deliverables require renewed approval.
- Never enable external access through a reverse proxy without a separate threat review.
