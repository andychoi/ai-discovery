from __future__ import annotations

from pathlib import Path

import pytest

from ai_discovery.parsers.python_parser import PythonParser


@pytest.fixture
def parser():
    return PythonParser()


@pytest.fixture
def sample_fastapi(tmp_path: Path):
    code = '''\
from fastapi import APIRouter, Depends

router = APIRouter()

class PaymentService:
    """Handles payment processing."""
    def process_payment(self, order_id: str, amount: float) -> dict:
        result = self._validate(order_id)
        self._charge(amount)
        return {"status": "ok"}
    def _validate(self, order_id: str) -> bool:
        return True
    def _charge(self, amount: float) -> None:
        pass

@router.post("/api/payments")
async def create_payment(order_id: str, amount: float):
    svc = PaymentService()
    return svc.process_payment(order_id, amount)

@router.get("/api/payments/{payment_id}")
async def get_payment(payment_id: str):
    return {"id": payment_id}
'''
    f = tmp_path / "payments.py"
    f.write_text(code)
    return f


def test_extracts_classes(parser: PythonParser, sample_fastapi: Path):
    nodes = parser.parse_file(sample_fastapi)
    classes = [n for n in nodes if n.node_type == "class"]
    assert len(classes) == 1
    assert classes[0].name == "PaymentService"
    assert classes[0].qualified_name == "payments.PaymentService"


def test_extracts_methods(parser: PythonParser, sample_fastapi: Path):
    nodes = parser.parse_file(sample_fastapi)
    methods = [n for n in nodes if n.node_type == "method"]
    method_names = {m.name for m in methods}
    assert method_names == {"process_payment", "_validate", "_charge"}
    for m in methods:
        assert m.qualified_name.startswith("payments.PaymentService.")


def test_extracts_endpoints(parser: PythonParser, sample_fastapi: Path):
    nodes = parser.parse_file(sample_fastapi)
    endpoints = [n for n in nodes if n.node_type == "endpoint"]
    assert len(endpoints) == 2

    by_name = {e.name: e for e in endpoints}

    create = by_name["create_payment"]
    assert create.framework_hints["method"] == "POST"
    assert create.framework_hints["route"] == "/api/payments"

    get = by_name["get_payment"]
    assert get.framework_hints["method"] == "GET"
    assert get.framework_hints["route"] == "/api/payments/{payment_id}"


def test_extracts_call_references(parser: PythonParser, sample_fastapi: Path):
    nodes = parser.parse_file(sample_fastapi)
    methods = {m.name: m for m in nodes if m.node_type == "method"}
    process = next(n for n in nodes if n.name == "process_payment")
    assert "_validate" in process.calls
    assert "_charge" in process.calls


def test_method_params_exclude_self(parser: PythonParser, sample_fastapi: Path):
    nodes = parser.parse_file(sample_fastapi)
    process = next(n for n in nodes if n.name == "process_payment")
    assert "self" not in process.params
    assert "order_id" in process.params
    assert "amount" in process.params


def test_can_parse(parser: PythonParser, tmp_path: Path):
    assert parser.can_parse(Path("foo.py"))
    assert parser.can_parse(Path("bar.pyw"))
    assert not parser.can_parse(Path("baz.js"))


def test_no_double_counting_class_functions(parser: PythonParser, sample_fastapi: Path):
    """Methods inside classes must NOT also appear as top-level functions."""
    nodes = parser.parse_file(sample_fastapi)
    functions = [n for n in nodes if n.node_type == "function"]
    function_names = {f.name for f in functions}
    assert "process_payment" not in function_names
    assert "_validate" not in function_names
    assert "_charge" not in function_names


# ---------------------------------------------------------------------------
# Endpoint detection — Flask methods=[], default GET, and class-based views.
# HIGH-5: these idioms were previously missed; Python had no endpoint tests.
# ---------------------------------------------------------------------------

def _endpoints(parser, tmp_path: Path, code: str):
    f = tmp_path / "views.py"
    f.write_text(code)
    return {n.name: n for n in parser.parse_file(f) if n.node_type == "endpoint"}


def test_flask_route_methods_list(parser, tmp_path):
    code = (
        "from flask import Flask\n"
        "app = Flask(__name__)\n\n"
        "@app.route('/users/signup', methods=['POST'])\n"
        "def signup():\n    pass\n\n"
        "@app.route('/health')\n"
        "def health():\n    pass\n"
    )
    eps = _endpoints(parser, tmp_path, code)
    assert eps["signup"].framework_hints["method"] == "POST"
    assert eps["signup"].framework_hints["route"] == "/users/signup"
    # No methods= defaults to GET.
    assert eps["health"].framework_hints["method"] == "GET"
    assert eps["health"].framework_hints["route"] == "/health"


def test_flask_route_multiple_methods(parser, tmp_path):
    code = (
        "@app.route('/items', methods=['GET', 'POST'])\n"
        "def items():\n    pass\n"
    )
    eps = _endpoints(parser, tmp_path, code)
    assert eps["items"].framework_hints["method"] == "GET,POST"


def test_class_based_view_verb_methods_are_endpoints(parser, tmp_path):
    code = (
        "class OrderView(APIView):\n"
        "    def get(self, request):\n        pass\n"
        "    def post(self, request):\n        pass\n"
        "    def helper(self):\n        pass\n"
    )
    eps = _endpoints(parser, tmp_path, code)
    assert set(eps) == {"get", "post"}  # helper is not an endpoint
    assert eps["get"].framework_hints["method"] == "GET"
