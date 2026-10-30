"""Fail-closed publishing interface pending OAuth/API implementation."""

from geofactory.core.config import FactoryConfig


class PublishingUnavailable(RuntimeError):
    """Raised rather than claiming that an upload happened."""


def upload_approved_video(config: FactoryConfig, job_id: str) -> None:
    if not config.publishing_enabled:
        raise PublishingUnavailable(
            "YouTube publishing is disabled. This build has no OAuth client; "
            "upload manually only after reviewing and approving the exact package."
        )
    raise PublishingUnavailable(
        "YouTube OAuth upload is not implemented. No upload was attempted."
    )
