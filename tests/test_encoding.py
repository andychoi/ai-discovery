"""Encoding fallback (roadmap item M0.4).

UTF-8 fixtures in tests/fixtures/encoding/ are re-encoded at test time; every
twin must parse to the same nodes as the UTF-8 original with Hangul intact.
"""

from __future__ import annotations

import codecs
from pathlib import Path

import pytest

from ai_discovery.core import encoding as enc
from ai_discovery.parsers.csharp import CSharpParser
from ai_discovery.parsers.java import JavaParser
from ai_discovery.parsers.jsp import JspParser
from ai_discovery.parsers.webforms import WebFormsParser

FIXTURES = Path(__file__).parent / "fixtures" / "encoding"

PARSERS = {
    "OrderService.cs": CSharpParser,
    "OrderController.java": JavaParser,
    "order.jsp": JspParser,
    "Order.aspx": WebFormsParser,
}

TWINS = {
    "cp949": lambda t: t.encode("cp949"),
    "euc-kr": lambda t: t.encode("euc-kr"),
    "utf-16": lambda t: t.encode("utf-16"),  # with BOM
    "utf-8-sig": lambda t: codecs.BOM_UTF8 + t.encode("utf-8"),
}


def _shape(nodes) -> list[tuple]:
    # qualified_name is the file path for JSP/WebForms nodes, so compare the
    # path-independent fields.
    return sorted((n.node_type, n.name, n.line_start, n.line_end, tuple(sorted(n.calls))) for n in nodes)


def _parse(parser_cls, path: Path):
    return parser_cls().parse_file(path)


@pytest.fixture(autouse=True)
def _clean_stats():
    enc.reset_fallback_stats()
    yield
    enc.reset_fallback_stats()


# ── decode_bytes unit tests ───────────────────────────────────────────────

def test_utf8_passes_through_unchanged() -> None:
    raw = "주문 order".encode("utf-8")
    decoded = enc.decode_bytes(raw)
    assert decoded.data == raw and decoded.encoding == "utf-8" and not decoded.is_fallback


@pytest.mark.parametrize("label", ["cp949", "euc-kr"])
def test_legacy_korean_is_transcoded(label: str) -> None:
    decoded = enc.decode_bytes("주문이 취소되었습니다".encode(label))
    assert decoded.text == "주문이 취소되었습니다"
    assert decoded.is_fallback and not decoded.lossy


def test_cp949_only_syllable_needs_cp949() -> None:
    # '똠' is in CP949 (UHC extension) but not in EUC-KR.
    decoded = enc.decode_bytes("똠양꿍".encode("cp949"))
    assert decoded.text == "똠양꿍" and decoded.encoding == "cp949"


def test_declared_hint_used_when_not_utf8() -> None:
    raw = '<%@ page pageEncoding="Shift_JIS" %>\nテスト'.encode("shift_jis")
    decoded = enc.decode_bytes(raw)
    assert decoded.encoding == "shift_jis" and "テスト" in decoded.text


def test_stale_declared_hint_loses_to_valid_utf8() -> None:
    raw = '<%@ page pageEncoding="EUC-KR" %>\n주문'.encode("utf-8")
    decoded = enc.decode_bytes(raw)
    assert decoded.encoding == "utf-8" and decoded.text.endswith("주문")


def test_undecodable_bytes_are_lossy_not_dropped() -> None:
    decoded = enc.decode_bytes(b"ok \xff\xfe\xfa end", legacy=())
    assert decoded.lossy and decoded.text.startswith("ok ") and decoded.text.endswith(" end")


# ── parser twins ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("label", sorted(TWINS))
@pytest.mark.parametrize("fixture", sorted(PARSERS))
def test_twin_parses_like_utf8(fixture: str, label: str, tmp_path: Path) -> None:
    src = FIXTURES / fixture
    text = src.read_text(encoding="utf-8")
    twin = tmp_path / fixture
    twin.write_bytes(TWINS[label](text))

    expected = _parse(PARSERS[fixture], src)
    got = _parse(PARSERS[fixture], twin)
    assert expected, f"fixture {fixture} produced no nodes"
    assert _shape(got) == _shape(expected)
    # Hangul survives in the extracted source text.
    assert any("주문" in n.source_code for n in got)


@pytest.mark.parametrize("fixture", ["OrderService.cs", "OrderController.java"])
def test_fallback_is_recorded_on_nodes_and_stats(fixture: str, tmp_path: Path) -> None:
    twin = tmp_path / fixture
    twin.write_bytes((FIXTURES / fixture).read_text(encoding="utf-8").encode("cp949"))
    nodes = _parse(PARSERS[fixture], twin)
    assert nodes and all(n.framework_hints.get("source_encoding") == "cp949" for n in nodes)
    assert enc.fallback_stats() == {"cp949": 1}


def test_utf8_nodes_carry_no_encoding_hint() -> None:
    nodes = _parse(CSharpParser, FIXTURES / "OrderService.cs")
    assert nodes and not any("source_encoding" in n.framework_hints for n in nodes)
    assert enc.fallback_stats() == {}
