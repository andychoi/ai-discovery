"""SPA route component files and Angular/TS DI typing (roadmap item M6.6)."""

from __future__ import annotations

from pathlib import Path

from ai_discovery.graph.call_graph import resolve_calls
from ai_discovery.parsers.javascript import JavaScriptParser
from ai_discovery.route_parser import parse_route_file, resolve_component_file


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)


def test_route_component_files_resolved_against_the_route_file(tmp_path) -> None:
    _write(tmp_path, {
        "tsconfig.json": '{ // comment\n "compilerOptions": { "baseUrl": ".", "paths": { "@views/*": ["src/views/*"] }, }, }\n',
        "src/views/Home.vue": "<template/>", "other/views/Home.vue": "<template/>",
        "src/views/admin/index.vue": "<template/>", "src/views/Users.vue": "<template/>",
        "src/views/Orders.vue": "<template/>",
        "src/router/index.js": "import Home from '../views/Home.vue';\n"
                               "export const routes = [\n  { path: '/', component: Home },\n"
                               "  { path: '/admin', component: () => import('../views/admin') },\n"
                               "  { path: '/users', component: () => import('@/views/Users.vue') },\n"
                               "  { path: '/orders', component: () => import('@views/Orders') },\n"
                               "  { path: '/x', component: () => import('some-package/X.vue') },\n];\n"})
    root = parse_route_file(tmp_path / "src/router/index.js", "vue", tmp_path)
    files = {c.path: c.component_file for c in root.children}
    assert files == {"/": "src/views/Home.vue", "/admin": "src/views/admin/index.vue",
                     "/users": "src/views/Users.vue", "/orders": "src/views/Orders.vue", "/x": None}


def test_angular_lazy_load_component(tmp_path) -> None:
    _write(tmp_path, {"src/app/orders/orders.component.ts": "export class OrdersComponent {}\n",
                      "src/app/app.routes.ts": "export const routes = [\n"
                      "  { path: 'orders', loadComponent: () => import('./orders/orders.component')"
                      ".then(m => m.OrdersComponent) },\n];\n"})
    root = parse_route_file(tmp_path / "src/app/app.routes.ts", "angular", tmp_path)
    assert root.children[0].component_file == "src/app/orders/orders.component.ts"
    assert resolve_component_file("@angular/router", tmp_path / "src/app/app.routes.ts", tmp_path) is None


def _nodes(root: Path, files: dict[str, str]) -> list:
    _write(root, files)
    return [n for f in sorted(root.rglob("*.ts")) for n in JavaScriptParser().parse_file(f)]


ANGULAR = {
    "order.service.ts": "export class OrderService { load(id: number) { return id; } }\n",
    "report.service.ts": "export class ReportService { load(id: number) { return id; } save() {} }\n",
}


def test_constructor_parameter_properties_and_inject_type_receivers(tmp_path) -> None:
    nodes = _nodes(tmp_path, {**ANGULAR, "detail.component.ts":
        "import { OrderService } from './order.service';\nimport { ReportService } from './report.service';\n"
        "import { HttpClient } from '@angular/common/http';\nimport { inject } from '@angular/core';\n"
        "export class Detail {\n  private reports = inject(ReportService);\n  private http: HttpClient;\n"
        "  constructor(private readonly orders: OrderService) {}\n"
        "  open(id: number) { this.orders.load(id); this.reports.save(); this.http.get('/x'); }\n"
        "  other(svc: ReportService) { svc.load(1); const o = new OrderService(); o.load(2); }\n}\n"})
    res = resolve_calls(nodes)
    edges = {(e.caller, e.callee): e.metadata.get("resolved_by") for e in res.edges}
    assert edges[("detail.component.Detail.open", "order.service.OrderService.load")] == "receiver_typed"
    assert ("detail.component.Detail.open", "report.service.ReportService.load") not in edges
    assert edges[("detail.component.Detail.open", "report.service.ReportService.save")] == "receiver_typed"
    assert [u.reason for u in res.unresolved if u.caller == "detail.component.Detail.open"
            and u.callee_name == "get"] == ["external"]                       # package type
    assert edges[("detail.component.Detail.other", "report.service.ReportService.load")] == "receiver_typed"
    assert edges[("detail.component.Detail.other", "order.service.OrderService.load")] == "receiver_typed"
