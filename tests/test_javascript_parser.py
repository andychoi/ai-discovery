from __future__ import annotations

from pathlib import Path

import pytest

from ai_discovery.parsers.javascript import JavaScriptParser


@pytest.fixture
def parser():
    return JavaScriptParser()


@pytest.fixture
def sample_express(tmp_path: Path):
    code = """\
const express = require('express');
const router = express.Router();

router.get('/api/payments/:id', async (req, res) => {
    const payment = await PaymentService.find(req.params.id);
    res.json(payment);
});

router.post('/api/payments', async (req, res) => {
    const result = await PaymentService.create(req.body);
    res.status(201).json(result);
});

module.exports = router;
"""
    f = tmp_path / "payments.js"
    f.write_text(code)
    return f


@pytest.fixture
def sample_react(tmp_path: Path):
    code = """\
function PaymentForm({ onSubmit }) {
    return <form onSubmit={onSubmit}><button>Pay</button></form>;
}
export default PaymentForm;
"""
    f = tmp_path / "PaymentForm.jsx"
    f.write_text(code)
    return f


@pytest.fixture
def sample_typescript(tmp_path: Path):
    code = """\
interface Payment {
    id: string;
    amount: number;
}

function processPayment(payment: Payment): boolean {
    return payment.amount > 0;
}

export { processPayment };
"""
    f = tmp_path / "payment.ts"
    f.write_text(code)
    return f


def test_extracts_express_endpoints(parser: JavaScriptParser, sample_express: Path):
    nodes = parser.parse_file(sample_express)
    endpoints = [n for n in nodes if n.node_type == "endpoint"]
    assert len(endpoints) == 2

    by_route = {e.framework_hints["route"]: e for e in endpoints}

    get_ep = by_route["/api/payments/:id"]
    assert get_ep.framework_hints["method"] == "GET"
    assert get_ep.framework_hints["framework"] == "express"

    post_ep = by_route["/api/payments"]
    assert post_ep.framework_hints["method"] == "POST"
    assert post_ep.framework_hints["framework"] == "express"


def test_extracts_react_component(parser: JavaScriptParser, sample_react: Path):
    nodes = parser.parse_file(sample_react)
    components = [n for n in nodes if n.node_type == "ui_component"]
    assert len(components) == 1
    assert components[0].name == "PaymentForm"


def test_handles_typescript(parser: JavaScriptParser, sample_typescript: Path):
    nodes = parser.parse_file(sample_typescript)
    functions = [n for n in nodes if n.node_type == "function"]
    assert any(n.name == "processPayment" for n in functions)


def test_can_parse(parser: JavaScriptParser):
    assert parser.can_parse(Path("foo.js"))
    assert parser.can_parse(Path("bar.ts"))
    assert parser.can_parse(Path("baz.tsx"))
    assert parser.can_parse(Path("qux.jsx"))
    assert not parser.can_parse(Path("nope.py"))


def test_extracts_calls_from_endpoints(parser: JavaScriptParser, sample_express: Path):
    nodes = parser.parse_file(sample_express)
    endpoints = [n for n in nodes if n.node_type == "endpoint"]
    get_ep = next(e for e in endpoints if e.framework_hints["method"] == "GET")
    assert "find" in get_ep.calls or "json" in get_ep.calls


def test_class_extraction(parser: JavaScriptParser, tmp_path: Path):
    code = """\
class UserService {
    constructor(db) {
        this.db = db;
    }
    findUser(id) {
        return this.db.query(id);
    }
}
"""
    f = tmp_path / "user.js"
    f.write_text(code)
    nodes = parser.parse_file(f)
    classes = [n for n in nodes if n.node_type == "class"]
    assert len(classes) == 1
    assert classes[0].name == "UserService"
    methods = [n for n in nodes if n.node_type == "method"]
    assert any(m.name == "findUser" for m in methods)


def test_arrow_function_extraction(parser: JavaScriptParser, tmp_path: Path):
    code = """\
const add = (a, b) => a + b;
const greet = (name) => {
    return 'Hello ' + name;
};
"""
    f = tmp_path / "utils.js"
    f.write_text(code)
    nodes = parser.parse_file(f)
    functions = [n for n in nodes if n.node_type == "function"]
    names = {n.name for n in functions}
    assert "add" in names
    assert "greet" in names


# ─── Phase 1.1: imports + call_sites ─────────────────────────────────


def test_captures_named_import(parser: JavaScriptParser, tmp_path: Path):
    f = tmp_path / "handler.js"
    f.write_text("""\
import { OrderService } from './svc/orders';

function handle() {
    OrderService.save(1);
}
""")
    nodes = parser.parse_file(f)
    fn = next(n for n in nodes if n.name == "handle")
    assert {"module": "./svc/orders", "name": "OrderService", "alias": None} in fn.imports


def test_captures_named_import_with_alias(parser: JavaScriptParser, tmp_path: Path):
    f = tmp_path / "handler.js"
    f.write_text("""\
import { OrderService as OS } from './svc/orders';

function go() {
    OS.save(1);
}
""")
    nodes = parser.parse_file(f)
    fn = next(n for n in nodes if n.name == "go")
    assert {"module": "./svc/orders", "name": "OrderService", "alias": "OS"} in fn.imports


def test_captures_default_import(parser: JavaScriptParser, tmp_path: Path):
    f = tmp_path / "handler.js"
    f.write_text("""\
import OrderService from './svc/orders';

function go() {
    OrderService.save(1);
}
""")
    nodes = parser.parse_file(f)
    fn = next(n for n in nodes if n.name == "go")
    assert {"module": "./svc/orders", "name": "OrderService", "alias": None} in fn.imports


def test_captures_namespace_import(parser: JavaScriptParser, tmp_path: Path):
    f = tmp_path / "handler.js"
    f.write_text("""\
import * as Orders from './svc/orders';

function go() {
    Orders.save(1);
}
""")
    nodes = parser.parse_file(f)
    fn = next(n for n in nodes if n.name == "go")
    assert {"module": "./svc/orders", "name": None, "alias": "Orders"} in fn.imports


def test_side_effect_import_produces_no_record(parser: JavaScriptParser, tmp_path: Path):
    f = tmp_path / "handler.js"
    f.write_text("""\
import './polyfills';

function go() {
    doThing();
}
""")
    nodes = parser.parse_file(f)
    fn = next(n for n in nodes if n.name == "go")
    assert fn.imports == []


def test_captures_call_site_receiver(parser: JavaScriptParser, tmp_path: Path):
    f = tmp_path / "handler.js"
    f.write_text("""\
import { OrderService } from './svc/orders';

function handle() {
    OrderService.save(1);
    plainCall();
}
""")
    nodes = parser.parse_file(f)
    fn = next(n for n in nodes if n.name == "handle")
    sites = {(s["name"], s["receiver"]) for s in fn.call_sites}
    assert ("save", "OrderService") in sites
    assert ("plainCall", None) in sites
