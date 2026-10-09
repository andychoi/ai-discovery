"""Page model, builder registry, notes (roadmap item M3.1)."""

from __future__ import annotations

import sqlite3
import sys
import textwrap
from pathlib import Path

import pytest

from ai_discovery.db import get_conn, init_db
from ai_discovery.pages import registry
from ai_discovery.pages.model import (
    Citation, Link, Page, PageError, Section, backlinks, load_page, make_page_id, save_pages,
)
from ai_discovery.pages.notes import add_note, notes_for, record_alias, resolve
from ai_discovery.pages.registry import BuildContext, page_builder, run_builders


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    db = tmp_path / "d.db"
    init_db(db)
    c = get_conn(db)
    c.execute("INSERT INTO scan_runs (repo_path, started_at, status) VALUES ('r', 'now', 'completed')")
    c.commit()
    return c


def _page(**kw) -> Page:
    base = dict(page_id="screen:order-cancel", title="Order cancel",
                facts={"tables": ["orders"], "label": "주문 취소"},
                sections=[Section("Purpose", "Cancels an order.")],
                links=[Link("table:orders", "writes"), Link("symbol:OrderService.cancel", "calls")],
                citations=[Citation("src/main/java/OrderController.java", 12, 30)],
                source_hashes={"src/main/java/OrderController.java": "abc"})
    base.update(kw)
    return Page(**base)


# ── model ─────────────────────────────────────────────────────────────────

def test_page_ids() -> None:
    assert make_page_id("table", "dbo.orders") == "table:dbo.orders"
    assert make_page_id("file", " src/My File.cs ") == "file:src/My-File.cs"
    for bad in (("nope", "x"), ("table", "  ")):
        with pytest.raises(PageError):
            make_page_id(*bad)
    with pytest.raises(PageError):
        Page("orders", "Orders")  # no type prefix


@pytest.mark.parametrize("kw", [
    {"title": " "}, {"confidence": 1.5}, {"freshness": "rotten"},
])
def test_page_validation(kw) -> None:
    with pytest.raises(PageError):
        _page(**kw)


def test_link_and_citation_validation() -> None:
    with pytest.raises(PageError):
        Link("table:orders", "teleports")
    with pytest.raises(PageError):
        Citation("/abs/path.java", 1, 2)
    with pytest.raises(PageError):
        Citation("a.java", 5, 2)
    assert str(Citation("a.java", 3, 3)) == "a.java:3" and str(Citation("a.java", 3, 7)) == "a.java:3-7"


def test_canonical_order_makes_rows_deterministic() -> None:
    a = _page()
    b = _page(links=list(reversed(a.links)) + [a.links[0]],
              facts={"label": "주문 취소", "tables": ["orders"]})
    assert a.to_row(1) == b.to_row(1)  # duplicate/unordered links and dict order don't matter


def test_round_trip_and_backlinks(conn) -> None:
    page = _page()
    save_pages(conn, 1, [page, _page(page_id="table:orders", title="orders", links=[])])
    conn.commit()
    loaded = load_page(conn, 1, "screen:order-cancel")
    assert loaded == page
    assert loaded.type == "screen" and loaded.key == "order-cancel"
    assert backlinks(conn, 1, "table:orders") == [Link("screen:order-cancel", "writes")]
    assert load_page(conn, 1, "screen:missing") is None


def test_resave_replaces_links(conn) -> None:
    save_pages(conn, 1, [_page()])
    save_pages(conn, 1, [_page(links=[Link("table:customers", "reads")])])
    assert backlinks(conn, 1, "table:orders") == []
    assert backlinks(conn, 1, "table:customers") == [Link("screen:order-cancel", "reads")]


# ── registry ──────────────────────────────────────────────────────────────

def _ctx(conn) -> BuildContext:
    return BuildContext(conn=conn, scan_id=1, repo=Path("."), config_hash="cfg", commit="abc")


def test_builders_are_auto_discovered(tmp_path: Path, monkeypatch) -> None:
    pkg = tmp_path / "fakebuilders"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "tables.py").write_text(textwrap.dedent('''
        from ai_discovery.pages.model import Page
        from ai_discovery.pages.registry import page_builder

        @page_builder("test-tables", emits={"table"}, inputs=("db_relationship",), version="2")
        def build(ctx):
            yield Page("table:orders", "orders")
    '''))
    (pkg / "_private.py").write_text("raise RuntimeError('must not be imported')\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        builders = registry.discover_builders("fakebuilders")
        b = next(x for x in builders if x.name == "test-tables")
        assert b.emits == {"table"} and b.inputs == ("db_relationship",) and b.version == "2"
    finally:
        registry.unregister("test-tables")
        sys.modules.pop("fakebuilders.tables", None)
        sys.modules.pop("fakebuilders", None)


def test_run_builders_stamps_sorts_and_validates(conn) -> None:
    @page_builder("t-screens", emits={"screen"}, inputs=("screen_specs",), version="3")
    def screens(ctx):
        yield _page(page_id="screen:b", title="B", links=[])
        yield _page(page_id="screen:a", title="A", links=[])

    try:
        pages = run_builders(_ctx(conn), [registry._REGISTRY["t-screens"]])
        assert [p.page_id for p in pages] == ["screen:a", "screen:b"]
        assert pages[0].stamp.builder == "t-screens" and pages[0].stamp.builder_version == "3"
        assert pages[0].stamp.commit == "abc" and pages[0].stamp.config_hash == "cfg"
    finally:
        registry.unregister("t-screens")


def test_builder_emitting_undeclared_type_fails(conn) -> None:
    @page_builder("t-liar", emits={"table"})
    def liar(ctx):
        yield _page()  # a screen page

    try:
        with pytest.raises(PageError, match="declares"):
            run_builders(_ctx(conn), [registry._REGISTRY["t-liar"]])
    finally:
        registry.unregister("t-liar")


def test_duplicate_page_ids_across_builders_fail(conn) -> None:
    @page_builder("t-one", emits={"table"})
    def one(ctx):
        yield Page("table:orders", "orders")

    @page_builder("t-two", emits={"table"})
    def two(ctx):
        yield Page("table:orders", "orders again")

    try:
        with pytest.raises(PageError, match="both"):
            run_builders(_ctx(conn), [registry._REGISTRY["t-one"], registry._REGISTRY["t-two"]])
    finally:
        registry.unregister("t-one")
        registry.unregister("t-two")


def test_builder_declaration_checked() -> None:
    with pytest.raises(PageError):
        page_builder("bad", emits={"spaceship"})
    with pytest.raises(PageError):
        page_builder("bad", emits=set())


# ── notes ─────────────────────────────────────────────────────────────────

def test_notes_survive_page_rebuild(conn) -> None:
    save_pages(conn, 1, [_page()])
    add_note(conn, "screen:order-cancel", "Legacy batch also cancels orders at midnight.", author="andy")
    conn.execute("DELETE FROM pages")  # a rebuild replaces page rows
    save_pages(conn, 1, [_page()])
    notes = notes_for(conn, "screen:order-cancel")
    assert [n.body for n in notes] == ["Legacy batch also cancels orders at midnight."]


def test_notes_follow_renames(conn) -> None:
    add_note(conn, "symbol:OrderSvc.cancel", "old note")
    record_alias(conn, "symbol:OrderSvc.cancel", "symbol:OrderService.cancel", removed_in_scan=2)
    add_note(conn, "symbol:OrderService.cancel", "new note")
    record_alias(conn, "symbol:OrderService.cancel", "symbol:OrderService.cancelOrder", removed_in_scan=3)

    assert resolve(conn, "symbol:OrderSvc.cancel") == "symbol:OrderService.cancelOrder"
    bodies = [n.body for n in notes_for(conn, "symbol:OrderService.cancelOrder")]
    assert bodies == ["old note", "new note"]
    # asking through an old id gives the same notes
    assert [n.body for n in notes_for(conn, "symbol:OrderSvc.cancel")] == bodies


def test_removed_page_keeps_its_notes_on_the_tombstone(conn) -> None:
    add_note(conn, "file:src/legacy.cs", "kept for audit")
    record_alias(conn, "file:src/legacy.cs", None, removed_in_scan=4)
    assert resolve(conn, "file:src/legacy.cs") == "file:src/legacy.cs"
    assert [n.body for n in notes_for(conn, "file:src/legacy.cs")] == ["kept for audit"]


def test_alias_cycles_rejected(conn) -> None:
    record_alias(conn, "symbol:a", "symbol:b")
    with pytest.raises(ValueError, match="cycle"):
        record_alias(conn, "symbol:b", "symbol:a")


def test_note_validation(conn) -> None:
    with pytest.raises(ValueError):
        add_note(conn, "screen:x", "  ")
    with pytest.raises(PageError):
        add_note(conn, "not-an-id", "body")
