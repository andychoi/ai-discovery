"""Screen → flow → entity → table drilldown on the wiki pages (roadmap item M4.4.b, ADR-0007).

The Spring fixture is scanned for real. Then one screen mapping and one literal state assignment are added to
the store, the way the screen and entity stages would write them. The pages must connect them through stored
facts only: the screen starts the flow, the flow's trace reaches the trigger, and the trigger changes the
entity's state. No page claims a process model (gateways, manual steps) that static analysis cannot see.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone

import pytest

from ai_discovery.pages.registry import BuildContext, run_builders
from tests.test_builders_flows_m34 import FIXTURES, _git_scan

ENTITY = "com.example.entity.Order"
TRIGGER = "com.example.service.OrderService.createOrder"


@pytest.fixture(scope="module")
def drill(tmp_path_factory):
    repo = tmp_path_factory.mktemp("drill") / "repo"
    shutil.copytree(FIXTURES / "projects" / "spring-boot-app", repo)
    conn, sid = _git_scan(repo)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("DELETE FROM screen_specs WHERE scan_id = ?", (sid,))
    conn.execute("INSERT INTO screen_specs (scan_id, screen_id, spec_json, model, created_at) VALUES (?, ?, ?, ?, ?)",
                 (sid, "orders-new", json.dumps({
                     "screen_id": "orders-new", "screen_label": "New Order", "menu_path": ["Orders", "New"],
                     "be_controllers": [{"component_type": "controller", "class_name": "OrderController",
                                         "file_path": "x", "methods": ["create"], "confidence": 0.9}],
                     "fe_api_calls": [{"method": "GET", "path": "/api/orders/{orderId}", "confidence": 0.7}],
                     "db_tables": []}), "structural", now))
    conn.execute("INSERT INTO entity_state_machines (scan_id, entity, entity_id, states_json, fields_json, "
                 "source_files_json) VALUES (?, 'Order', ?, ?, ?, '[]')",
                 (sid, ENTITY, json.dumps(["DRAFT", "SUBMITTED"]), json.dumps(["status"])))
    conn.execute("INSERT INTO state_transitions (scan_id, entity, entity_id, field, from_state, to_state, "
                 "trigger_function, guard_expr) VALUES (?, 'Order', ?, 'status', 'DRAFT', 'SUBMITTED', ?, "
                 "'order.items.size() > 0')", (sid, ENTITY, TRIGGER))
    conn.execute("INSERT INTO state_transitions (scan_id, entity, entity_id, field, from_state, to_state, "
                 "trigger_function) VALUES (?, 'Order', ?, 'status', 'SUBMITTED', 'SHIPPED', 'external.Unknown.ship')",
                 (sid, ENTITY))
    conn.commit()
    pages = {p.page_id: p for p in run_builders(BuildContext(conn=conn, scan_id=sid, repo=repo))}
    return _Pages(pages, (conn, sid, repo))


class _Pages(dict):
    """Pages by id, plus ``["_ctx"]`` → (conn, scan_id, repo) for rebuilding."""

    def __init__(self, pages, ctx):
        super().__init__(pages)
        self.ctx = ctx

    def __getitem__(self, key):
        return self.ctx if key == "_ctx" else super().__getitem__(key)


def _flow(pages, method: str):
    return next(p for p in pages.values() if p.type == "flow" and p.facts["entry"].endswith(f"OrderController.{method}"))


def _section(page, title: str) -> str:
    return next(s.body_md for s in page.sections if s.heading == title)


def test_drilldown_screen_routes_to_the_flows_it_starts(drill) -> None:
    screen = drill["screen:orders-new"]
    create, get = _flow(drill, "create"), _flow(drill, "get")
    routed = {lk.dst for lk in screen.links if lk.kind == "routes_to"}
    assert routed == {create.page_id, get.page_id}        # by mapped controller method; by normalized API route
    started = _section(screen, "Flows it starts")
    assert "controller `OrderController`" in started and "API call `GET /api/orders/{orderId}`" in started
    assert _flow(drill, "list").page_id not in routed    # `methods` restricts the controller mapping
    assert "DRAFT → SUBMITTED" in _section(screen, "Business state it can change")


def test_drilldown_flow_writes_the_entity_its_trace_changes(drill) -> None:
    create = _flow(drill, "create")
    assert ("entity:" + ENTITY, "writes") in {(lk.dst, lk.kind) for lk in create.links}
    body = _section(create, "State changes")
    assert "DRAFT → SUBMITTED" in body and "`order.items.size() > 0`" in body
    assert create.facts["screens"] == ["orders-new"]
    assert "[New Order](screen:orders-new)" in _section(create, "Trigger")
    assert not any(s.heading == "State changes" for s in _flow(drill, "list").sections)   # its trace never reaches it


def test_drilldown_entity_says_what_drives_each_change_and_its_limits(drill) -> None:
    entity = drill["entity:" + ENTITY]
    body = _section(entity, "What drives each change")
    lines = {ln.split(" | ")[0].lstrip("| "): ln for ln in body.splitlines() if "→" in ln}
    assert f"]({_flow(drill, 'create').page_id})" in lines["DRAFT → SUBMITTED"]
    assert "[New Order](screen:orders-new)" in lines["DRAFT → SUBMITTED"]
    assert "_no traced flow reaches it_" in lines["SUBMITTED → SHIPPED"]
    assert "Not visible from code" in body and "ADR-0007" in body


def test_drilldown_links_resolve_and_no_inferred_process_model(drill) -> None:
    live = set(drill)
    assert sorted({(p.page_id, lk.dst) for p in drill.values() for lk in p.links if lk.dst not in live}) == []
    for p in drill.values():
        text = "\n".join(s.body_md for s in p.sections).lower()
        assert "gateway" not in text and "user_task" not in text and "bpmn" not in text, p.page_id


def test_drilldown_pages_are_deterministic(drill) -> None:
    from ai_discovery.pages.model import canonical_json

    pages, (conn, sid, repo) = drill, drill["_ctx"]
    again = {p.page_id: p for p in run_builders(BuildContext(conn=conn, scan_id=sid, repo=repo))}
    kinds = ("screen", "flow", "entity")
    assert {k: canonical_json(p.to_row(sid)) for k, p in again.items() if p.type in kinds} == \
        {k: canonical_json(p.to_row(sid)) for k, p in pages.items() if k != "_ctx" and p.type in kinds}
