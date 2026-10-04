"""Detect & redact secrets before chunks are embedded / stored."""
import json
import logging
import re

logger = logging.getLogger("neuroflow.security")

SECRET_PATTERNS = {
    "private_key": re.compile(
        r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----(?:.*?-----END (?:[A-Z]+ )?PRIVATE KEY-----)?",
        re.DOTALL,
    ),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
    "aws_access_key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "generic_api_key": re.compile(
        r"""['"]?(?:api|secret|token|key|password)['"]?\s*[:=]\s*['"][A-Za-z0-9/+]{20,}['"]""",
        re.IGNORECASE,
    ),
}
REDACTED = "[REDACTED]"


def scan_for_secrets(text: str) -> list[str]:
    return [name for name, rx in SECRET_PATTERNS.items() if rx.search(text)]


def redact_secrets(text: str, document_id: str | None = None) -> tuple[str, list[str]]:
    """Return (redacted_text, [pattern_types]). Logs a structured event per hit."""
    found: list[str] = []
    for name, rx in SECRET_PATTERNS.items():
        if rx.search(text):
            text = rx.sub(REDACTED, text)
            found.append(name)
            logger.warning(
                json.dumps(
                    {"event": "secret_redacted", "document_id": document_id, "pattern_type": name}
                )
            )
    return text, found
