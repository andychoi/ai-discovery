"""Tests for domain_classifier module."""

from __future__ import annotations

from ai_discovery.graph.domain_classifier import classify_domains, infer_domain
from ai_discovery.graph.models import CodeNode


def _make_node(
    file_path: str = "src/svc.py",
    qualified_name: str = "svc",
    node_type: str = "function",
    language: str = "python",
    **kwargs,
) -> CodeNode:
    return CodeNode(
        file_path=file_path,
        language=language,
        node_type=node_type,
        name=qualified_name.split(".")[-1],
        qualified_name=qualified_name,
        source_code="",
        line_start=1,
        line_end=10,
        **kwargs,
    )


# ── infer_domain ──


def test_infer_domain_from_namespace():
    assert infer_domain("com.corp.payments.PaymentService", "src/payments/PaymentService.java") == "payments"


def test_infer_domain_from_path():
    assert infer_domain("PaymentsController", "src/controllers/payments/PaymentsController.cs") == "payments"


def test_infer_domain_skips_framework_dirs():
    # "controllers" is a framework dir, should skip to "payments"
    assert infer_domain("handler", "app/controllers/payments/handler.py") == "payments"


def test_infer_domain_fallback_to_stem():
    assert infer_domain("utils", "utils.py") == "utils"


def test_infer_domain_strips_suffix_on_fallback():
    assert infer_domain("payments_controller", "payments_controller.py") == "payments"


def test_infer_domain_dotted_namespace_skips_tld():
    assert infer_domain("org.example.billing.InvoiceService", "billing/InvoiceService.java") == "billing"


# ── classify_domains ──


def test_classify_domains_groups_correctly():
    nodes = [
        _make_node(
            file_path="src/payments/svc.py",
            qualified_name="payments.svc.PaymentService",
            node_type="class",
        ),
        _make_node(
            file_path="src/payments/api.py",
            qualified_name="payments.api.create",
            node_type="endpoint",
        ),
        _make_node(
            file_path="src/auth/middleware.py",
            qualified_name="auth.middleware.check",
            node_type="function",
        ),
    ]
    domains = classify_domains(nodes)
    assert "payments" in domains
    assert "auth" in domains
    assert len(domains["payments"].nodes) == 2
    assert len(domains["payments"].entry_points) == 1
    assert len(domains["auth"].nodes) == 1
    assert len(domains["auth"].entry_points) == 0


def test_classify_domains_collects_db_models():
    nodes = [
        _make_node(
            file_path="src/orders/Order.py",
            qualified_name="orders.Order",
            node_type="db_model",
        ),
        _make_node(
            file_path="src/orders/api.py",
            qualified_name="orders.api.list",
            node_type="endpoint",
        ),
    ]
    domains = classify_domains(nodes)
    assert len(domains["orders"].db_models) == 1
    assert len(domains["orders"].entry_points) == 1


def test_classify_domains_aggregates_tech_stack():
    nodes = [
        _make_node(
            file_path="src/billing/svc.py",
            qualified_name="billing.svc",
            language="python",
            framework_hints={"frameworks": ["fastapi"]},
        ),
        _make_node(
            file_path="src/billing/model.java",
            qualified_name="billing.model",
            language="java",
            framework_hints={"frameworks": ["spring"]},
        ),
    ]
    domains = classify_domains(nodes)
    ts = domains["billing"].tech_stack
    assert "python" in ts["languages"]
    assert "java" in ts["languages"]
    assert "fastapi" in ts["frameworks"]
    assert "spring" in ts["frameworks"]


def test_classify_domains_sets_domain_on_nodes():
    nodes = [
        _make_node(
            file_path="src/payments/svc.py",
            qualified_name="payments.svc.PaymentService",
            node_type="class",
        ),
    ]
    classify_domains(nodes)
    assert nodes[0].domain == "payments"
