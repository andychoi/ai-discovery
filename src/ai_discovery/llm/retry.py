"""Retry policy for LLM calls (roadmap item M1.11.a, edge-case catalog: rate limits).

Classification is by HTTP status / exception type — never by matching error
text:

* ``rate_limit`` — HTTP 429, Bedrock ``Throttling*``/``TooManyRequests*``: up to
  ``rate_limit_attempts`` tries, sleeping for ``Retry-After`` when the server
  sends one (capped), else exponential backoff;
* ``transient`` — HTTP 408/5xx/529, Bedrock ``ServiceUnavailable``/
  ``InternalServer``/``ModelNotReady``/``ModelTimeout``: up to
  ``transient_attempts`` tries;
* ``network`` — connect/read timeouts and transport errors: same budget as transient;
* ``fatal`` — everything else (other 4xx such as validation or auth errors):
  fail at once.

Retries are per call: a batch caller retries the failing item only.
"""

from __future__ import annotations

import email.utils
import logging
import random
import time
from dataclasses import dataclass
from typing import Callable, Optional, TypeVar

log = logging.getLogger(__name__)
T = TypeVar("T")

_TRANSIENT_STATUS = {408, 500, 502, 503, 504, 529}
_BEDROCK_RATE = ("ThrottlingException", "TooManyRequestsException")
_BEDROCK_TRANSIENT = ("ServiceUnavailableException", "InternalServerException",
                      "ModelNotReadyException", "ModelTimeoutException")
_NETWORK_CLASSES = ("TimeoutException", "ConnectError", "ReadError", "WriteError", "RemoteProtocolError",
                    "TransportError", "NetworkError", "ReadTimeoutError", "ConnectTimeoutError",
                    "EndpointConnectionError", "ConnectionClosedError", "APIConnectionError",
                    "APITimeoutError")


@dataclass(frozen=True)
class RetryPolicy:
    rate_limit_attempts: int = 6
    transient_attempts: int = 3
    base_delay: float = 0.5
    max_delay: float = 30.0
    retry_after_cap: float = 60.0


DEFAULT_POLICY = RetryPolicy()


@dataclass(frozen=True)
class Failure:
    kind: str                         # rate_limit | transient | network | fatal
    status: Optional[int] = None
    retry_after: Optional[float] = None


def _parse_retry_after(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    return max(0.0, when.timestamp() - time.time())


def _status_and_headers(exc: BaseException) -> tuple[Optional[int], dict]:
    response = getattr(exc, "response", None)
    status = getattr(exc, "status_code", None)
    headers: dict = {}
    if response is not None and not isinstance(response, dict):
        status = status or getattr(response, "status_code", None)
        try:
            headers = {k.lower(): v for k, v in dict(getattr(response, "headers", {}) or {}).items()}
        except (TypeError, ValueError):
            headers = {}
    return (int(status) if isinstance(status, int) else None), headers


def classify(exc: BaseException) -> Failure:
    """Map an exception from any provider SDK/HTTP client to a retry class."""
    response = getattr(exc, "response", None)
    if isinstance(response, dict) and "Error" in response:          # botocore ClientError
        code = str(response.get("Error", {}).get("Code", ""))
        status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if code in _BEDROCK_RATE or status == 429:
            return Failure("rate_limit", status)
        if code in _BEDROCK_TRANSIENT or (isinstance(status, int) and status in _TRANSIENT_STATUS):
            return Failure("transient", status)
        return Failure("fatal", status)
    status, headers = _status_and_headers(exc)
    if status == 429:
        return Failure("rate_limit", 429, _parse_retry_after(headers.get("retry-after")))
    if status in _TRANSIENT_STATUS:
        return Failure("transient", status, _parse_retry_after(headers.get("retry-after")))
    if status is not None:
        return Failure("fatal", status)
    names = {cls.__name__ for cls in type(exc).__mro__}
    if names & set(_NETWORK_CLASSES) or isinstance(exc, (TimeoutError, ConnectionError)):
        return Failure("network")
    return Failure("fatal")


def _sleep(seconds: float) -> None:      # patched in tests
    time.sleep(seconds)


def call_with_retry(fn: Callable[[], T], *, what: str = "LLM call",
                    policy: RetryPolicy = DEFAULT_POLICY) -> T:
    """Run *fn*, retrying per *policy*; re-raises the last error when out of tries."""
    attempt = 0
    while True:
        attempt += 1
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — classified below, re-raised when not retryable
            failure = classify(exc)
            limit = (policy.rate_limit_attempts if failure.kind == "rate_limit"
                     else policy.transient_attempts if failure.kind in ("transient", "network") else 1)
            if attempt >= limit:
                raise
            if failure.retry_after is not None:
                delay = min(failure.retry_after, policy.retry_after_cap)
            else:
                delay = min(policy.base_delay * (2 ** (attempt - 1)), policy.max_delay)
                delay += random.uniform(0, policy.base_delay / 2)
            log.warning("%s failed (%s%s), attempt %d/%d; retrying in %.1fs", what, failure.kind,
                        f" {failure.status}" if failure.status else "", attempt, limit, delay)
            _sleep(delay)
