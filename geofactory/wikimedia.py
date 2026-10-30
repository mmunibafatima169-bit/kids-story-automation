"""Rate-limited Wikimedia API and media requests with bounded response sizes."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

USER_AGENT = (
    "GeoFactory/0.1 (local-first educational documentary workflow; "
    "https://github.com/mmunibafatima169-bit/kids-story-automation)"
)
API_URLS = {
    "wikipedia": "https://en.wikipedia.org/w/api.php",
    "commons": "https://commons.wikimedia.org/w/api.php",
}
API_HOSTS = {"en.wikipedia.org", "commons.wikimedia.org"}
MEDIA_HOSTS = {"upload.wikimedia.org", "thumb.wikimedia.org"}
API_RESPONSE_LIMIT = 4 * 1024 * 1024
IMAGE_RESPONSE_LIMIT = 25 * 1024 * 1024
REQUEST_INTERVAL_SECONDS = 1.0
_request_lock = threading.Lock()
_last_request_at = 0.0


class WikimediaError(RuntimeError):
    """Raised when a Wikimedia request fails or violates request bounds."""


@dataclass(frozen=True)
class HttpPayload:
    body: bytes
    content_type: str
    final_url: str


def _validate_url(url: str, allowed_hosts: set[str]) -> None:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in allowed_hosts
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
    ):
        raise WikimediaError("Refusing a non-Wikimedia or non-HTTPS URL.")


def _wait_for_rate_limit() -> None:
    global _last_request_at
    with _request_lock:
        now = time.monotonic()
        delay = REQUEST_INTERVAL_SECONDS - (now - _last_request_at)
        if delay > 0:
            time.sleep(delay)
        _last_request_at = time.monotonic()


def request_bytes(
    url: str,
    params: dict[str, str | int] | None = None,
    *,
    max_bytes: int = API_RESPONSE_LIMIT,
    timeout: int = 30,
    media_download: bool = False,
) -> HttpPayload:
    allowed_hosts = MEDIA_HOSTS if media_download else API_HOSTS
    _validate_url(url, allowed_hosts)
    target = f"{url}?{urlencode(params)}" if params else url
    request = Request(target, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    _wait_for_rate_limit()
    try:
        with urlopen(request, timeout=timeout) as response:
            final_url = response.geturl()
            _validate_url(final_url, allowed_hosts)
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise WikimediaError(f"Wikimedia response exceeded the {max_bytes}-byte limit.")
            content_type = response.headers.get_content_type().lower()
    except HTTPError as exc:
        raise WikimediaError(f"Wikimedia returned HTTP {exc.code} for {url}.") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise WikimediaError(f"Wikimedia request failed for {url}: {exc}") from exc
    if not body:
        raise WikimediaError(f"Wikimedia returned an empty response for {url}.")
    return HttpPayload(body=body, content_type=content_type, final_url=final_url)


def request_json(endpoint: str, params: dict[str, str | int]) -> dict[str, object]:
    if endpoint not in API_URLS:
        raise WikimediaError(f"Unknown Wikimedia API endpoint: {endpoint}")
    response = request_bytes(API_URLS[endpoint], params)
    if response.content_type not in {"application/json", "text/json", "text/plain"}:
        raise WikimediaError(f"Wikimedia API returned unexpected content type: {response.content_type}")
    try:
        result = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WikimediaError("Wikimedia API returned malformed JSON.") from exc
    if not isinstance(result, dict):
        raise WikimediaError("Wikimedia API response must be a JSON object.")
    if "error" in result:
        error = result["error"]
        if isinstance(error, dict):
            raise WikimediaError(
                f"Wikimedia API error {error.get('code', 'unknown')}: "
                f"{error.get('info', 'no details')}"
            )
        raise WikimediaError("Wikimedia API returned an error.")
    return result
