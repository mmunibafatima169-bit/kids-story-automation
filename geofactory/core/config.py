"""Validated configuration with safe, local-only defaults."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


class ConfigurationError(ValueError):
    """Raised when a configuration value is unsafe or invalid."""


@dataclass(frozen=True)
class FactoryConfig:
    root: Path
    database_path: Path
    output_dir: Path
    bind_host: str = "127.0.0.1"
    dashboard_port: int = 8765
    require_topic_approval: bool = True
    dry_run: bool = True
    publishing_enabled: bool = False
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    tts_backend: str = "none"
    piper_executable: str = "piper"
    piper_voice_model: Path | None = None
    piper_voice_license: str = ""
    local_llm_url: str = "http://127.0.0.1:11434"
    local_llm_model: str = ""
    minimum_free_disk_gb: float = 2.0
    width: int = 1920
    height: int = 1080


def _resolve_path(root: Path, value: str | None, fallback: str) -> Path:
    path = Path(value or fallback).expanduser()
    return path if path.is_absolute() else (root / path).resolve()


def load_config(path: Path | str | None = None) -> FactoryConfig:
    """Load JSON configuration; selected overrides may be supplied by environment."""
    config_path = Path(path).expanduser().resolve() if path else Path.cwd() / "config.json"
    raw: dict[str, Any] = {}
    if config_path.is_file():
        try:
            loaded = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigurationError(f"Cannot read configuration {config_path}: {exc}") from exc
        if not isinstance(loaded, dict):
            raise ConfigurationError("Configuration root must be a JSON object.")
        raw = loaded

    root_value = Path(raw.get("root", config_path.parent)).expanduser()
    root = root_value if root_value.is_absolute() else (config_path.parent / root_value).resolve()
    env_values = {
        "publishing_enabled": os.environ.get("GEOFACTORY_PUBLISHING_ENABLED"),
        "dashboard_port": os.environ.get("GEOFACTORY_DASHBOARD_PORT"),
        "local_llm_model": os.environ.get("GEOFACTORY_LOCAL_LLM_MODEL"),
    }
    for key, value in env_values.items():
        if value is not None:
            raw[key] = value
    try:
        cfg = FactoryConfig(
            root=root,
            database_path=_resolve_path(root, raw.get("database_path"), "data/geofactory.sqlite3"),
            output_dir=_resolve_path(root, raw.get("output_dir"), "output"),
            bind_host=str(raw.get("bind_host", "127.0.0.1")),
            dashboard_port=int(raw.get("dashboard_port", 8765)),
            require_topic_approval=_as_bool(raw.get("require_topic_approval", True)),
            dry_run=_as_bool(raw.get("dry_run", True)),
            publishing_enabled=_as_bool(raw.get("publishing_enabled", False)),
            ffmpeg=str(raw.get("ffmpeg", "ffmpeg")),
            ffprobe=str(raw.get("ffprobe", "ffprobe")),
            tts_backend=str(raw.get("tts_backend", "none")).lower(),
            piper_executable=str(raw.get("piper_executable", "piper")),
            piper_voice_model=(
                _resolve_path(root, raw["piper_voice_model"], "")
                if raw.get("piper_voice_model")
                else None
            ),
            piper_voice_license=str(raw.get("piper_voice_license", "")),
            local_llm_url=str(raw.get("local_llm_url", "http://127.0.0.1:11434")).rstrip("/"),
            local_llm_model=str(raw.get("local_llm_model", "")),
            minimum_free_disk_gb=float(raw.get("minimum_free_disk_gb", 2.0)),
            width=int(raw.get("width", 1920)),
            height=int(raw.get("height", 1080)),
        )
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"Invalid configuration value: {exc}") from exc
    validate_config(cfg)
    return cfg


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in {"true", "1", "yes"}:
        return True
    if isinstance(value, str) and value.lower() in {"false", "0", "no"}:
        return False
    raise ValueError(f"Expected a boolean, got {value!r}.")


def validate_config(cfg: FactoryConfig) -> None:
    if not 1024 <= cfg.dashboard_port <= 65535:
        raise ConfigurationError("dashboard_port must be between 1024 and 65535.")
    if cfg.bind_host not in {"127.0.0.1", "localhost", "::1"}:
        raise ConfigurationError(
            "The dashboard must bind to loopback; remote access is intentionally unsupported."
        )
    if cfg.tts_backend not in {"none", "piper"}:
        raise ConfigurationError("tts_backend must be 'none' or 'piper'.")
    if cfg.tts_backend == "piper":
        if not cfg.piper_voice_model or not cfg.piper_voice_model.is_file():
            raise ConfigurationError("Piper needs an existing piper_voice_model path.")
        if not cfg.piper_voice_license.strip():
            raise ConfigurationError(
                "Record the voice model's verified licence in piper_voice_license before use."
            )
    if cfg.minimum_free_disk_gb < 0:
        raise ConfigurationError("minimum_free_disk_gb cannot be negative.")
    llm_url = urlparse(cfg.local_llm_url)
    if llm_url.scheme != "http" or llm_url.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ConfigurationError("The local LLM URL must be plain HTTP on loopback only.")
    if cfg.width < 320 or cfg.height < 180 or cfg.width % 2 or cfg.height % 2:
        raise ConfigurationError("Video dimensions must be even and at least 320x180.")
    if cfg.publishing_enabled:
        raise ConfigurationError(
            "Publishing is not implemented in this build; leave publishing_enabled false."
        )
