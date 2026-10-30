# Implementation roadmap

Status reflects the code in this repository, not a promise of future work.

| Phase | Dependencies | Completion criteria | Status |
|---|---|---|---|
| 1. Repository/machine assessment and architecture | Existing repository, host diagnostics | Preserve useful starter code; record OS, Python, memory, disk, GPU, FFmpeg, Git, Ollama; document safe plan | Complete. Ubuntu 24.04/Python 3.14.2; low available RAM; no GPU/FFmpeg/Ollama |
| 2. Config, logging, SQLite, queue, CLI | Python 3.11+ | Validated config, schema version, state transitions, idempotency, local diagnostics, tests | Complete for local sequential workflow |
| 3. Topics and source-linked research | SQLite; owner research | Scored/deduplicated proposals; source/claim import; factual claims require source IDs | Partial. Deterministic ideas and manual evidence import; no source discovery/retrieval |
| 4. Scripts, claims, scenes | Research ledger; optional Ollama | Original 1,200–1,800-word draft; chapter/scene plan; every material factual claim traceable | Partial. Ollama script draft is optional; model/claim output needs human verification; no automatic synthesis confidence check |
| 5. TTS and subtitles | Piper plus voice with verified licence | Local narration; measurable audio; recoverable generation; aligned SRT | Partial. Piper invocation and duration exist; no installed/tested voice, chunk recovery, or measured-time subtitle alignment |
| 6. Rights-aware assets and visuals | Owner-verified source/licence | Records, hash/attribution, reusable images/maps/charts | Partial. Manual local registration and original SVG bar chart; no API search/download, maps, or automatic rights verification |
| 7. Video and thumbnail | FFmpeg/FFprobe; usable visuals/audio | Playable 16:9 video, narration, subtitles, original thumbnail | Partial. Still-image + narration assembly and SVG thumbnail exist; FFmpeg unavailable here; no motion effects/transitions or validated complete render |
| 8. QA and approval dashboard | Rendered package | Automated blockers, preview/ledgers, approve/reject/change; exact-version approval | Partial. Local queue and hash-bound approval implemented; full editorial/frame/policy checks remain manual |
| 9. YouTube OAuth publishing | Official API review, credentials, quota/legal review | OAuth, private-first upload, exact approval, explicit visibility, status checks | Not implemented. Publishing is disabled and the client fails closed |
| 10. Scheduling, restart recovery, analytics | Persistent queue and owner analytics | Safe restart, bounded retry, pause/resume, supported analytics | Partial. SQLite state/recovery, manual sequential worker, backup, CSV import; no auto scheduler or OAuth analytics |
| 11. End-to-end sample and failure testing | Optional media tools/models | Complete playable 8–15-minute sample; restart/approval/render tests | Partial. Offline package and safety tests pass; no complete media render possible on assessed host |

## Known blockers and next acceptance work

- Install FFmpeg/FFprobe and licensed Piper voice assets to validate the media toolchain on a capable host.
- Obtain and check real, authoritative source records; implement permitted source discovery only after source-specific terms and rate limits are reviewed.
- Test Ollama with a model that fits actual available RAM; a configured model is not auto-downloaded.
- Implement stronger editorial/factual checks and human review of maps, labels, subtitles, and full-length video.
- Implement YouTube only in a separate change with OAuth consent, official API policy/quota review, private-first semantics, idempotent upload verification, and explicit release approval.
- Add migrations for future schema versions before changing the deployed schema.
- Test interruption during actual FFmpeg rendering on Windows and Linux.

The demo is intentionally not an example of real-world geography; it cannot pass approval without replacing the synthetic placeholders.
