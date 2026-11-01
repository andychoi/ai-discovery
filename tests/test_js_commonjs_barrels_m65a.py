"""JS/TS module resolution (roadmap item M6.5.a): CommonJS bindings, TS grammar queries, barrels, defaults."""

from __future__ import annotations

from pathlib import Path

from ai_discovery.graph.call_graph import resolve_calls
from ai_discovery.parsers.javascript import JavaScriptParser, extract_reexports


def _nodes(root: Path, files: dict[str, str]) -> list:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return [n for f in sorted(root.rglob("*")) if f.suffix in (".js", ".ts", ".tsx")
            for n in JavaScriptParser().parse_file(f)]


def _edges(nodes) -> dict[tuple[str, str], str]:
    return {(e.caller, e.callee): e.metadata.get("resolved_by") for e in resolve_calls(nodes).edges}


def test_commonjs_namespace_and_destructured_requires(tmp_path) -> None:
    nodes = _nodes(tmp_path, {
        "db.js": "function get(id) { return id; }\nfunction put(x) {}\nmodule.exports = { get, put };\n",
        "cache.js": "function get(k) { return k; }\nmodule.exports = { get };\n",   # same-named decoy
        "svc.js": "const db = require('./db');\nconst { put, get: fetch } = require('./db');\n"
                  "const find = async (id) => db.get(id);\nconst save = (x) => { put(x); return fetch(1); };\n"
                  "module.exports = { find, save };\n"})
    edges = _edges(nodes)
    assert edges[("svc.find", "db.get")] == "import_exact"
    assert edges[("svc.save", "db.put")] == "import_exact"
    assert edges[("svc.save", "db.get")] == "import_exact"          # aliased destructure binds the original
    assert ("svc.find", "cache.get") not in edges


def test_library_require_never_binds_a_repo_function(tmp_path) -> None:
    nodes = _nodes(tmp_path, {"util.js": "function get(o, k) { return o[k]; }\nmodule.exports = { get };\n",
                              "app.js": "const _ = require('lodash');\nconst run = (o) => _.get(o, 'a');\n"})
    res = resolve_calls(nodes)
    assert not [e for e in res.edges if e.caller == "app.run"]
    assert [u.reason for u in res.unresolved if u.caller == "app.run"] == ["external"]


def test_typescript_files_get_imports_and_call_sites(tmp_path) -> None:
    nodes = _nodes(tmp_path, {
        "author.mapper.ts": "const authorMapper = (a: any) => ({ name: a.name });\nexport default authorMapper;\n",
        "article.mapper.ts": "import authorMapper from './author.mapper';\n"
                             "const articleMapper = (a: any) => ({ author: authorMapper(a.author) });\n"
                             "export default articleMapper;\n",
        "View.tsx": "import { load } from './api';\nexport function View() { load(); return <div>{1}</div>; }\n",
        "api.ts": "export function load(): void {}\n"})
    by = {n.qualified_name: n for n in nodes}
    assert by["article.mapper.articleMapper"].imports[0]["module"] == "./author.mapper"
    assert {"name": "authorMapper", "receiver": None} in by["article.mapper.articleMapper"].call_sites
    assert by["View.View"].node_type == "ui_component"                 # .tsx parsed with the TSX grammar
    edges = _edges(nodes)
    assert edges[("article.mapper.articleMapper", "author.mapper.authorMapper")] == "import_exact"
    assert edges[("View.View", "api.load")] == "import_exact"


def test_default_import_under_another_local_name(tmp_path) -> None:
    nodes = _nodes(tmp_path, {"fmt.ts": "export default function formatMoney(x: number) { return x; }\n",
                              "other.ts": "export function formatMoney() {}\n",
                              "cart.ts": "import fmt from './fmt';\nexport const total = () => fmt(3);\n"})
    edges = _edges(nodes)
    assert edges[("cart.total", "fmt.formatMoney")] == "import_exact"
    assert ("cart.total", "other.formatMoney") not in edges


def test_barrel_reexports_followed_to_the_definition(tmp_path) -> None:
    nodes = _nodes(tmp_path, {
        "lib/orders/place.ts": "export function placeOrder() {}\n",
        "lib/orders/index.ts": "export * from './place';\n",
        "lib/pay.ts": "export function charge() {}\n",
        "lib/index.ts": "export * from './orders';\nexport { charge as pay } from './pay';\n",
        "lib/decoy.ts": "export function placeOrder() {}\nexport function pay() {}\n",
        "app.ts": "import { placeOrder, pay } from './lib';\nimport * as lib from './lib';\n"
                  "export function go() { placeOrder(); pay(); lib.placeOrder(); }\n"})
    assert extract_reexports(tmp_path / "lib/index.ts") == [
        {"module": "./orders", "name": "*", "exported": "*"},
        {"module": "./pay", "name": "charge", "exported": "pay"}]
    edges = _edges(nodes)
    assert edges[("app.go", "place.placeOrder")] == "import_exact"   # two barrel hops, `export *`
    assert edges[("app.go", "pay.charge")] == "import_exact"         # alias preserved
    assert ("app.go", "decoy.placeOrder") not in edges and ("app.go", "decoy.pay") not in edges


def test_reexport_cycle_terminates(tmp_path) -> None:
    nodes = _nodes(tmp_path, {"a.ts": "export * from './b';\n", "b.ts": "export * from './a';\n",
                              "app.ts": "import { nothing } from './a';\nexport function go() { nothing(); }\n"})
    res = resolve_calls(nodes)
    assert not [e for e in res.edges if e.caller == "app.go"]
