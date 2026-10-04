"""Exception hierarchy for the NeuroFlow SDK."""
from __future__ import annotations


class NeuroFlowError(Exception):
    """Base class for all SDK errors."""


class APIError(NeuroFlowError):
    """The API returned an error response."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"HTTP {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class AuthenticationError(APIError):
    """401/403: missing or invalid API key."""


class NotFoundError(APIError):
    """404: resource does not exist."""


class RateLimitError(APIError):
    """429 persisted after all retries were exhausted."""


class IngestionError(NeuroFlowError):
    """Ingestion finished in a failed state."""


class NeuroFlowTimeout(NeuroFlowError):
    """Polling for a result timed out."""
