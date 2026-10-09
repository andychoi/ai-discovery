"""Secret redaction for anything persisted or printed (roadmap item M0.8).

Two tools:
  * ``redact_mapping`` — recursively blank values whose *key* names a secret
    (``api_key``, ``token``, ``secret``, ``password`` ...). Used before a config
    is serialized into the DB.
  * ``redact_text`` — mask secret-looking *values* inside free text (URL
    credentials, bearer tokens, ``key=value`` pairs, well-known key prefixes).
    Always applied **before** truncation, so a truncated message can never
    keep the head of a secret.
"""

from __future__ import annotations

import re
from typing import Any

MASK = "***"

_SECRET_KEY_RE = re.compile(r"(api[_-]?key|key$|token|secret|password|passwd|credential|auth)", re.I)

_TEXT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # scheme://user:secret@host  or  scheme://secret@host
    (re.compile(r"(?P<p>[a-z][a-z0-9+.-]*://)[^/\s:@]+(?::[^/\s@]*)?@", re.I), r"\g<p>" + MASK + "@"),
    # Authorization: Bearer xxx / Basic xxx
    (re.compile(r"(?P<p>\b(?:bearer|basic)\s+)[A-Za-z0-9._~+/=-]{8,}", re.I), r"\g<p>" + MASK),
    # key=value / key: value for secret-ish keys
    (re.compile(r"(?P<p>\b[\w.-]*(?:api[_-]?key|token|secret|password|passwd)[\w.-]*\s*[=:]\s*[\"']?)[^\s\"',;]+", re.I),
     r"\g<p>" + MASK),
    # Well-known key shapes
    (re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}\b"), MASK),          # OpenAI/Anthropic-style
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), MASK),                          # AWS access key id
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), MASK),                # GitHub tokens
    (re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"), MASK),                    # Google API keys
)


def is_secret_key(key: str) -> bool:
    return bool(_SECRET_KEY_RE.search(key))


def redact_mapping(obj: Any) -> Any:
    """Return a copy of *obj* with secret-named values replaced by ``MASK``.

    Empty/None secret values are kept as-is (nothing to hide, and it records
    that the setting was unset).
    """
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(k, str) and is_secret_key(k) and isinstance(v, (str, bytes)) and v:
                out[k] = MASK
            else:
                out[k] = redact_mapping(v)
        return out
    if isinstance(obj, list):
        return [redact_mapping(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(redact_mapping(v) for v in obj)
    if isinstance(obj, str):
        return redact_text(obj)
    return obj


def redact_text(text: str) -> str:
    """Mask secret-looking substrings in free text."""
    if not text:
        return text
    for pattern, repl in _TEXT_PATTERNS:
        text = pattern.sub(repl, text)
    return text


def safe_message(text: str, limit: int = 500) -> str:
    """Redact, then truncate (never the other way round)."""
    return redact_text(str(text))[:limit]
