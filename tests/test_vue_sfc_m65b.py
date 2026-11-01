"""Vue single-file components (roadmap item M6.5.b): script offsets, TS scripts, template edges."""

from __future__ import annotations

from pathlib import Path

from ai_discovery.graph.call_graph import resolve_calls
from ai_discovery.parsers.javascript import JavaScriptParser
from ai_discovery.parsers.vue import split_sfc


def _nodes(root: Path, files: dict[str, str]) -> list:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return [n for f in sorted(root.rglob("*")) if f.suffix in (".js", ".ts", ".vue")
            for n in JavaScriptParser().parse_file(f)]


def _edges(nodes) -> dict[tuple[str, str], str]:
    return {(e.caller, e.callee): e.metadata.get("resolved_by") for e in resolve_calls(nodes).edges}


CART = """<template>
  <div>
    <template v-if="items.length">
      <button @click="checkout(items)">Pay</button>
    </template>
    <input @input="count++" v-on:keyup.enter="refresh" />
    <a @click="$emit('close')">x</a>
  </div>
</template>

<script setup lang="ts">
import { pay } from '../api/pay';
const items: number[] = [];
function checkout(list: number[]): void {
  pay(list.length);
}
const refresh = (): void => {};
</script>
"""


def test_split_keeps_offsets_and_reads_the_template() -> None:
    sfc = split_sfc(CART.encode())
    assert len(sfc.script) == len(CART.encode()) and sfc.script.count(b"\n") == CART.count("\n")
    assert b"<button" not in sfc.script and b"function checkout" in sfc.script
    assert sfc.grammar == "ts" and (sfc.template_line, sfc.template_end) == (1, 9)   # nested <template> counted
    assert [h for h, _ in sfc.handlers] == ["checkout", "refresh"]   # `count++` binds nothing; `$emit` kept out later
    assert dict(sfc.handlers)["checkout"] == 4


def test_script_setup_functions_and_template_handlers(tmp_path) -> None:
    nodes = _nodes(tmp_path, {"components/Cart.vue": CART, "api/pay.ts": "export function pay(n: number) {}\n"})
    by = {n.qualified_name: n for n in nodes}
    assert by["Cart.checkout"].line_start == 14                       # real line in the .vue file
    assert by["Cart"].node_type == "ui_component"
    edges = _edges(nodes)
    assert edges[("Cart", "Cart.checkout")] == "framework"
    assert edges[("Cart", "Cart.refresh")] == "framework"
    assert edges[("Cart.checkout", "pay.pay")] == "import_exact"     # TS script, relative import


def test_options_api_methods_and_this_calls(tmp_path) -> None:
    nodes = _nodes(tmp_path, {"Orders.vue": """<template><button @click="save">Save</button></template>
<script>
import { post } from './http';
export default {
  name: 'Orders',
  methods: {
    save() { this.validate(); post('/orders'); },
    validate: function () { return true; },
  },
};
</script>
""", "http.js": "export function post(url) {}\n"})
    edges = _edges(nodes)
    assert edges[("Orders", "Orders.save")] == "framework"
    assert edges[("Orders.save", "Orders.validate")] == "self_scope"
    assert edges[("Orders.save", "http.post")] == "import_exact"


def test_component_tags_bind_to_the_imported_file(tmp_path) -> None:
    nodes = _nodes(tmp_path, {
        "orders/Index.vue": "<template><order-row /><el-button /></template>\n<script>\n"
                            "import OrderRow from './OrderRow.vue';\nimport { ElButton } from 'element-plus';\n"
                            "export default { components: { OrderRow, ElButton } };\n</script>\n",
        "orders/OrderRow.vue": "<template><tr @click=\"open\"></tr></template>\n"
                               "<script setup>\nfunction open() {}\n</script>\n",
        "users/Index.vue": "<template><b @click=\"open\">u</b></template>\n<script setup>\nfunction open() {}\n</script>\n"})
    res = resolve_calls(nodes)
    edges = {(e.caller, e.callee): e.metadata.get("resolved_by") for e in res.edges}
    assert edges[("Index", "OrderRow")] == "framework"              # kebab-case tag → imported PascalCase
    assert [u.reason for u in res.unresolved if u.callee_name == "ElButton"] == ["external"]


def test_same_named_components_never_cross_link(tmp_path) -> None:
    nodes = _nodes(tmp_path, {
        "a/Index.vue": "<template><b @click=\"open\">a</b></template>\n<script setup>\nfunction open() {}\n</script>\n",
        "b/Index.vue": "<template><b @click=\"close\">b</b></template>\n<script setup>\nfunction other() {}\n</script>\n"})
    res = resolve_calls(nodes)
    # b/Index.vue has no `close`: it must stay unresolved, never bind to a/Index.vue's functions
    assert [u.reason for u in res.unresolved if u.callee_name == "close"] == ["not_found"]
    assert all(e.metadata.get("call") != "close" for e in res.edges)
