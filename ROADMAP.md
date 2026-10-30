# Implementation roadmap

Status reflects the code in this repository, not a promise of future work.

| Phase | Dependencies | Completion criteria | Status |
|---|---|---|---|
| 1. Repository/machine assessment and architecture | Existing repository, host diagnostics | Preserve useful starter code; record OS, Python, memory, disk, GPU, FFmpeg, Git, Ollama; document safe plan | Complete. Ubuntu 24.04/Python 3.14.2; low available RAM; no GPU/FFmpeg/Ollama |
| 2. Config, logging, SQLite, queue, CLI | Python 3.11+ | Validated config, schema version, state transitions, idempotency, local diagnostics, tests | Complete for local sequential workflow |
| 3. Topics and source-linked research | SQLite; public Wikipedia API; owner review | Scored/deduplicated proposals; source/claim import; factual claims require source IDs | Partial. Wikipedia API discovery and licence/date recording work; independent-source verification, broader trusted-source discovery, and claim fact-checking remain manual |
| 4. Scripts, claims, scenes | Research ledger; optional Ollama | Original 1,200–1,800-word draft; chapter/scene plan; every material factual claim traceable | Partial. Ollama script draft is optional; model/claim output needs human verification; no automatic synthesis confidence check |
| 5. TTS and subtitles | Piper plus voice with verified licence | Local narration; measurable audio; recoverable generation; aligned SRT | Partial. Piper invocation and duration exist; no installed/tested voice, chunk recovery, or measured-time subtitle alignment |
| 6. Rights-aware assets and visuals | Commons API; owner review | Records, hash/attribution, reusable images/maps/charts | Partial. Commons search/download and licence metadata gates, manual local registration, and original SVG chart; no authoritative maps or independent legal verification |
| 7. Video and thumbnail | FFmpeg/FFprobe; usable visuals/audio | Playable 16:9 video, narration, subtitles, original thumbnail | Partial. FFmpeg 6.1.1 installed; still-image + narration assembly exists. No Piper voice here, so no complete render; no motion effects/transitions or validated complete render |
| 8. QA and approval dashboard | Rendered package | Automated blockers, preview/ledgers, approve/reject/change; exact-version approval | Partial. Local queue and hash-bound approval implemented; full editorial/frame/policy checks remain manual |
| 9. YouTube OAuth publishing | Official API review, credentials, quota/legal review | OAuth, private-first upload, exact approval, explicit visibility, status checks | Not implemented. Publishing is disabled and the client fails closed |
| 10. Scheduling, restart recovery, analytics | Persistent queue and owner analytics | Safe restart, bounded retry, pause/resume, supported analytics | Partial. SQLite state/recovery, manual sequential worker, backup, CSV import; no auto scheduler or OAuth analytics |
| 11. End-to-end sample and failure testing | Optional media tools/models; Wikimedia APIs | Complete playable 8–15-minute sample; restart/approval/render tests | Partial. Live Wikipedia research and two licensed Commons downloads completed; QA correctly blocks the unfinished package because narration/video are absent. Automated tests cover API parsing/rights rejection; Ollama inference and full-duration render remain unverified |

## Known blockers and next acceptance work

- Install a properly licensed Piper voice to validate full media rendering. FFmpeg/FFprobe are installed.
- Add independent authoritative sources beyond Wikipedia and validate source facts against their citations.
- Ollama and `qwen2.5:0.5b` are installed/downloaded, but at about 1.2 GiB available memory and no swap, inference was not run and remains unverified.
- Implement stronger editorial/factual checks and human review of maps, labels, subtitles, and full-length video.
- Implement YouTube only in a separate change with OAuth consent, official API policy/quota review, private-first semantics, idempotent upload verification, and explicit release approval.
- Add migrations for future schema versions before changing the deployed schema.
- Test interruption during actual FFmpeg rendering on Windows and Linux.

The demo is intentionally not an example of real-world geography; it cannot pass approval without replacing the synthetic placeholders.
