# GeoFactory — local-first geography documentary workflow

This repository now contains a Python command-line and local review workflow for preparing evidence-led geography documentary packages. It uses SQLite and the Python standard library, does not need API keys for its offline demo, defaults to loopback-only review, and never enables public publishing. The original `main.py` / `story_generator.py` starter files are preserved as-is and are not part of this workflow.

**Important current boundary:** the offline demo creates a clearly labelled draft package, not a finished documentary. The optional online demo discovers Wikipedia sources and Commons images, but does not assert verified facts, narration, or a rendered video; QA blocks approval. Full editorial verification, current YouTube OAuth upload, automatic scheduling, and causal analytics are not implemented. Do not treat generated material or a passing automated check as editorial/legal review.

## Requirements and quick start

- Python 3.11 or newer. The current environment was assessed at Python 3.14.
- No Python dependencies are required. FFmpeg/FFprobe, Ollama, and Piper are optional and used only when installed/configured.
- The dashboard only binds to `127.0.0.1`, `localhost`, or `::1`.

From the repository root:

```bash
python3 --version
python3 -m geofactory init
python3 -m geofactory doctor
python3 -m geofactory topics
python3 -m geofactory demo
python3 -m geofactory status
python3 -m geofactory serve
```

`init` creates ignored local `config.json`, the SQLite database, and output directory. `demo` writes an offline sample under `output/<job-id>/`; its `qa_failed` state is expected until real evidence, narration, licensed visuals, and a valid render are supplied. The dashboard is at <http://127.0.0.1:8765/> by default. Stop it with Ctrl+C. The scheduler is not started automatically.

Run tests without installing anything:

```bash
python3 -m unittest discover -v
```

For Windows PowerShell, run `.\setup.ps1`, then use `python -m geofactory ...` in the repository directory. The script creates a virtual environment and initializes local config; it does not install optional media tools or enable publishing.

## Building a real package (manual editorial gates)

1. Choose a proposal and create its persistent job:

   ```bash
   python3 -m geofactory topics
   python3 -m geofactory new --topic "Your topic" --question "Your viewer question?"
   python3 -m geofactory demo --topic "Your topic" --question "Your viewer question?"
   ```

   The `demo` command prepares a review scaffold for that idempotent job. It is not a research claim or a completed documentary.

2. Discover source leads from the public English Wikipedia API, then review them:

   ```bash
   python3 -m geofactory research-online JOB_ID --query "Venice lagoon geography" --limit 5
   python3 -m geofactory research JOB_ID --ledger path/to/source_ledger.json
   python3 -m geofactory script JOB_ID
   ```

   `research-online` records exact API article titles, URLs, publisher, access date, text licence, and licence URL. It saves a short article lead as a research lead, not a verified claim; check article citations and use independent sources. Source URLs must be HTTPS. Each factual claim added to a hand-reviewed ledger needs a source ID and a stated licence. With a local Ollama model configured, `script` requests a 1,200–1,800-word evidence-conditioned draft and validates claim IDs/word count. Model output still requires line-by-line human fact-checking. Without it, `script` writes evidence-aware notes, not finished narration.

3. Search and download suitably licensed Wikimedia Commons images:

   ```bash
   python3 -m geofactory commons-search JOB_ID --query "Venice lagoon aerial" --downloads 2
   ```

   Wikimedia requests are rate-limited to no faster than one per second. Downloads are limited to Wikimedia's media host, HTTPS, 25 MiB, and checked image file signatures. Commons licence metadata must explicitly permit commercial reuse (`CC0`, `CC BY`, `CC BY-SA`, or a recognized public-domain mark) and must agree with the official licence URL. Unknown, non-commercial, no-derivatives, missing, or mismatched licence metadata is rejected. Creator/title/attribution, source page, publisher, retrieval time, licence, licence URL, and SHA-256 are stored. This validates the Commons metadata, not the underlying legal accuracy; inspect the item page and the applicable licence yourself.

   Alternatively, register a local image only after personally checking its rights:

   ```bash
   python3 -m geofactory narrate JOB_ID --text-file path/to/reviewed_narration.txt
   python3 -m geofactory asset-add JOB_ID --file path/to/image.jpg \
     --origin-url "https://source.example/image" --creator "Creator" \
     --licence "CC-BY-4.0" --attribution "Creator, title, CC BY 4.0, source URL" \
     --commercial-use
   ```

   `asset-add` only registers an existing image and does not independently verify manual licence assertions. Accepted identifiers include `CC0-1.0`, `CC-BY-2.0/3.0/4.0`, `CC-BY-SA-2.0/3.0/4.0`, and `Public Domain`. Compatibility and legal interpretation still require human review.

4. Install FFmpeg and FFprobe from a trusted package source, then render and review:

   ```bash
   python3 -m geofactory render JOB_ID
   python3 -m geofactory qa JOB_ID
   python3 -m geofactory serve
   ```

   The renderer currently assembles registered still images and WAV narration. It does not create accurate basemaps or animated geography, and it does not yet align subtitle timings to measured speech (SRT timings are estimated). QA requires an 8–15-minute video, 720p or better, audio, SRT, source-linked factual claims, licensed visuals, and attribution. Manual checks for accuracy, map/border labels, black/frozen frames, levels, policy, and title/thumbnail agreement remain mandatory. Fix failures and rerun QA before approval.

5. Approve only a QA-passing package in the dashboard or CLI:

   ```bash
   python3 -m geofactory approve JOB_ID --reviewer "Owner"
   ```

   Approval hashes all files in the exact package (excluding only the approval receipt itself). A detected content change invalidates approval and returns the job to `awaiting_approval`. Upload is not implemented and fails closed; no command can currently publish.

## Configuration and local models

`config.example.json` is the validated template. `config.json` is local and ignored by Git. Paths resolve from its configured root. Publishing must remain disabled; attempts to enable it are rejected. The dashboard refuses non-loopback binds. The optional Ollama URL is also restricted to loopback, and `local_llm_model` must be explicitly configured. The model endpoint is never called unless the script command is configured for it.

Current machine assessment: Ubuntu 24.04, Python 3.14.2, x86_64, approximately 7.8 GiB RAM (roughly 1.1–1.3 GiB available during this phase), no swap, 15+ GiB free disk, and no detected NVIDIA GPU. FFmpeg 6.1.1 and FFprobe are installed. Ollama 0.40.13 is installed and `qwen2.5:0.5b` (397 MB) is downloaded; the ignored local `config.json` selects that configurable model name. **Model inference has not been run**: free memory is tight, CPU-only, and without swap. Do not expect dependable long-script generation or production throughput on this host. No voice model was installed or licence-reviewed. No model is downloaded automatically on other hosts.

Ollama's systemd service is not running in this container. The API server was stopped after the model pull; if you deliberately want to test inference, start `ollama serve` in a separate terminal first. To keep the safe non-LLM script fallback, set `local_llm_model` to an empty string in `config.json`. A tiny model download succeeding does not prove that long-context generation will fit in available RAM.

To build a real-source research package scaffold (still blocked until reviewed narration and a video render are supplied):

```bash
python3 -m geofactory demo-online
```

This live command searches Wikipedia and Commons, records returned metadata, downloads compatible Commons images, exports attribution, and reruns QA. It does not create a finished documentary, fabricate claims, synthesize narration, or bypass human review.

No secrets are needed by the current application. `.env.example` is documentation only; the application does not load `.env`. The starter repository had a tracked `.env`; it was removed from the current Git index while preserving the local file. Older Git commits may still contain its contents, so rotate any credential that may have appeared there and consider repository-history remediation separately.

## Operations, backup, and recovery

- `python3 -m geofactory status` shows persistent jobs; `python3 -m geofactory doctor` checks Python, memory, disk, optional tools, and database health.
- `python3 -m geofactory backup --destination backups/geofactory.sqlite3` creates a consistent SQLite backup. Keep backups private and outside version control.
- Processing is sequential and explicit; nothing runs in the background by default. `python3 -m geofactory work --max-jobs 1` recovers interrupted research/script/render states and processes queued proposals. An interrupted upload is marked failed with an unknown outcome and must be checked with the provider before any retry (upload is disabled in this build). After a dashboard change request, edit the package and run `python3 -m geofactory review-ready JOB_ID`, then rerun QA.
- Generated media, database, config, tokens, logs, caches, and local `.env` are ignored. Do not put credentials in tracked files or command-line arguments.
- `analytics-import` accepts an owner-provided CSV with `views`, `click_through_rate` (fraction 0–1), `watch_time_minutes`, and `subscribers_gained`. Reports are descriptive, not causal.

See [ARCHITECTURE.md](./ARCHITECTURE.md), [ROADMAP.md](./ROADMAP.md), and the labelled fixtures in [examples/](./examples/).
