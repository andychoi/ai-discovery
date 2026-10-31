"""Mask secrets in source text before it leaves the machine (roadmap item M1.13).

Code-aware and **high-confidence only**: a pattern fires on a known credential
shape (private-key block, provider key prefix, JWT, URL credentials, a password
in a connection string) or on a string *literal* assigned to a secret-named
identifier. It never touches identifiers, calls or placeholders, so
``token = getToken()`` and ``password: ${DB_PASSWORD}`` reach the model intact —
the code still reads correctly, only the secret value is replaced by
``<redacted:KIND>``.

Pure functions, standard library only: the MCP layer (M5.12) reuses
``mask_secrets`` for source it serves. ``core/redact.py`` stays the tool for log
and persistence text, where its looser ``key=value`` rule is appropriate.
"""

from __future__ import annotations

import re
import threading
from collections import Counter
from typing import Callable, Union

Repl = Union[str, Callable[["re.Match[str]"], str]]


def _tag(kind: str) -> str:
    return f"<redacted:{kind}>"


# The keyword must end the word (camelCase or not): `authToken`, `DB_PASSWORD`, `client_secret` match;
# `tokenizer`, `tokenType`, `passwordHint`, `secretary` do not.
_SECRET_NAME = r"(?:[A-Za-z0-9_.-]*?(?:password|passwd|pwd|secret|api[_-]?key|apikey|access[_-]?key|token)" \
               r"(?![a-z])[A-Za-z0-9_.-]*)"
# A literal that is not a placeholder: no ${...}, %X%, {{...}}, <...>, $(...), and not obviously empty/dummy.
_PLACEHOLDER = re.compile(r"^\s*$|^\$\{.*\}$|^%[^%]+%$|^\{\{.*\}\}$|^<[^>]*>$|^\$\(.*\)$|^(?:x+|\*+|changeme|"
                          r"password|secret|your[_-].*|example.*|dummy|test|null|none|true|false|bearer|basic)$", re.I)


def _literal(kind: str, group: str = "v") -> Callable[["re.Match[str]"], str]:
    def repl(m: "re.Match[str]") -> str:
        value = m.group(group)
        if len(value) < 6 or _PLACEHOLDER.match(value):
            return m.group(0)
        start, end = m.span(group)
        base = m.start(0)
        return m.group(0)[:start - base] + _tag(kind) + m.group(0)[end - base:]
    return repl


# (kind, pattern, replacement) — applied in order; earlier, more specific shapes first.
_PATTERNS: tuple[tuple[str, "re.Pattern[str]", Repl], ...] = (
    ("private_key", re.compile(r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----.*?-----END (?:[A-Z0-9 ]+ )?PRIVATE KEY-----",
                               re.S), _tag("private_key")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), _tag("aws_access_key")),
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b"),
     _tag("github_token")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"), _tag("slack_token")),
    ("stripe_key", re.compile(r"\b(?:sk|rk)_live_[A-Za-z0-9]{16,}\b"), _tag("stripe_key")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), _tag("google_api_key")),
    ("llm_api_key", re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{24,}\b"), _tag("llm_api_key")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"), _tag("jwt")),
    # scheme://user:password@host — keep the user, mask the password
    ("url_credentials", re.compile(r"(?P<pre>\b[a-z][a-z0-9+.-]*://[^/\s:@'\"]+:)(?P<v>[^/\s@'\"]+)(?=@)", re.I),
     _literal("url_password")),
    # ADO.NET / ODBC / JDBC property style:  Password=...;  Pwd=...;
    ("connection_string", re.compile(r"(?P<pre>\b(?:password|pwd)\s*=\s*)(?P<v>[^;'\"\s<>]+)", re.I),
     _literal("connection_password")),
    # secret-named identifier assigned a quoted literal:  password = "…", "apiKey": '…', clientSecret: "…"
    ("assigned_literal", re.compile(
        r"(?P<pre>[\"']?\b" + _SECRET_NAME + r"[\"']?\s*(?::=|=>|[=:])\s*(?P<q>[\"'`]))(?P<v>[^\"'`\n]{1,512})(?P=q)",
        re.I), _literal("secret")),
    # XML config: <add key="ApiKey" value="…"/>  /  <property name="password" value="…"/>
    ("xml_setting", re.compile(
        r"(?P<pre>(?:key|name)\s*=\s*\"" + _SECRET_NAME + r"\"\s+value\s*=\s*\")(?P<v>[^\"]{1,512})\"", re.I),
     _literal("secret")),
    # Authorization: Bearer <token>
    ("bearer", re.compile(r"(?P<pre>\bBearer\s+)(?P<v>[A-Za-z0-9._~+/=-]{20,})"), _literal("bearer_token")),
)

_lock = threading.Lock()
_totals: Counter = Counter()


def mask_secrets(text: str) -> tuple[str, int]:
    """Return (masked text, number of secrets masked). Idempotent; never raises on odd input."""
    if not text:
        return text, 0
    count = 0
    for kind, pattern, repl in _PATTERNS:
        def counting(m: "re.Match[str]", repl=repl) -> str:
            nonlocal count
            out = repl(m) if callable(repl) else repl
            if out != m.group(0):
                count += 1
            return out
        text = pattern.sub(counting, text)
    return text, count


def record(n: int) -> None:
    """Add *n* masked secrets to the process-wide counter (see ``masked_total``)."""
    if n:
        with _lock:
            _totals["masked"] += n


def mask_and_count(text: str) -> str:
    """``mask_secrets`` that also adds to the process-wide counter."""
    masked, n = mask_secrets(text)
    record(n)
    return masked


def masked_total() -> int:
    with _lock:
        return _totals["masked"]


def reset_counter() -> None:
    with _lock:
        _totals.clear()
