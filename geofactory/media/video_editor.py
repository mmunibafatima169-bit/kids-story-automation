"""FFmpeg still-image/narration assembly with safe argument-list invocation."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from geofactory.core.config import FactoryConfig
from geofactory.media.asset_manager import ALLOWED_LICENSES, sha256_file
from geofactory.media.tts_engine import audio_duration_seconds


class RenderError(RuntimeError):
    """Raised when render prerequisites or FFmpeg execution fails."""


def render_video(
    config: FactoryConfig,
    images: list[Path],
    narration: Path,
    destination: Path,
    asset_records: list[dict[str, object]],
) -> Path:
    free_bytes = shutil.disk_usage(config.root).free
    required_bytes = int(config.minimum_free_disk_gb * 1024 ** 3)
    if free_bytes < required_bytes:
        raise RenderError(
            f"Low disk space: {free_bytes / 1024 ** 3:.2f} GiB free; "
            f"configured minimum is {config.minimum_free_disk_gb:.2f} GiB."
        )
    ffmpeg = shutil.which(config.ffmpeg)
    if not ffmpeg:
        raise RenderError(f"FFmpeg executable not found: {config.ffmpeg}")
    if not images:
        raise RenderError("At least one rights-cleared visual asset is required.")
    if not narration.is_file() or narration.stat().st_size == 0:
        raise RenderError("A non-empty narration WAV file is required.")
    records_by_path = {str(record["path"]): record for record in asset_records}
    for image in images:
        resolved = image.resolve(strict=True)
        record = records_by_path.get(str(resolved))
        if record is None or record.get("licence") not in ALLOWED_LICENSES:
            raise RenderError(f"Visual is not registered with an accepted licence: {image}")
        if not record.get("commercial_use") or sha256_file(resolved) != record.get("file_sha256"):
            raise RenderError(f"Visual rights or file hash validation failed: {image}")
    duration = audio_duration_seconds(narration)
    destination.parent.mkdir(parents=True, exist_ok=True)
    inputs: list[str] = []
    filters: list[str] = []
    clip_duration = duration / len(images)
    for index, image in enumerate(images):
        inputs.extend(["-loop", "1", "-t", f"{clip_duration:.3f}", "-i", str(image)])
        filters.append(
            f"[{index}:v]scale={config.width}:{config.height}:force_original_aspect_ratio=decrease,"
            f"pad={config.width}:{config.height}:(ow-iw)/2:(oh-ih)/2,"
            f"setsar=1,fps=30,format=yuv420p[v{index}]"
        )
    audio_index = len(images)
    inputs.extend(["-i", str(narration)])
    joined = "".join(f"[v{index}]" for index in range(len(images)))
    filters.append(f"{joined}concat=n={len(images)}:v=1:a=0[vout]")
    command = [
        ffmpeg, "-y", "-v", "error", *inputs, "-filter_complex", ";".join(filters),
        "-map", "[vout]", "-map", f"{audio_index}:a:0", "-shortest",
        "-c:v", "libx264", "-preset", "medium", "-crf", "22",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
        str(destination),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=7200, check=False)
    if result.returncode:
        raise RenderError(f"FFmpeg render failed ({result.returncode}): {result.stderr[-2000:]}")
    if not destination.is_file() or destination.stat().st_size == 0:
        raise RenderError("FFmpeg completed without producing a video file.")
    return destination


def probe_video(config: FactoryConfig, path: Path) -> dict[str, object]:
    ffprobe = shutil.which(config.ffprobe)
    if not ffprobe:
        raise RenderError(f"FFprobe executable not found: {config.ffprobe}")
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=60, check=False,
    )
    if result.returncode:
        raise RenderError(f"FFprobe failed: {result.stderr[-1000:]}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RenderError("FFprobe returned invalid JSON.") from exc
