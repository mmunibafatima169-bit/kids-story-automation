"""Loopback-only Ollama client for optional evidence-conditioned generation."""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from geofactory.core.config import FactoryConfig


class LocalModelError(RuntimeError):
    """Raised when the configured local model is unavailable or malformed."""


def generate_json(config: FactoryConfig, prompt: str, timeout: int = 600) -> dict[str, object]:
    if not config.local_llm_model.strip():
        raise LocalModelError("Set local_llm_model in config.json to use local model generation.")
    body = json.dumps(
        {
            "model": config.local_llm_model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
        }
    ).encode("utf-8")
    request = Request(
        f"{config.local_llm_url}/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            response_body = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise LocalModelError(
            f"Local model HTTP error {exc.code}; check Ollama and configured model."
        ) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise LocalModelError(
            f"Could not connect to Ollama at {config.local_llm_url}: {exc}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise LocalModelError("Ollama returned invalid JSON.") from exc
    response_text = response_body.get("response")
    if not isinstance(response_text, str):
        raise LocalModelError("Ollama response did not contain generated text.")
    try:
        parsed = json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise LocalModelError("Generated response is not valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise LocalModelError("Generated JSON must be an object.")
    return parsed
