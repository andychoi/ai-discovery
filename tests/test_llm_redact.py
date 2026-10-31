"""Mask secrets in source before prompts (roadmap item M1.13)."""

from __future__ import annotations

import pytest

from ai_discovery import llm
from ai_discovery.llm import redact, transport
from ai_discovery.llm.redact import mask_secrets
from ai_discovery.llm.settings import settings_from_config

POSITIVE = [
    ('String password = "Sup3rS3cret!";', 'String password = "<redacted:secret>";'),
    ('"clientSecret": "abcDEF123456789",', '"clientSecret": "<redacted:secret>",'),
    ("authToken = 'a1b2c3d4e5f6g7'", "authToken = '<redacted:secret>'"),
    ('DB_PASSWORD="hunter2hunter2"', 'DB_PASSWORD="<redacted:secret>"'),
    ("jdbc:postgresql://admin:hunter2pass@db:5432/app", "jdbc:postgresql://admin:<redacted:url_password>@db:5432/app"),
    ("Server=x;User Id=sa;Password=P@ssw0rd123;", "Server=x;User Id=sa;Password=<redacted:connection_password>;"),
    ("spring.datasource.password=s3cr3tValue", "spring.datasource.password=<redacted:connection_password>"),
    ('<add key="ApiKey" value="9f8e7d6c5b4a3210"/>', '<add key="ApiKey" value="<redacted:secret>"/>'),
    ("key = AKIAIOSFODNN7EXAMPLE", "key = <redacted:aws_access_key>"),
    ("ghp_abcdefghijklmnopqrstuvwxyz0123456789AB", "<redacted:github_token>"),
    ("xoxb-1234567890-abcdefghij", "<redacted:slack_token>"),
    ("sk_live_abcdefghijklmnop1234", "<redacted:stripe_key>"),
    ("ANTHROPIC=sk-ant-abcdefghijklmnopqrstuvwxyz012345", "ANTHROPIC=<redacted:llm_api_key>"),
    ("Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456", "Authorization: Bearer <redacted:bearer_token>"),
    ('t = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop"', 't = "<redacted:jwt>"'),
]

NEGATIVE = [
    "token = getToken();",
    "password: ${DB_PASSWORD}",
    "apiKey: process.env.API_KEY",
    'private static final String SECRET = "changeme";',
    "if (password.equals(input)) {",
    'request.getParameter("password")',
    'def check(password="x"):',
    'tokenizer = "unicode61 remove_diacritics 2"',
    'const tokenType = "Bearer";',
    'passwordHint = "your pet name"',
    '<add key="Environment" value="Production"/>',
    "Password=%DB_PASSWORD%;",
    "https://example.com/path:with:colons",
]


@pytest.mark.parametrize("src,expected", POSITIVE)
def test_masks_secrets(src, expected) -> None:
    out, n = mask_secrets(src)
    assert out == expected and n == 1


@pytest.mark.parametrize("src", NEGATIVE)
def test_leaves_code_and_placeholders(src) -> None:
    assert mask_secrets(src) == (src, 0)


def test_private_key_block_and_idempotence() -> None:
    pem = "x\n-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA\nabc\n-----END RSA PRIVATE KEY-----\ny"
    out, n = mask_secrets(pem)
    assert out == "x\n<redacted:private_key>\ny" and n == 1
    for src, _ in POSITIVE:
        once, _ = mask_secrets(src)
        assert mask_secrets(once) == (once, 0)


def test_no_llm_imports() -> None:
    """M5.12 reuses the masker from the MCP layer: it must stay dependency-free."""
    import ast
    from pathlib import Path

    tree = ast.parse(Path(redact.__file__).read_text())
    mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | \
        {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert mods <= {"__future__", "re", "threading", "collections", "typing"}


def test_invoke_text_masks_prompt_and_system(monkeypatch) -> None:
    seen = {}

    def fake_invoke(model, prompt, max_tokens=4096, base_url="", api_key="", max_tokens_field=""):
        seen["prompt"] = prompt
        return "ok", 1, 2

    monkeypatch.setattr(transport, "invoke_openai_compat", fake_invoke)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    redact.reset_counter()
    out = llm.invoke_text(settings_from_config(None, {"provider": "openai"}), "fast",
                          'Summarise:\nString password = "Sup3rS3cret!";', system="db: Password=P@ssw0rd123;")
    assert "Sup3rS3cret" not in seen["prompt"] and "P@ssw0rd123" not in seen["prompt"]
    assert "<redacted:secret>" in seen["prompt"] and out["secrets_masked"] == 2
    assert redact.masked_total() == 2


def test_embeddings_are_masked(monkeypatch) -> None:
    seen = []
    monkeypatch.setattr(transport, "embed_ollama", lambda model, text, url, api_key=None: seen.append(text) or [0.0])
    llm.embed(settings_from_config(None, {"provider": "ollama"}), 'apiKey = "9f8e7d6c5b4a3210"')
    assert seen == ['apiKey = "<redacted:secret>"']
