"""Retry policy (roadmap item M1.11.a): classification by status/exception, Retry-After, bounds."""

from __future__ import annotations

import httpx
import pytest

from ai_discovery.llm import retry
from ai_discovery.llm import transport


@pytest.fixture
def sleeps(monkeypatch) -> list[float]:
    slept: list[float] = []
    monkeypatch.setattr(retry, "_sleep", slept.append)
    return slept


def _http_error(status: int, headers: dict | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
    response = httpx.Response(status, headers=headers or {}, request=request)
    return httpx.HTTPStatusError(f"HTTP {status}", request=request, response=response)


class _ClientError(Exception):
    """Shape of botocore.exceptions.ClientError (dict `response`)."""

    def __init__(self, code: str, status: int):
        super().__init__(code)
        self.response = {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}}


class APIConnectionError(Exception):   # same class name as the anthropic SDK's
    pass


@pytest.mark.parametrize("exc,kind", [
    (_http_error(429, {"retry-after": "7"}), "rate_limit"),
    (_http_error(503), "transient"),
    (_http_error(529), "transient"),
    (_http_error(408), "transient"),
    (_http_error(400), "fatal"),
    (_http_error(401), "fatal"),
    (_ClientError("ThrottlingException", 400), "rate_limit"),
    (_ClientError("ServiceUnavailableException", 503), "transient"),
    (_ClientError("ValidationException", 400), "fatal"),
    (httpx.ConnectError("refused"), "network"),
    (httpx.ReadTimeout("slow"), "network"),
    (APIConnectionError("boom"), "network"),
    (ValueError("Throttling in the message text is not a signal"), "fatal"),
])
def test_classify(exc, kind) -> None:
    assert retry.classify(exc).kind == kind


def test_retry_after_is_honoured_and_capped(sleeps) -> None:
    errors = [_http_error(429, {"retry-after": "7"}), _http_error(429, {"retry-after": "500"})]

    def fn():
        if errors:
            raise errors.pop(0)
        return "ok"

    assert retry.call_with_retry(fn) == "ok"
    assert sleeps == [7.0, retry.DEFAULT_POLICY.retry_after_cap]


def test_rate_limits_get_more_tries_than_server_errors(sleeps) -> None:
    calls = {"n": 0}

    def always(exc):
        def fn():
            calls["n"] += 1
            raise exc
        return fn

    with pytest.raises(httpx.HTTPStatusError):
        retry.call_with_retry(always(_http_error(429)))
    assert calls["n"] == retry.DEFAULT_POLICY.rate_limit_attempts
    calls["n"] = 0
    with pytest.raises(httpx.HTTPStatusError):
        retry.call_with_retry(always(_http_error(502)))
    assert calls["n"] == retry.DEFAULT_POLICY.transient_attempts


def test_client_errors_fail_fast(sleeps) -> None:
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise _http_error(400)

    with pytest.raises(httpx.HTTPStatusError):
        retry.call_with_retry(fn)
    assert calls["n"] == 1 and sleeps == []


def test_http_date_retry_after() -> None:
    assert retry._parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT") == 0.0   # in the past → no wait
    assert retry._parse_retry_after("2.5") == 2.5 and retry._parse_retry_after("soon") is None


def test_openai_compat_transport_retries_429_then_succeeds(monkeypatch, sleeps) -> None:
    responses = [httpx.Response(429, headers={"retry-after": "1"}),
                 httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}],
                                           "usage": {"prompt_tokens": 100, "completion_tokens": 5,
                                                     "prompt_tokens_details": {"cached_tokens": 60}}})]

    def fake_post(url, json=None, timeout=None, headers=None):
        resp = responses.pop(0)
        resp.request = httpx.Request("POST", url)
        return resp

    monkeypatch.setattr(transport.httpx, "post", fake_post)
    result = transport.invoke_openai_compat("gpt-5-mini", "Hi", base_url="https://api.example/v1")
    text, tok_in, tok_out = result
    assert (text, tok_in, tok_out) == ("hi", 40, 5)          # cached tokens split out of the input
    assert result.cache_read == 60 and sleeps == [1.0]


def test_openai_compat_transport_does_not_retry_a_400(monkeypatch, sleeps) -> None:
    calls = {"n": 0}

    def fake_post(url, json=None, timeout=None, headers=None):
        calls["n"] += 1
        return httpx.Response(400, json={"error": "bad"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(transport.httpx, "post", fake_post)
    with pytest.raises(RuntimeError, match="OpenAI-compatible invocation failed"):
        transport.invoke_openai_compat("gpt-5-mini", "Hi", base_url="https://api.example/v1")
    assert calls["n"] == 1 and sleeps == []
