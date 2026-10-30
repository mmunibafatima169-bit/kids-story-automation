"""Optional Piper narration with explicit local voice-licence attestation."""

from __future__ import annotations

import shutil
import subprocess
import wave
from pathlib import Path

from geofactory.core.config import FactoryConfig


class TTSError(RuntimeError):
    """Raised when local speech generation is unavailable or fails."""


def audio_duration_seconds(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as audio:
            if audio.getframerate() <= 0:
                raise TTSError("Audio has an invalid sample rate.")
            return audio.getnframes() / audio.getframerate()
    except (wave.Error, OSError) as exc:
        raise TTSError(f"Cannot read WAV audio {path}: {exc}") from exc


def synthesize(text: str, destination: Path, config: FactoryConfig) -> float:
    if not text.strip():
        raise TTSError("Narration text is empty.")
    if config.tts_backend != "piper":
        raise TTSError(
            "Narration is not configured. Install Piper and a voice with verified terms, "
            "then set tts_backend='piper' and the model/licence in config.json."
        )
    if not config.piper_voice_model or not config.piper_voice_license.strip():
        raise TTSError("Piper voice model and verified licence are required.")
    executable = shutil.which(config.piper_executable)
    if not executable:
        raise TTSError(f"Piper executable not found: {config.piper_executable}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [
        executable, "--model", str(config.piper_voice_model),
        "--output_file", str(destination),
    ]
    try:
        result = subprocess.run(
            command, input=text, text=True, capture_output=True, timeout=600, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise TTSError("Piper exceeded its 10-minute generation timeout.") from exc
    if result.returncode:
        raise TTSError(f"Piper failed ({result.returncode}): {result.stderr[-1000:]}")
    duration = audio_duration_seconds(destination)
    if duration <= 0:
        raise TTSError("Piper produced an empty audio file.")
    return duration


def write_estimated_srt(text: str, destination: Path, words_per_minute: int = 145) -> None:
    """Write clearly estimated timings; final captions should be aligned to measured audio."""
    if words_per_minute < 60 or words_per_minute > 240:
        raise ValueError("words_per_minute must be between 60 and 240.")
    words = text.split()
    if not words:
        raise ValueError("Cannot create subtitles for empty narration.")
    chunks: list[str] = []
    current: list[str] = []
    for word in words:
        if len(word) > 46:
            raise ValueError("A narration token is too long for a readable subtitle line.")
        if current and len(" ".join([*current, word])) > 42:
            chunks.append(" ".join(current))
            current = []
        current.append(word)
    if current:
        chunks.append(" ".join(current))
    seconds_per_word = 60.0 / words_per_minute
    elapsed = 0.0
    entries = []
    for index, chunk in enumerate(chunks, 1):
        duration = max(1.0, len(chunk.split()) * seconds_per_word)
        start, end = elapsed, elapsed + duration
        entries.extend([str(index), f"{_timestamp(start)} --> {_timestamp(end)}", chunk, ""])
        elapsed = end
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(entries), encoding="utf-8")


def _timestamp(seconds: float) -> str:
    millis = round(seconds * 1000)
    hours, remainder = divmod(millis, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{whole_seconds:02},{milliseconds:03}"
